/**
 * The inspector's sections for a selection: steps, sub-recipes, inputs and
 * outputs boxes. Nothing here reads the session, so the docs site shows the
 * same sections the application does.
 */
import { Fragment, type ReactNode } from 'react';
import { fieldSources, sourceLabel } from '../api/fields';
import {
  declaredIn,
  findInclude,
  findStep,
  includeOf,
  inputName,
  opLabel,
  type Graph,
  type GraphInclude,
  type StepNode,
} from '../api/graph';
import type { InputField, InputSource } from '../api/models';
import {
  includeInputRows,
  includeOutputRows,
  inputsBoxMore,
  inputsBoxRows,
  outputsBoxRows,
  readers,
  recipeInputRows,
  rowValue,
  splitRows,
  stepOutputRows,
  type InputRow,
  type OutputRow,
} from '../diagram/buildView';
import { includePath, selectStep } from '../diagram/locate';
import { useDocsEnv, type DocsEnv } from '../env';
import { Prose } from '../Prose';
import { useViewStore, type Selection } from '../state/view';
import { plainText, plural, showValue, valueText } from '../text';
import { categoryLabel } from '../theme/categories';
import { ByFile } from './ByFile';
import { opReferenceUrl, paramRows } from './details';

/** Link attributes for a docs link: a new tab in the app, the same tab on the site. */
function linkProps(target: DocsEnv['linkTarget']) {
  return target === '_blank' ? { target: '_blank', rel: 'noopener noreferrer' } : {};
}

/** Label and value rows. */
export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <table className="rc-table rc-facts">
      <tbody>
        {rows.map(([label, value]) => (
          <tr key={label}>
            <th scope="row">{label}</th>
            <td>{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function Mono({ children }: { children: ReactNode }) {
  return <span className="rc-mono">{children}</span>;
}

function StepLink({ graph, id }: { graph: Graph; id: string }) {
  const store = useViewStore();
  return (
    <button type="button" className="rc-link-button rc-mono" onClick={() => selectStep(store, graph, id)}>
      {id}
    </button>
  );
}

function StepOverview({ step }: { step: StepNode }) {
  const { docsUrl, linkTarget } = useDocsEnv();
  const url = opReferenceUrl(step, docsUrl);
  const implementation = step.implementation;
  const rows: [string, ReactNode][] = [
    ['id', <Mono>{step.id}</Mono>],
    ['op', <Mono>{opLabel(step)}</Mono>],
    ['category', step.custom ? 'custom' : categoryLabel(step.category)],
  ];
  if (implementation) {
    const which = `${implementation.key} implementation`;
    rows.push([
      'implementation',
      <span>
        <Mono>{implementation.callable_path}</Mono> <span className="rc-muted">({which})</span>
      </span>,
    ]);
    rows.push(['package', implementation.dependency ?? 'none']);
  } else if (step.callable_path) {
    rows.push(['callable', <Mono>{step.callable_path}</Mono>]);
  }
  rows.push(['defined in', <Mono>{step.document}</Mono>]);
  return (
    <section>
      <h3>Overview</h3>
      {step.description && (
        <p className="rc-description">
          <Prose text={step.description} />
        </p>
      )}
      <Facts rows={rows} />
      {url && (
        <p>
          <a href={url} {...linkProps(linkTarget)}>
            Op reference for {step.extends ?? step.op}
          </a>
        </p>
      )}
    </section>
  );
}

function StepParameters({ step }: { step: StepNode }) {
  const rows = paramRows(step);
  return (
    <section>
      <h3>Parameters</h3>
      {rows.length === 0 ? (
        <div className="rc-empty">No parameters.</div>
      ) : (
        <table className="rc-table">
          <thead>
            <tr>
              <th>name</th>
              <th>value</th>
              <th>type</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.name} title={plainText(row.description) || undefined}>
                <td className="rc-mono">{row.name}</td>
                <td className="rc-mono rc-wrap">{row.value || <span className="rc-muted">none</span>}</td>
                <td className="rc-muted">{row.type}</td>
                <td>
                  <span className={`rc-badge${row.set ? ' rc-badge-session' : ''}`}>
                    {row.set ? 'set' : 'default'}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {step.sweep && (
        <>
          <h3>Sweep ({step.sweep.mode})</h3>
          <Facts
            rows={Object.entries(step.sweep.params).map(([name, values]) => [
              name,
              <Mono>{showValue(values)}</Mono>,
            ])}
          />
        </>
      )}
    </section>
  );
}

/** Where an output goes: the steps that read it, the recipe outputs block, or nowhere. */
function OutputWhere({ graph, by, final }: { graph: Graph; by: string[]; final: boolean }) {
  if (by.length === 0 && !final) return <span className="rc-muted">not used</span>;
  return (
    <span>
      {by.length > 0 && (
        <>
          used by <StepLinks graph={graph} ids={by} />
        </>
      )}
      {by.length > 0 && final && ', '}
      {final && 'recipe output'}
    </span>
  );
}

function StepPorts({ graph, step }: { graph: Graph; step: StepNode }) {
  const where = new Map(stepOutputRows(graph, step.id).map((status) => [status.name, status]));
  const noStatus = { by: [] as string[], final: false };
  return (
    <section>
      <h3>Inputs</h3>
      {step.inputs.length === 0 ? (
        <div className="rc-empty">No inputs.</div>
      ) : (
        <table className="rc-table">
          <tbody>
            {step.inputs.map((port) => (
              <tr key={port.name} title={plainText(port.description) || undefined}>
                <td className="rc-mono">{port.name}</td>
                <td className="rc-mono rc-wrap">
                  {showValue(port.value) || <span className="rc-muted">not wired</span>}
                </td>
                <td className="rc-muted">
                  {port.type}
                  {port.many ? ' (many)' : ''}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <h3>Outputs</h3>
      {step.outputs.length === 0 ? (
        <div className="rc-empty">No outputs.</div>
      ) : (
        <table className="rc-table">
          <tbody>
            {step.outputs.map((port) => (
              <tr key={port.name} title={plainText(port.description) || undefined}>
                <td className="rc-mono">{port.name}</td>
                <td className="rc-muted">{port.type}</td>
                <td>
                  <OutputWhere graph={graph} {...(where.get(port.name) ?? noStatus)} />
                </td>
                <td>{port.disposed && <span className="rc-badge">disposed</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}

interface StepInspectorProps {
  graph: Graph;
  step: StepNode;
  extras?: (step: StepNode) => ReactNode;
}

function StepInspector({ graph, step, extras }: StepInspectorProps) {
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span className="rc-mono">{step.id}</span>
        <span className="rc-badge">step</span>
      </div>
      <StepOverview step={step} />
      <StepParameters step={step} />
      <StepPorts graph={graph} step={step} />
      {extras?.(step)}
    </div>
  );
}

interface IncludeInputsProps {
  graph: Graph;
  include: GraphInclude;
  inputValues: Record<string, unknown>;
  inputSources?: Record<string, InputSource>;
}

/** The overrides an include entry sets, one expandable row each. */
function OverridesSection({ graph, rows }: { graph: Graph; rows: InputRow[] }) {
  return (
    <>
      <h3>Overrides</h3>
      <p className="rc-field-help">Values the parent sets where it includes this sub-recipe.</p>
      {rows.length === 0 && <div className="rc-empty">No overrides.</div>}
      {rows.map((row) => (
        <details key={row.name} className="rc-row-details">
          <RowSummary name={row.name} value={rowValue(row)} />
          <OverrideDetails graph={graph} row={row} />
        </details>
      ))}
    </>
  );
}

/** The inputs a sub-recipe file declares and its entry leaves alone, one expandable row each. */
function OwnInputsSection({ graph, include, rows }: { graph: Graph; include: GraphInclude; rows: InputRow[] }) {
  return (
    <>
      <h3>Inputs</h3>
      <p className="rc-field-help">
        What <Mono>{include.source}</Mono> declares; each shows the value a run would use.
      </p>
      {rows.length === 0 && <div className="rc-empty">No inputs of its own.</div>}
      {rows.map((row) => (
        <details key={row.name} className="rc-row-details">
          <RowSummary name={row.name} value={rowValue(row)} source={rowBadge(row)} />
          <InputRowDetails graph={graph} row={row} />
        </details>
      ))}
    </>
  );
}

/** "9 declared, 4 overridden" for a sub-recipe file, or null for an older graph. */
function declaredText(include: GraphInclude): string | null {
  if (!include.inputs) return null;
  const overridden = include.inputs.filter((input) => input.name in include.overrides).length;
  return `${include.inputs.length} declared, ${overridden} overridden`;
}

function IncludeInspector({ graph, include, inputValues, inputSources }: IncludeInputsProps) {
  const { drillInto } = useViewStore().getState();
  const { docsUrl, linkTarget, pages } = useDocsEnv();
  const page = pages[include.id];
  const parent = include.parent ? findInclude(graph, include.parent) : null;
  const inputs = includeInputRows(graph, include.id, inputValues, inputSources);
  const declared = declaredText(include);
  const outputs = includeOutputRows(graph, include.id);
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span>{include.title}</span>
        <span className="rc-badge">sub-recipe</span>
      </div>
      <div className="rc-pane-toolbar">
        <button type="button" className="rc-button rc-small" onClick={() => drillInto(include.id)}>
          Drill in
        </button>
      </div>
      {include.description && (
        <p className="rc-description">
          <Prose text={include.description} />
        </p>
      )}
      <Facts
        rows={[
          ['source', <Mono>{include.source}</Mono>],
          ['steps', String(include.steps.length)],
          ...(declared ? [['inputs', declared] as [string, ReactNode]] : []),
          ...(parent ? [['included by', parent.title] as [string, ReactNode]] : []),
          ...(include.map_over ? [['map over', <Mono>{include.map_over}</Mono>] as [string, ReactNode]] : []),
          ...(page
            ? [
                [
                  'documented at',
                  <a href={`${docsUrl}${page.url}`} {...linkProps(linkTarget)}>
                    {page.title}
                  </a>,
                ] as [string, ReactNode],
              ]
            : []),
        ]}
      />
      <OverridesSection graph={graph} rows={inputs.overrides} />
      <OwnInputsSection graph={graph} include={include} rows={inputs.own} />
      <h3>Outputs</h3>
      {outputs.rows.length === 0 ? (
        <div className="rc-empty">No output is used outside the sub-recipe.</div>
      ) : (
        <table className="rc-table">
          <tbody>
            {[...new Set(outputs.rows.map((row) => row.step))].map((stepId) => (
              <Fragment key={stepId}>
                <tr>
                  <th colSpan={2}>
                    <StepLink graph={graph} id={stepId} />
                  </th>
                </tr>
                {outputs.rows
                  .filter((row) => row.step === stepId)
                  .map((row) => (
                    <tr key={row.name}>
                      <td className="rc-mono">{row.name}</td>
                      <td>
                        <OutputWhere graph={graph} by={row.by} final={row.final} />
                      </td>
                    </tr>
                  ))}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
      {outputs.inside > 0 && (
        <p className="rc-field-help">
          {plural(outputs.inside, 'output')} {outputs.inside === 1 ? 'is' : 'are'} used inside the sub-recipe.
        </p>
      )}
      <h3>Steps</h3>
      <ul className="rc-list">
        {include.steps.map((id) => (
          <li key={id}>
            <StepLink graph={graph} id={id} />
          </li>
        ))}
      </ul>
    </div>
  );
}

function InputInspector({ graph, id, fields = [] }: { graph: Graph; id: string; fields?: InputField[] }) {
  const node = graph.nodes.find((candidate) => candidate.id === id);
  if (!node || (node.kind !== 'input' && node.kind !== 'external')) return <Missing />;
  if (node.kind === 'external') {
    return (
      <div className="rc-pane rc-inspector">
        <div className="rc-inspector-title">
          <span className="rc-mono">{node.label}</span>
          <span className="rc-badge">external</span>
        </div>
        <Facts rows={[['type', node.type ?? '']]} />
        <div className="rc-field-help">Output of a step in another file.</div>
      </div>
    );
  }
  const name = inputName(node);
  const field = fields.find((candidate) => candidate.name === name);
  const consumers = readers(graph, id);
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span className="rc-mono">{name}</span>
        <span className="rc-badge">recipe input</span>
      </div>
      {node.description && (
        <p className="rc-description">
          <Prose text={node.description} />
        </p>
      )}
      <Facts
        rows={[
          ['type', node.type ?? ''],
          ['declared', node.declared ? 'yes' : 'no'],
          ...(node.declared ? [['declared in', <Mono>{declaredIn(graph, node)}</Mono>] as [string, ReactNode]] : []),
          ['required', node.required ? 'yes' : 'no'],
          ['default', <Mono>{showValue(node.default)}</Mono>],
          ...(field
            ? [
                [
                  'value',
                  <span>
                    <Mono>{showValue(field.value)}</Mono>{' '}
                    <span className={`rc-badge rc-badge-${field.source}`}>{sourceLabel(field.source)}</span>
                  </span>,
                ] as [string, ReactNode],
              ]
            : []),
        ]}
      />
      <h3>Used by</h3>
      <ul className="rc-list">
        {consumers.map((step) => (
          <li key={step}>
            <StepLink graph={graph} id={step} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Links to steps, comma separated. */
export function StepLinks({ graph, ids }: { graph: Graph; ids: string[] }) {
  if (ids.length === 0) return <span className="rc-muted">none</span>;
  return (
    <span>
      {ids.map((id, index) => (
        <span key={id}>
          {index > 0 && ', '}
          <StepLink graph={graph} id={id} />
        </span>
      ))}
    </span>
  );
}

/** The default an input row's declaring file gives it, else the recipe input's. */
function declaredDefault(graph: Graph, row: InputRow): unknown {
  const include = row.declaredIn ? includeOf(graph, row.declaredIn) : undefined;
  const declaration = include?.inputs?.find((input) => input.name === row.name);
  if (declaration) return declaration.default;
  const node = graph.nodes.find((candidate) => candidate.id === row.inputId);
  return node?.kind === 'input' ? node.default : undefined;
}

/** The facts naming the declaring file of an input row, if it says. */
export function declaredInFact(row: InputRow): [string, ReactNode][] {
  return row.declaredIn ? [['declared in', <Mono>{row.declaredIn}</Mono>]] : [];
}

/** A recipe input's details: description, type, default, declaring file and readers. */
export function RecipeInputDetails({ graph, row }: { graph: Graph; row: InputRow }) {
  return (
    <>
      {row.description && (
        <p className="rc-description">
          <Prose text={row.description} />
        </p>
      )}
      <Facts
        rows={[
          ['type', row.type ?? ''],
          ['default', <Mono>{valueText(declaredDefault(graph, row))}</Mono>],
          ...declaredInFact(row),
          ['used by', <StepLinks graph={graph} ids={row.usedBy} />],
        ]}
      />
    </>
  );
}

/** The badge of a sub-recipe input row: the run source, else what gives its value. */
function rowBadge(row: InputRow): string {
  if (row.setBy === 'run') return row.runSource ?? 'set';
  return row.setBy;
}

/** Where a sub-recipe input row's value comes from, in words. */
function setByText(row: InputRow): string {
  switch (row.setBy) {
    case 'run':
      if (row.runSource === 'config') return 'from the run config';
      return row.runSource === 'session' ? 'from the session' : 'set for the run';
    case 'parent':
      return 'set by an include above';
    case 'unset':
      return 'required, not set';
    default:
      return 'the default';
  }
}

/** A sub-recipe input's details: the file's type and default, the value and who sets it. */
function InputRowDetails({ graph, row }: { graph: Graph; row: InputRow }) {
  return (
    <>
      {row.description && (
        <p className="rc-description">
          <Prose text={row.description} />
        </p>
      )}
      <Facts
        rows={[
          ['type', row.type ?? ''],
          ['default', <Mono>{valueText(declaredDefault(graph, row))}</Mono>],
          [
            'value',
            <span>
              <Mono>{rowValue(row)}</Mono> <span className="rc-muted">({setByText(row)})</span>
            </span>,
          ],
          ...declaredInFact(row),
          ['used by', <StepLinks graph={graph} ids={row.usedBy} />],
        ]}
      />
    </>
  );
}

function OverrideDetails({ graph, row }: { graph: Graph; row: InputRow }) {
  return (
    <Facts
      rows={[
        ['value', <Mono>{rowValue(row)}</Mono>],
        ...(row.from.length > 0
          ? [['from', <StepLinks graph={graph} ids={row.from.map((ref) => ref.step)} />] as [string, ReactNode]]
          : []),
        ['used by', <StepLinks graph={graph} ids={row.usedBy} />],
      ]}
    />
  );
}

export function RowSummary({ name, value, source }: { name: string; value: string; source?: string }) {
  return (
    <summary>
      <span className="rc-mono">{name}</span>
      <span className="rc-mono rc-row-value">{value}</span>
      {source && <span className={`rc-badge rc-badge-${source}`}>{sourceLabel(source)}</span>}
    </summary>
  );
}

interface RecipeInputsListProps {
  graph: Graph;
  inputValues: Record<string, unknown>;
  inputSources?: Record<string, InputSource>;
}

/** Every recipe input, read only and grouped by declaring file: one expandable row each. */
function RecipeInputsList({ graph, inputValues, inputSources }: RecipeInputsListProps) {
  const rows = recipeInputRows(graph, inputValues, inputSources);
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span>Recipe inputs</span>
        <span className="rc-badge">{plural(rows.length, 'input')}</span>
      </div>
      {rows.length === 0 && <div className="rc-empty">No inputs.</div>}
      <ByFile graph={graph} items={rows} fileOf={(row) => row.declaredIn}>
        {(row) => (
          <details key={row.name} className="rc-row-details">
            <RowSummary name={row.name} value={rowValue(row)} />
            <RecipeInputDetails graph={graph} row={row} />
          </details>
        )}
      </ByFile>
    </div>
  );
}

/** A drilled in sub-recipe's inputs box: the overrides its entry sets, then the inputs it declares. */
function IncludeInputsInspector({ graph, include, inputValues, inputSources }: IncludeInputsProps) {
  const drill = includePath(graph, include.id);
  const { overrides, inputs } = splitRows(inputsBoxRows(graph, drill, inputValues, inputSources));
  const more = inputsBoxMore(graph, drill);
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span>Inputs</span>
        <span className="rc-badge">sub-recipe</span>
      </div>
      <p className="rc-field-help">
        Inputs of {include.title} (<Mono>{include.source}</Mono>): the overrides the parent sets, then the inputs
        the file declares.
      </p>
      <OverridesSection graph={graph} rows={overrides} />
      <OwnInputsSection graph={graph} include={include} rows={inputs} />
      {more > 0 && (
        <p className="rc-field-help">{plural(more, 'more input')} declared in sub-recipes below this one.</p>
      )}
    </div>
  );
}

interface InputsBoxInspectorProps {
  graph: Graph;
  id: string;
  inputValues: Record<string, unknown>;
  inputSources?: Record<string, InputSource>;
  recipeInputs?: ReactNode;
}

/** The recipe inputs box, or a drilled in sub-recipe's inputs box. */
function InputsBoxInspector({ graph, id, inputValues, inputSources, recipeInputs }: InputsBoxInspectorProps) {
  if (!id.startsWith('overrides:')) {
    return recipeInputs ?? <RecipeInputsList graph={graph} inputValues={inputValues} inputSources={inputSources} />;
  }
  const include = findInclude(graph, id.slice('overrides:'.length));
  if (!include) return <Missing />;
  return (
    <IncludeInputsInspector graph={graph} include={include} inputValues={inputValues} inputSources={inputSources} />
  );
}

function outputWhere(graph: Graph, row: OutputRow): ReactNode {
  if (row.kind === 'exit') return <OutputWhere graph={graph} by={row.by} final={false} />;
  return <OutputWhere graph={graph} by={[]} final={row.kind === 'final'} />;
}

/** One producer's outputs box: each output and where it goes. */
function OutputsBoxInspector({ graph, owner, drill }: { graph: Graph; owner: string; drill: string[] }) {
  const rows = outputsBoxRows(graph, drill, owner);
  const include = findInclude(graph, owner);
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span>Outputs</span>
        <span className="rc-badge">{plural(rows.length, 'output')}</span>
      </div>
      <Facts rows={[['from', include ? include.title : <StepLink graph={graph} id={owner} />]]} />
      {rows.length === 0 && <div className="rc-empty">No outputs in this view.</div>}
      {rows.length > 0 && rows.every((row) => row.kind === 'unused') && (
        <p className="rc-field-help">No output of this step is used later.</p>
      )}
      {rows.map((row) => (
        <details key={`${row.kind}:${row.name}`} className="rc-row-details">
          <summary>
            <span className="rc-mono">{row.name}</span>
            {row.type && <span className="rc-muted">{row.type}</span>}
          </summary>
          <Facts
            rows={[
              ['type', row.type ?? ''],
              ['where', outputWhere(graph, row)],
            ]}
          />
        </details>
      ))}
    </div>
  );
}

function Missing() {
  return <div className="rc-placeholder">The selected node is not in this recipe.</div>;
}

export interface InspectorBodyProps {
  graph: Graph;
  selected: Selection | null;
  drill: string[];
  inputValues?: Record<string, unknown>;
  /** Declared recipe inputs with their current values (the session's); absent shows defaults only. */
  declared?: InputField[];
  /** Where each recipe input's value comes from, by name; derived from `declared` when absent. */
  inputSources?: Record<string, InputSource>;
  /** Sections after Inputs and outputs for a step (the app's Cache section). */
  stepExtras?: (step: StepNode) => ReactNode;
  /** Replaces the recipe inputs list (the app's Apply form). */
  recipeInputs?: ReactNode;
}

/** Details of the selected step, sub-recipe, inputs box or outputs box. */
export function InspectorBody({
  graph,
  selected,
  drill,
  inputValues = {},
  declared,
  inputSources,
  stepExtras,
  recipeInputs,
}: InspectorBodyProps) {
  if (!selected) {
    return (
      <div className="rc-placeholder">
        Select a step, sub-recipe, inputs or outputs box in the diagram to inspect it.
      </div>
    );
  }
  const sources = inputSources ?? (declared ? fieldSources(declared) : undefined);
  switch (selected.kind) {
    case 'step': {
      const step = findStep(graph, selected.id);
      return step ? <StepInspector graph={graph} step={step} extras={stepExtras} /> : <Missing />;
    }
    case 'include': {
      const include = findInclude(graph, selected.id);
      if (!include) return <Missing />;
      return <IncludeInspector graph={graph} include={include} inputValues={inputValues} inputSources={sources} />;
    }
    case 'input':
      return <InputInspector graph={graph} id={selected.id} fields={declared} />;
    case 'inputsBox':
      return (
        <InputsBoxInspector
          graph={graph}
          id={selected.id}
          inputValues={inputValues}
          inputSources={sources}
          recipeInputs={recipeInputs}
        />
      );
    case 'outputsBox':
      return <OutputsBoxInspector graph={graph} owner={selected.id} drill={drill} />;
  }
}
