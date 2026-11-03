/** The open session's inspector: InspectorBody with the Cache section and the edit forms. */
import { useId, useState, type FormEvent, type ReactNode } from 'react';
import { fieldSources } from '../api/fields';
import { echogramCandidate, rootDocument, type Graph, type GraphInclude, type StepNode } from '../api/graph';
import { fileEditable, type InputField, type SessionDocument, type StepPreview } from '../api/models';
import {
  useCurrentSession,
  useEdit,
  useMutationError,
  useSetInputs,
  useStepArtifacts,
} from '../api/queries';
import { recipeInputRows, rowValue, type InputRow } from '../diagram/buildView';
import { checkpointLook } from '../diagram/nodes/StepNode';
import { showEchogram } from '../echogram/show';
import { useSetCheckpoint } from '../editing/useSetCheckpoint';
import {
  DraftActions,
  InputControl,
  InputProblems,
  UnappliedBadge,
  useInputDraft,
  ValueInput,
} from '../inputs/InputsPane';
import { isUnapplied, toDraft, type FieldEdit } from '../inputs/inputForm';
import { staleResult, statusText, surveyBackedSteps } from '../runs/badges';
import type { StepRunState } from '../runs/reducer';
import { openStepLog, useShownRun } from '../runs/store';
import { useCurrentPreview } from '../runs/usePreviewIntent';
import { showPane } from '../shell/dock';
import { useSessionStore } from '../state/session';
import { useViewStore } from '../state/view';
import { plural, secondsText, valueText } from '../text';
import { includerFile } from './bindings';
import { ByFile } from './ByFile';
import { InputDefaultForm } from './InputDefaultForm';
import {
  declaredInFact,
  Facts,
  InspectorBody,
  Mono,
  OverridesSection,
  OwnInputsSection,
  RecipeInputDetails,
  RowSummary,
  StepLinks,
  type IncludeSectionRows,
} from './InspectorBody';
import { OverrideForm } from './OverrideForm';
import { RunConfigInspector } from './RunConfigInspector';
import { EditField, FieldButton, useEditError, useEdits } from './formParts';
import { StepParamForm } from './StepParamForm';
import { controlForValue, defaultOperations, parseValue, valueType, type DefaultField } from './paramForm';

/** The tiers holding a step: "user, survey", with instance counts for a fanned out step. */
function heldText(ahead: StepPreview): string {
  if (ahead.held_by.length === 0 && Object.keys(ahead.instances_held).length === 0) return 'none';
  if (!ahead.fanned_out) return ahead.held_by.join(', ');
  const of = ahead.instances === null ? '' : `/${ahead.instances}`;
  return Object.entries(ahead.instances_held)
    .map(([tier, count]) => `${tier} ${count}${of}`)
    .join(', ');
}

/** What the preview says becomes of a step's result in this run. */
const CHECKPOINT_TEXT: Record<string, string> = {
  'memory-only': 'kept in memory',
  'will-save': 'will be saved',
  saved: 'saved',
};

/** What becomes of a step's result in this run: "will be saved, pinned as a target". */
function thisRunText(ahead: StepPreview): string {
  const text = CHECKPOINT_TEXT[ahead.checkpoint] ?? ahead.checkpoint;
  return ahead.pinned ? `${text}, pinned as a target` : text;
}

/** The last actual outcome: "computed in 1.2 s (user)". */
function actualText(actual: StepRunState): string {
  const parts: string[] = [statusText(actual.status)];
  if (actual.seconds !== null && actual.status !== 'running') parts.push(`in ${secondsText(actual.seconds)}`);
  const loaded = actual.status === 'hit-user-cache' || actual.status === 'hit-survey-cache';
  if (actual.tier && !loaded) parts.push(`(${actual.tier})`);
  return parts.join(' ');
}

/** Where a step's saved outputs are, fetched when the list is opened. */
function SavedFiles({ step }: { step: StepNode }) {
  const sid = useSessionStore((state) => state.sid);
  const [open, setOpen] = useState(false);
  const found = useStepArtifacts(sid, step.id, open);
  const data = found.data;
  let body: ReactNode;
  if (found.error) body = <p className="rc-field-error">{found.error.message}</p>;
  else if (!data) body = <p className="rc-muted">Looking...</p>;
  else if (data.artifacts.length === 0) {
    body = <p className="rc-muted">{data.note ?? 'Nothing is saved for the current hash.'}</p>;
  } else {
    body = (
      <table className="rc-table rc-artifacts">
        <tbody>
          {data.artifacts.map((artifact) => (
            <tr key={`${artifact.step_hash}/${artifact.output}`}>
              <td className="rc-mono">
                {artifact.output}
                {artifact.instance_index !== null ? ` #${artifact.instance_index}` : ''}
              </td>
              <td>{artifact.format}</td>
              <td>{artifact.tier}</td>
              <td className="rc-mono rc-artifact-url" title={artifact.url}>
                {artifact.url}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  return (
    <details className="rc-saved-files" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Saved files</summary>
      {open && body}
    </details>
  );
}

/** The Cache section's twin of the node's save icon: sets or removes `checkpoint: always`. */
function CheckpointToggle({ step }: { step: StepNode }) {
  const set = useSetCheckpoint(step.id);
  const checkpointed = step.checkpoint === 'always';
  return (
    <button
      type="button"
      className="rc-button rc-small"
      title={checkpointLook(step.checkpoint).title}
      onClick={() => void set(!checkpointed)}
    >
      {checkpointed ? 'Remove the checkpoint' : 'Make this a checkpoint'}
    </button>
  );
}

function StepCache({ step }: { step: StepNode }) {
  const sid = useSessionStore((state) => state.sid);
  const session = useCurrentSession().data;
  const preview = useCurrentPreview().data;
  const run = useShownRun();
  const ahead = preview?.steps.find((candidate) => candidate.step_id === step.id);
  const actual = run?.steps[step.id];
  const hash = ahead?.step_hash ?? step.hash;
  const error = actual?.status === 'failed' ? actual.error : null;
  const rows: [string, ReactNode][] = [
    ['hash', hash ? <Mono>{hash}</Mono> : <span className="rc-muted">not computed</span>],
  ];
  if (ahead) {
    rows.push(['held by', heldText(ahead)]);
    const planned = statusText(ahead.planned);
    rows.push(['predicted', ahead.note ? `${planned} (${ahead.note})` : planned]);
  }
  rows.push(['last run', actual ? actualText(actual) : <span className="rc-muted">not run</span>]);
  rows.push(['checkpoint', step.checkpoint ?? 'default']);
  if (ahead) rows.push(['this run', thisRunText(ahead)]);
  rows.push(['regenerate', step.regenerate ?? 'default']);
  return (
    <section>
      <h3>Cache</h3>
      <Facts rows={rows} />
      {sid && session && fileEditable(session, step.document) && <CheckpointToggle step={step} />}
      {sid && echogramCandidate(step) && (
        <button type="button" className="rc-button rc-small" onClick={() => showEchogram(sid, step)}>
          Show in echogram
        </button>
      )}
      <SavedFiles step={step} />
      {staleResult(ahead, actual) && (
        <p className="rc-field-help rc-status-warning">The hash changed since the last run.</p>
      )}
      {error && (
        <div className="rc-step-error">
          <div className="rc-field-error">
            {error.error_type ?? 'Error'}: {error.message}
          </div>
          <button type="button" className="rc-button rc-small" onClick={() => openStepLog(step.id)}>
            Open log
          </button>
        </div>
      )}
    </section>
  );
}

/**
 * The recipe inputs box: one expandable row per input, grouped by the file
 * that declares it, holding its details and its control, and one Apply for
 * every edit.
 */
/** A recipe input's default, edited in the file that declares it. */
function defaultField(graph: Graph, row: InputRow, field: InputField): DefaultField {
  return { name: row.name, type: field.type, default: field.default, document: row.declaredIn ?? rootDocument(graph) };
}

interface DefaultControlProps {
  field: DefaultField;
  edit: FieldEdit | undefined;
  error: string | undefined;
  onEdit: (edit: FieldEdit | undefined) => void;
  idPrefix: string;
}

/** A link that opens a control for an input's default in its file, then the control. */
function DefaultControl({ field, edit, error, onEdit, idPrefix }: DefaultControlProps) {
  const type = valueType(field.type, field.default);
  const kind = controlForValue(type, field.default);
  const id = `${idPrefix}rc-default-${field.name}`;
  if (!edit || !('draft' in edit)) {
    return (
      <button
        type="button"
        className="rc-link-button"
        onClick={() => onEdit({ draft: toDraft(field.default, kind) })}
      >
        Change the default in {field.document}
      </button>
    );
  }
  return (
    <EditField
      id={id}
      name={`default in ${field.document}`}
      badges={isUnapplied(edit, field.default, (draft) => parseValue(draft, type)) && <UnappliedBadge />}
      action={<FieldButton label="Cancel" onClick={() => onEdit(undefined)} />}
      error={error}
      notes={<div className="rc-field-help">Changes the recipe file; the value above is this session's.</div>}
    >
      <ValueInput
        id={id}
        kind={kind}
        draft={edit.draft}
        invalid={Boolean(error)}
        integer={type === 'int'}
        onChange={(draft) => onEdit({ draft })}
      />
    </EditField>
  );
}

function RecipeInputsInspector({ graph, session }: { graph: Graph; session: SessionDocument }) {
  const setInputs = useSetInputs(session.id);
  const edit = useEdit(session.id);
  const applyError = useMutationError(setInputs);
  const editError = useEditError(edit);
  const fields = session.inputs.declared;
  const draft = useInputDraft(fields);
  const defaults = useEdits<FieldEdit>();
  const idPrefix = useId();
  const rows = recipeInputRows(graph, session.inputs.values, fieldSources(fields));
  const byName = new Map(fields.map((field) => [field.name, field]));
  const defaultFields = new Map(
    rows.flatMap((row) => {
      const field = byName.get(row.name);
      return field ? [[row.name, defaultField(graph, row, field)] as const] : [];
    }),
  );
  const defaultPlan = defaultOperations([...defaultFields.values()], defaults.edits);
  // Rows and fields both come from the declared inputs, so this is empty unless the two disagree.
  const unused = fields.filter((field) => !rows.some((row) => row.name === field.name));
  const invalid = draft.invalid || Object.keys(defaultPlan.errors).length > 0;
  const changed = draft.result.changed || defaultPlan.operations.length > 0;
  const control = (field: InputField) => (
    <InputControl
      field={field}
      edit={draft.edits[field.name]}
      error={draft.result.errors[field.name]}
      onEdit={(next) => draft.setEdit(field.name, next)}
      idPrefix={idPrefix}
    />
  );
  const defaultControl = (name: string) => {
    const field = defaultFields.get(name);
    if (!field || !fileEditable(session, field.document)) return null;
    return (
      <DefaultControl
        field={field}
        edit={defaults.edits[name]}
        error={defaultPlan.errors[name]}
        onEdit={(next) => defaults.setEdit(name, next)}
        idPrefix={idPrefix}
      />
    );
  };
  const opened = (field: InputField | undefined) =>
    field && (draft.result.errors[field.name] || defaultPlan.errors[field.name]) ? true : undefined;
  // The defaults go first as an edit, then the session's values; a failure keeps the rest of the form.
  const apply = async () => {
    try {
      if (defaultPlan.operations.length > 0) {
        await edit.mutateAsync(defaultPlan.operations);
        defaults.discard();
      }
      if (draft.result.changed) {
        await setInputs.mutateAsync(draft.result.values);
        draft.discard();
      }
    } catch {
      // Shown by the form's apply error.
    }
  };
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!invalid && changed) void apply();
  };
  const discard = () => {
    draft.discard();
    defaults.discard();
  };
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span>Recipe inputs</span>
        <span className="rc-badge">{plural(rows.length + unused.length, 'input')}</span>
      </div>
      <form onSubmit={submit}>
        <DraftActions
          invalid={invalid}
          changed={changed}
          count={Object.keys({ ...draft.edits, ...defaults.edits }).length}
          discard={discard}
          applying={setInputs.isPending || edit.isPending}
          applyError={editError ?? applyError}
        />
        {rows.length + unused.length === 0 && <div className="rc-empty">No inputs.</div>}
        <ByFile graph={graph} items={rows} fileOf={(row) => row.declaredIn}>
          {(row) => {
            const field = byName.get(row.name);
            return (
              <details key={row.name} className="rc-row-details" open={opened(field)}>
                <RowSummary name={row.name} value={rowValue(row)} source={field?.source} />
                {field ? (
                  <>
                    {control(field)}
                    {defaultControl(row.name)}
                    <Facts
                      rows={[
                        ['default', <Mono>{valueText(field.default)}</Mono>],
                        ...declaredInFact(row),
                        ['used by', <StepLinks graph={graph} ids={row.usedBy} />],
                      ]}
                    />
                  </>
                ) : (
                  <RecipeInputDetails graph={graph} row={row} />
                )}
              </details>
            );
          }}
        </ByFile>
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

interface IncludeSectionsProps {
  graph: Graph;
  include: GraphInclude;
  rows: IncludeSectionRows;
  session: SessionDocument;
}

/** A sub-recipe's Overrides and Inputs sections, each with controls when the session may edit its file. */
function EditableIncludeSections({ graph, include, rows, session }: IncludeSectionsProps) {
  return (
    <>
      {fileEditable(session, includerFile(graph, include)) ? (
        <OverrideForm graph={graph} include={include} session={session} rows={rows.overrides} />
      ) : (
        <OverridesSection graph={graph} rows={rows.overrides} />
      )}
      {fileEditable(session, include.source) ? (
        <InputDefaultForm graph={graph} include={include} session={session} rows={rows.inputs} />
      ) : (
        <OwnInputsSection graph={graph} include={include} rows={rows.inputs} />
      )}
    </>
  );
}

/** The open session's inspector: the shared sections plus the Cache section and the edit forms. */
export function Inspector() {
  const data = useCurrentSession().data;
  const preview = useCurrentPreview().data ?? null;
  const store = useViewStore();
  const selected = store((state) => state.selected);
  const drill = store((state) => state.drill);

  if (!data) {
    return <div className="rc-placeholder">Open a recipe from the Recipes pane.</div>;
  }
  const survey = surveyBackedSteps(preview);
  return (
    <InspectorBody
      graph={data.graph}
      selected={selected}
      drill={drill}
      inputValues={data.inputs.values}
      declared={data.inputs.declared}
      stepParams={(step) =>
        fileEditable(data, step.document) ? (
          <StepParamForm
            key={step.id}
            graph={data.graph}
            step={step}
            session={data}
            surveyBacked={survey.has(step.id)}
            onOpenRunConfig={() => showPane('runConfig')}
          />
        ) : null
      }
      stepExtras={(step) => <StepCache step={step} />}
      recipeInputs={<RecipeInputsInspector graph={data.graph} session={data} />}
      includeSections={(include, rows) => (
        <EditableIncludeSections key={include.id} graph={data.graph} include={include} rows={rows} session={data} />
      )}
      runConfig={<RunConfigInspector block={data.run_config} declared={data.inputs.declared} />}
    />
  );
}
