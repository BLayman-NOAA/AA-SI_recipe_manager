import { stepNodes } from '../api/graph';
import type { Problem } from '../api/models';
import { useCurrentSession, useHealth, useOpeningRecipe, useRuns } from '../api/queries';
import { failedSteps, runProgress, type RunState } from '../runs/reducer';
import { useRunStore } from '../runs/store';
import { useSessionStore } from '../state/session';
import { firstSentence, plural, secondsText } from '../text';
import { useDocsUrl } from './links';

/** "2 errors, 1 warning", or "valid" when there are no problems. */
export function validationSummary(problems: Problem[]): string {
  const errors = problems.filter((problem) => problem.severity === 'error').length;
  const warnings = problems.length - errors;
  const parts: string[] = [];
  if (errors) parts.push(plural(errors, 'error'));
  if (warnings) parts.push(plural(warnings, 'warning'));
  return parts.length ? parts.join(', ') : 'valid';
}

/** Shown from Cancel until run_finished: core stops between steps, so the step running ends first. */
export const CANCELLING_TEXT = 'cancelling: waiting for the current step to stop';

/** "cancelling: waiting for slow to finish", naming the steps running, else CANCELLING_TEXT. */
export function cancellingText(run: RunState): string {
  const running = Object.entries(run.steps)
    .filter(([, step]) => step.status === 'running')
    .sort(([, a], [, b]) => a.index - b.index)
    .map(([id]) => id);
  if (running.length === 0) return CANCELLING_TEXT;
  const names = running.length > 2 ? `${running.slice(0, 2).join(', ')} and ${running.length - 2} more` : running.join(' and ');
  return `cancelling: waiting for ${names} to finish`;
}

/**
 * A run's state in a few words: "running 12/37", "completed in 77 s",
 * "failed at x", "cancelling: ...", "cancelled". A run that ended without a
 * failed step shows the first sentence of its message (the server writes one
 * for a run process that stopped on its own); runStatusTitle has all of it.
 */
export function runStatusText(run: RunState, cancelling: boolean): string {
  const finished = run.finished;
  if (!finished) {
    if (cancelling) return cancellingText(run);
    if (run.status === 'pending') return 'starting';
    const { done, total } = runProgress(run);
    return `running ${done}/${total}`;
  }
  switch (finished.status) {
    case 'completed':
      return finished.seconds === null ? 'completed' : `completed in ${secondsText(finished.seconds)}`;
    case 'failed': {
      const failed = failedSteps(run)[0];
      if (failed) return `failed at ${failed.step}`;
      return finished.message ? `failed: ${firstSentence(finished.message)}` : 'failed, see the log';
    }
    default:
      return finished.status;
  }
}

/** The tooltip of a run's status: the run's full end message, if any. */
export function runStatusTitle(run: RunState): string | undefined {
  return run.finished?.message ?? undefined;
}

const RUN_TONE: Partial<Record<RunState['status'], string>> = {
  completed: 'rc-status-ok',
  failed: 'rc-status-error',
  cancelled: 'rc-status-warning',
  interrupted: 'rc-status-warning',
};

/** The run being followed, or the last one: "run 3: running 12/37". */
function RunStatus() {
  const sid = useSessionStore((state) => state.sid);
  const runs = useRuns(sid).data ?? [];
  const current = useRunStore((state) => state.current);
  const last = useRunStore((state) => state.last);
  const cancelling = useRunStore((state) => state.cancelling);
  const error = useRunStore((state) => state.error);
  const run = current ?? last;
  if (error) return <span className="rc-status-error">{error}</span>;
  if (!run) return null;
  const index = runs.findIndex((record) => record.id === run.runId);
  const label = index < 0 ? 'run' : `run ${runs.length - index}`;
  const text = runStatusText(run, cancelling === run.runId);
  return (
    <span className={RUN_TONE[run.status]} title={runStatusTitle(run)}>
      {`${label}: ${text}`}
    </span>
  );
}

function SessionStatus() {
  const session = useCurrentSession();
  const opening = useOpeningRecipe();
  const doc = session.data;
  if (opening) return <span>{`Opening ${opening}...`}</span>;
  if (doc) return <span>{`session: ${doc.title}${doc.read_only ? ' (read only)' : ''}`}</span>;
  if (session.error) {
    return <span className="rc-status-error">{`session: ${session.error.message}`}</span>;
  }
  return <span>{session.isFetching ? 'opening session' : 'no session'}</span>;
}

/** Session, validation and server connection at the bottom of the window. */
export function StatusBar() {
  const session = useCurrentSession();
  const health = useHealth();
  const docsUrl = useDocsUrl();
  const doc = session.data;
  const problems = doc?.validation.problems ?? [];
  const hasErrors = problems.some((problem) => problem.severity === 'error');

  return (
    <div className="rc-statusbar" role="status">
      <SessionStatus />
      {doc && <span>{plural(stepNodes(doc.graph).length, 'step')}</span>}
      {doc && (
        <span className={hasErrors ? 'rc-status-error' : problems.length ? 'rc-status-warning' : 'rc-status-ok'}>
          {validationSummary(problems)}
        </span>
      )}
      {doc && <RunStatus />}
      <span className="rc-spacer" />
      <span className={health.isError ? 'rc-status-error' : health.data ? 'rc-status-ok' : undefined}>
        {health.isError ? 'server: not reachable' : health.data ? `server: ok ${health.data.version}` : 'server: connecting'}
      </span>
      <a href={docsUrl} target="_blank" rel="noreferrer">
        docs
      </a>
    </div>
  );
}
