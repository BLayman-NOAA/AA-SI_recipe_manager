/** The statistics pane: the selected step's saved table as grouped bars and counts. */
import { useArtifactJson, useCurrentSession, useStepArtifacts } from '../api/queries';
import { useRunControlsContext } from '../runs/useRunControls';
import { usePaneVisible } from '../shell/dock';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { chooseOutput, jsonOutputs, paneMessage, paneState, selectedStep, wantsArtifacts } from './paneState';
import { StatsView } from './StatsView';
import { useStatsStore } from './store';
import './stats.css';

/** The offer to run to a step that has no saved table. */
function RunOffer({ step }: { step: string }) {
  const { canRun, isActive, runToHere } = useRunControlsContext();
  const reason = isActive ? 'A run is going' : 'A run can start once the recipe is valid';
  return (
    <button
      type="button"
      className="rc-button rc-small rc-primary"
      disabled={!canRun}
      title={canRun ? undefined : reason}
      onClick={() => runToHere(step)}
    >
      Run to {step}
    </button>
  );
}

export function StatsPane() {
  const sid = useSessionStore((state) => state.sid);
  const graph = useCurrentSession().data?.graph;
  const selected = useView((state) => state.selected);
  const choice = useStatsStore((state) => state.choice);
  const choose = useStatsStore((state) => state.choose);
  const visible = usePaneVisible();
  const step = selectedStep(graph, selected);
  const wanted = wantsArtifacts(step, choice);
  const found = useStepArtifacts(sid, wanted ? step.id : null, visible);
  const outputs = wanted ? jsonOutputs(found.data) : [];
  const output = wanted ? chooseOutput(step.id, outputs, choice) : null;
  const hash = found.data?.step_hash ?? null;
  const json = useArtifactJson(sid, wanted ? step.id : null, output, hash, visible);
  const state = paneState({
    sid,
    step,
    choice,
    artifacts: found.data,
    artifactsError: found.error,
    output,
    json: json.data,
    jsonError: json.error,
  });
  // A refetch keeps the table drawn, dimmed, until the answer arrives.
  const refreshing = (json.isFetching || found.isFetching) && state.kind === 'table';
  return (
    <div className={refreshing ? 'rc-pane rc-stats rc-stats-refreshing' : 'rc-pane rc-stats'}>
      {wanted && outputs.length > 1 && output !== null && (
        <label className="rc-pane-toolbar">
          Output
          <select
            className="rc-input rc-stats-output"
            value={output}
            onChange={(event) => choose({ step: step.id, output: event.target.value })}
          >
            {outputs.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </label>
      )}
      {state.kind === 'table' ? (
        <StatsView table={state.table} stepId={state.step} output={state.output} />
      ) : (
        <p className="rc-empty">{paneMessage(state)}</p>
      )}
      {state.kind === 'unsaved' && <RunOffer step={state.step} />}
    </div>
  );
}
