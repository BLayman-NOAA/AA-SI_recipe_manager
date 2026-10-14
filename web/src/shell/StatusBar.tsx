import { stepNodes } from '../api/graph';
import type { Problem } from '../api/models';
import { useCurrentSession, useHealth, useOpeningRecipe } from '../api/queries';
import { plural } from '../text';
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
