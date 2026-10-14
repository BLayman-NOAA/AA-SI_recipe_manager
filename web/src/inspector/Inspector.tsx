import { Fragment, useId, type ReactNode } from 'react';
import {
  findInclude,
  findStep,
  inputName,
  opLabel,
  type Graph,
  type GraphInclude,
  type StepNode,
} from '../api/graph';
import type { InputField, SessionDocument } from '../api/models';
import { useCurrentSession, useMutationError, useSetInputs } from '../api/queries';
import {
  includeOutputRows,
  includeOverrideRows,
  inputsBoxRows,
  outputsBoxRows,
  rowValue,
  stepOutputRows,
  type InputRow,
  type OutputRow,
} from '../diagram/buildView';
import { includePath, selectStep } from '../diagram/locate';
import { DraftActions, InputControl, InputProblems, useInputDraft } from '../inputs/InputsPane';
import { useView } from '../state/view';
import { useDocsUrl } from '../shell/links';
import { Prose } from '../Prose';
import { plainText, plural, showValue, valueText } from '../text';
import { categoryLabel } from '../theme/categories';
import { opReferenceUrl, paramRows } from './details';

/** Label and value rows. */
function Facts({ rows }: { rows: [string, ReactNode][] }) {
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

function Mono({ children }: { children: ReactNode }) {
  return <span className="rc-mono">{children}</span>;
}

function StepLink({ graph, id }: { graph: Graph; id: string }) {
  return (
    <button type="button" className="rc-link-button rc-mono" onClick={() => selectStep(graph, id)}>
      {id}
    </button>
  );
}

function StepOverview({ step }: { step: StepNode }) {
  const docsUrl = useDocsUrl();
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
          <a href={url} target="_blank" rel="noopener noreferrer">
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

function StepCache({ step }: { step: StepNode }) {
  return (
    <section>
      <h3>Cache</h3>
      <Facts
        rows={[
          ['checkpoint', step.checkpoint ?? 'default'],
          ['regenerate', step.regenerate ?? 'default'],
          ['hash', step.hash ? <Mono>{step.hash}</Mono> : <span className="rc-muted">not computed</span>],
        ]}
      />
      <div className="rc-field-help">Cache status will appear here once recipes can run from the client.</div>
    </section>
  );
}

function StepInspector({ graph, step }: { graph: Graph; step: StepNode }) {
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span className="rc-mono">{step.id}</span>
        <span className="rc-badge">step</span>
      </div>
      <StepOverview step={step} />
      <StepParameters step={step} />
      <StepPorts graph={graph} step={step} />
      <StepCache step={step} />
    </div>
  );
}

interface IncludeInspectorProps {
  graph: Graph;
  include: GraphInclude;
  inputValues: Record<string, unknown>;
}

function IncludeInspector({ graph, include, inputValues }: IncludeInspectorProps) {
  const { drillInto } = useView.getState();
  const parent = include.parent ? findInclude(graph, include.parent) : null;
  const inputs = includeOverrideRows(graph, include.id, inputValues);
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
          ...(parent ? [['included by', parent.title] as [string, ReactNode]] : []),
          ...(include.map_over ? [['map over', <Mono>{include.map_over}</Mono>] as [string, ReactNode]] : []),
        ]}
      />
      <h3>Inputs</h3>
      <p className="rc-field-help">Values the parent sets where it includes this sub-recipe.</p>
      {inputs.length === 0 && <div className="rc-empty">No overrides.</div>}
      {inputs.map((row) => (
        <details key={row.name} className="rc-row-details">
          <RowSummary name={row.name} value={rowValue(row)} />
          <OverrideDetails graph={graph} row={row} />
        </details>
      ))}
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

function InputInspector({ graph, id, fields }: { graph: Graph; id: string; fields: InputField[] }) {
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
  const consumers = graph.edges.filter((edge) => edge.source === id).map((edge) => edge.target);
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
          ['required', node.required ? 'yes' : 'no'],
          ['default', <Mono>{showValue(node.default)}</Mono>],
          ...(field
            ? [
                [
                  'value',
                  <span>
                    <Mono>{showValue(field.value)}</Mono>{' '}
                    <span className={`rc-badge rc-badge-${field.source}`}>{field.source}</span>
                  </span>,
                ] as [string, ReactNode],
              ]
            : []),
        ]}
      />
      <h3>Used by</h3>
      <ul className="rc-list">
        {[...new Set(consumers)].map((step) => (
          <li key={step}>
            <StepLink graph={graph} id={step} />
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Links to steps, comma separated. */
function StepLinks({ graph, ids }: { graph: Graph; ids: string[] }) {
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

function RecipeInputDetails({ graph, row }: { graph: Graph; row: InputRow }) {
  const node = graph.nodes.find((candidate) => candidate.id === row.inputId);
  const input = node?.kind === 'input' ? node : null;
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
          ['default', <Mono>{valueText(input?.default)}</Mono>],
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

/** The declared input a recipe inputs row shows, if any. */
function fieldOf(graph: Graph, row: InputRow, fields: InputField[]): InputField | undefined {
  const node = graph.nodes.find((candidate) => candidate.id === row.inputId);
  const name = node?.kind === 'input' ? inputName(node) : null;
  return fields.find((candidate) => candidate.name === name);
}

function RowSummary({ name, value, source }: { name: string; value: string; source?: string }) {
  return (
    <summary>
      <span className="rc-mono">{name}</span>
      <span className="rc-mono rc-row-value">{value}</span>
      {source && <span className={`rc-badge rc-badge-${source}`}>{source}</span>}
    </summary>
  );
}

/**
 * The recipe inputs box: one expandable row per input holding its details and
 * its control, and one Apply for every edit.
 */
function RecipeInputsInspector({ graph, session }: { graph: Graph; session: SessionDocument }) {
  const setInputs = useSetInputs(session.id);
  const applyError = useMutationError(setInputs);
  const fields = session.inputs.declared;
  const draft = useInputDraft(fields);
  const idPrefix = useId();
  const rows = inputsBoxRows(graph, [], session.inputs.values);
  const shown = rows.map((row) => fieldOf(graph, row, fields));
  const unused = fields.filter((field) => !shown.includes(field));
  const control = (field: InputField) => (
    <InputControl
      field={field}
      edit={draft.edits[field.name]}
      error={draft.result.errors[field.name]}
      onEdit={(edit) => draft.setEdit(field.name, edit)}
      idPrefix={idPrefix}
    />
  );
  const opened = (field: InputField | undefined) => (field && draft.result.errors[field.name] ? true : undefined);
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span>Recipe inputs</span>
        <span className="rc-badge">{plural(rows.length + unused.length, 'input')}</span>
      </div>
      <form onSubmit={draft.submit((values) => setInputs.mutateAsync(values))}>
        <DraftActions draft={draft} applying={setInputs.isPending} applyError={applyError} />
        {rows.length + unused.length === 0 && <div className="rc-empty">No inputs.</div>}
        {rows.map((row, index) => {
          const field = shown[index];
          // An input read both whole and per item has two rows; the first holds its control.
          const first = field !== undefined && shown.indexOf(field) === index;
          return (
            <details key={row.name} className="rc-row-details" open={opened(field)}>
              <RowSummary name={row.name} value={rowValue(row)} source={field?.source} />
              {first ? (
                <>
                  {control(field)}
                  <Facts
                    rows={[
                      ['default', <Mono>{valueText(field.default)}</Mono>],
                      ['used by', <StepLinks graph={graph} ids={row.usedBy} />],
                    ]}
                  />
                </>
              ) : (
                <RecipeInputDetails graph={graph} row={row} />
              )}
            </details>
          );
        })}
        {unused.map((field) => (
          <details key={field.name} className="rc-row-details" open={opened(field)}>
            <RowSummary name={field.name} value={valueText(field.value)} source={field.source} />
            {control(field)}
            <Facts rows={[['used by', <span className="rc-muted">no step</span>]]} />
          </details>
        ))}
        <InputProblems problems={session.validation.problems} declared={fields} />
      </form>
    </div>
  );
}

/** A drilled in include's overrides, one expandable row each. */
interface ParentInputsProps {
  graph: Graph;
  include: GraphInclude;
  session: SessionDocument;
}

function ParentInputsInspector({ graph, include, session }: ParentInputsProps) {
  const rows = inputsBoxRows(graph, includePath(graph, include.id), session.inputs.values);
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span>Inputs from the parent</span>
        <span className="rc-badge">{plural(rows.length, 'input')}</span>
      </div>
      <p className="rc-field-help">
        The overrides the parent recipe sets where it includes {include.title} (<Mono>{include.source}</Mono>).
      </p>
      {rows.length === 0 && <div className="rc-empty">No inputs.</div>}
      {rows.map((row) => (
        <details key={row.name} className="rc-row-details">
          <RowSummary name={row.name} value={rowValue(row)} />
          <OverrideDetails graph={graph} row={row} />
        </details>
      ))}
    </div>
  );
}

/** The recipe inputs box, or a drilled in include's overrides. */
function InputsBoxInspector({ graph, id, session }: { graph: Graph; id: string; session: SessionDocument }) {
  if (!id.startsWith('overrides:')) return <RecipeInputsInspector graph={graph} session={session} />;
  const include = findInclude(graph, id.slice('overrides:'.length));
  return include ? <ParentInputsInspector graph={graph} include={include} session={session} /> : <Missing />;
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

/** Details of the selected step, sub-recipe, inputs box or outputs box. */
export function Inspector() {
  const data = useCurrentSession().data;
  const selected = useView((state) => state.selected);
  const drill = useView((state) => state.drill);

  if (!data) {
    return <div className="rc-placeholder">Open a recipe from the Recipes pane.</div>;
  }
  if (!selected) {
    return (
      <div className="rc-placeholder">
        Select a step, sub-recipe, inputs or outputs box in the diagram to inspect it.
      </div>
    );
  }
  const graph = data.graph;
  switch (selected.kind) {
    case 'step': {
      const step = findStep(graph, selected.id);
      return step ? <StepInspector graph={graph} step={step} /> : <Missing />;
    }
    case 'include': {
      const include = findInclude(graph, selected.id);
      if (!include) return <Missing />;
      return <IncludeInspector graph={graph} include={include} inputValues={data.inputs.values} />;
    }
    case 'input':
      return <InputInspector graph={graph} id={selected.id} fields={data.inputs.declared} />;
    case 'inputsBox':
      return <InputsBoxInspector graph={graph} id={selected.id} session={data} />;
    case 'outputsBox':
      return <OutputsBoxInspector graph={graph} owner={selected.id} drill={drill} />;
  }
}
