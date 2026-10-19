import type { RunRecord } from '../api/models';
import { useRuns } from '../api/queries';
import { showPane } from '../shell/dock';
import { useSessionStore } from '../state/session';
import { firstSentence, secondsText } from '../text';
import { shownRun, useRunStore } from './store';

/** "to bump" or "only scaled", with "force" when forced; "all" for a whole run. */
export function runWhat(run: RunRecord): string {
  const what = run.mode === 'all' ? 'all' : `${run.mode} ${run.targets.join(', ')}`;
  const forced = run.force ? ', force all' : run.force_steps.length ? `, force ${run.force_steps.join(', ')}` : '';
  return what + forced;
}

/**
 * Why a run did not complete: its failed step, else the first sentence of
 * its message (such as the server's note on a run process that stopped).
 */
export function runReason(run: RunRecord): string {
  if (run.status === 'completed') return '';
  return run.summary?.failed_step ?? firstSentence(run.summary?.message ?? '');
}

/** Seconds between two ISO times, or null. */
export function duration(run: RunRecord): number | null {
  if (run.summary?.seconds !== undefined && run.summary?.seconds !== null) return run.summary.seconds;
  if (!run.started || !run.finished) return null;
  return (Date.parse(run.finished) - Date.parse(run.started)) / 1000;
}

interface RunTableProps {
  runs: RunRecord[];
  /** The run whose marks the diagram shows. */
  shown: string | null;
  onLogs: (runId: string) => void;
  onShow: (runId: string) => void;
}

/** The session's runs, newest first. */
export function RunTable({ runs, shown, onLogs, onShow }: RunTableProps) {
  if (runs.length === 0) return <div className="rc-empty">No runs yet.</div>;
  return (
    <table className="rc-table rc-runs">
      <thead>
        <tr>
          <th>run</th>
          <th>started</th>
          <th>what</th>
          <th>executor</th>
          <th>status</th>
          <th>time</th>
          <th>executed / skipped / untargeted</th>
          <th>failed step or reason</th>
          <th />
        </tr>
      </thead>
      <tbody>
        {runs.map((run, index) => {
          const seconds = duration(run);
          const summary = run.summary;
          return (
            <tr key={run.id} className={run.id === shown ? 'rc-row-current' : undefined}>
              <td>{runs.length - index}</td>
              <td className="rc-mono">{(run.started ?? run.created).slice(0, 19).replace('T', ' ')}</td>
              <td className="rc-mono">{runWhat(run)}</td>
              <td>{run.executor ?? 'sequential'}</td>
              <td className={`rc-run-status-${run.status}`} title={summary?.message ?? undefined}>
                {run.status}
                {run.killed ? ' (killed)' : ''}
              </td>
              <td>{seconds === null ? '' : secondsText(seconds)}</td>
              <td>{summary ? `${summary.executed} / ${summary.skipped} / ${summary.untargeted}` : ''}</td>
              <td className={summary?.failed_step ? 'rc-mono' : undefined} title={summary?.message ?? undefined}>
                {runReason(run)}
              </td>
              <td className="rc-row-actions">
                <button type="button" className="rc-button rc-small" onClick={() => onLogs(run.id)}>
                  Logs
                </button>
                <button
                  type="button"
                  className="rc-button rc-small"
                  disabled={run.id === shown}
                  onClick={() => onShow(run.id)}
                >
                  Show on diagram
                </button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Run history of the open session (FR-72). */
export function RunsPane() {
  const sid = useSessionStore((state) => state.sid);
  const runs = useRuns(sid);
  const shown = useRunStore((state) => shownRun(state)?.runId ?? null);
  if (!sid) return <div className="rc-placeholder">No session is open.</div>;
  if (runs.error) return <div className="rc-placeholder rc-error-text">{runs.error.message}</div>;
  const { setLogRun, setLogStep, show } = useRunStore.getState();
  return (
    <div className="rc-pane">
      <RunTable
        runs={runs.data ?? []}
        shown={shown}
        onLogs={(runId) => {
          setLogRun(runId);
          setLogStep('');
          showPane('logs');
        }}
        onShow={(runId) => {
          show(runId);
          showPane('diagram');
        }}
      />
    </div>
  );
}
