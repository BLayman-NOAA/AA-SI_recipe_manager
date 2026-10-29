/** The open session's inspector: InspectorBody with the Cache section and the inputs form. */
import { useId, useState, type ReactNode } from 'react';
import { fieldSources } from '../api/fields';
import { echogramCandidate, type Graph, type StepNode } from '../api/graph';
import type { InputField, SessionDocument, StepPreview } from '../api/models';
import { useCurrentSession, useMutationError, useSetInputs, useStepArtifacts } from '../api/queries';
import { recipeInputRows, rowValue } from '../diagram/buildView';
import { showEchogram } from '../echogram/show';
import { DraftActions, InputControl, InputProblems, useInputDraft } from '../inputs/InputsPane';
import { staleResult, statusText } from '../runs/badges';
import type { StepRunState } from '../runs/reducer';
import { openStepLog, useShownRun } from '../runs/store';
import { useCurrentPreview } from '../runs/usePreviewIntent';
import { useSessionStore } from '../state/session';
import { useViewStore } from '../state/view';
import { plural, secondsText, valueText } from '../text';
import { ByFile } from './ByFile';
import {
  declaredInFact,
  Facts,
  InspectorBody,
  Mono,
  RecipeInputDetails,
  RowSummary,
  StepLinks,
} from './InspectorBody';

/** The tiers holding a step: "user, survey", with instance counts for a fanned out step. */
function heldText(ahead: StepPreview): string {
  if (ahead.held_by.length === 0 && Object.keys(ahead.instances_held).length === 0) return 'none';
  if (!ahead.fanned_out) return ahead.held_by.join(', ');
  const of = ahead.instances === null ? '' : `/${ahead.instances}`;
  return Object.entries(ahead.instances_held)
    .map(([tier, count]) => `${tier} ${count}${of}`)
    .join(', ');
}

/** The last actual outcome: "computed in 1.2 s (user)". */
function actualText(actual: StepRunState): string {
  const parts: string[] = [statusText(actual.status)];
  if (actual.seconds !== null && actual.status !== 'running') parts.push(`in ${secondsText(actual.seconds)}`);
  if (actual.tier) parts.push(`(${actual.tier})`);
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

function StepCache({ step }: { step: StepNode }) {
  const sid = useSessionStore((state) => state.sid);
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
  if (ahead) rows.push(['this run', `${ahead.checkpoint}${ahead.pinned ? ', pinned as a target' : ''}`]);
  rows.push(['regenerate', step.regenerate ?? 'default']);
  return (
    <section>
      <h3>Cache</h3>
      <Facts rows={rows} />
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
function RecipeInputsInspector({ graph, session }: { graph: Graph; session: SessionDocument }) {
  const setInputs = useSetInputs(session.id);
  const applyError = useMutationError(setInputs);
  const fields = session.inputs.declared;
  const draft = useInputDraft(fields);
  const idPrefix = useId();
  const rows = recipeInputRows(graph, session.inputs.values, fieldSources(fields));
  const byName = new Map(fields.map((field) => [field.name, field]));
  // Rows and fields both come from the declared inputs, so this is empty unless the two disagree.
  const unused = fields.filter((field) => !rows.some((row) => row.name === field.name));
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
        <ByFile graph={graph} items={rows} fileOf={(row) => row.declaredIn}>
          {(row) => {
            const field = byName.get(row.name);
            return (
              <details key={row.name} className="rc-row-details" open={opened(field)}>
                <RowSummary name={row.name} value={rowValue(row)} source={field?.source} />
                {field ? (
                  <>
                    {control(field)}
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

/** The open session's inspector: the shared sections plus the Cache section and the inputs form. */
export function Inspector() {
  const data = useCurrentSession().data;
  const store = useViewStore();
  const selected = store((state) => state.selected);
  const drill = store((state) => state.drill);

  if (!data) {
    return <div className="rc-placeholder">Open a recipe from the Recipes pane.</div>;
  }
  return (
    <InspectorBody
      graph={data.graph}
      selected={selected}
      drill={drill}
      inputValues={data.inputs.values}
      declared={data.inputs.declared}
      stepExtras={(step) => <StepCache step={step} />}
      recipeInputs={<RecipeInputsInspector graph={data.graph} session={data} />}
    />
  );
}
