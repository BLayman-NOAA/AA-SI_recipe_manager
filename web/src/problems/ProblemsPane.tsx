import type { Graph } from '../api/graph';
import type { Preview, Problem } from '../api/models';
import { useCurrentSession } from '../api/queries';
import { selectStep } from '../diagram/locate';
import type { StepFailedData } from '../runs/events';
import { failedSteps } from '../runs/reducer';
import { openStepLog, useShownRun } from '../runs/store';
import { useCurrentPreview } from '../runs/usePreviewIntent';
import { useView } from '../state/view';

/** The preview's warnings and its unsaved blockers, as warnings. */
export function previewProblems(preview: Preview | null | undefined): Problem[] {
  if (!preview) return [];
  const problems: Problem[] = preview.warnings.map((message) => ({
    severity: 'warning',
    message: `Preview: ${message}`,
    step: null,
  }));
  if (preview.blockers.length > 0) {
    problems.push({
      severity: 'warning',
      message: `Preview: these steps are not saved, so later runs start before them: ${preview.blockers.join(', ')}`,
      step: null,
    });
  }
  return problems;
}

/** Errors before warnings, otherwise in the server's order. */
export function sortProblems(problems: Problem[]): Problem[] {
  const rank = (problem: Problem) => (problem.severity === 'error' ? 0 : 1);
  return problems
    .map((problem, index) => ({ problem, index }))
    .sort((a, b) => rank(a.problem) - rank(b.problem) || a.index - b.index)
    .map((entry) => entry.problem);
}

interface ProblemListProps {
  problems: Problem[];
  graph: Graph | null;
}

/** Validation problems, errors first; a click selects the problem's step. */
export function ProblemList({ problems, graph }: ProblemListProps) {
  if (problems.length === 0) {
    return <div className="rc-empty">No problems.</div>;
  }
  return (
    <ul className="rc-list">
      {sortProblems(problems).map((problem, index) => (
        <li key={index}>
          <button
            type="button"
            className={`rc-problem rc-problem-${problem.severity}`}
            onClick={() => problem.step && selectStep(useView, graph, problem.step)}
            disabled={!problem.step}
          >
            <span className="rc-problem-icon" aria-label={problem.severity}>
              {problem.severity === 'error' ? 'error' : 'warning'}
            </span>
            <span className="rc-row-main">
              {problem.step && <span className="rc-mono">{problem.step}: </span>}
              {problem.message}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

interface RunFailureListProps {
  failures: StepFailedData[];
  graph: Graph | null;
}

/** Steps that failed in the run shown; a click selects the step and opens its log. */
export function RunFailureList({ failures, graph }: RunFailureListProps) {
  return (
    <ul className="rc-list">
      {failures.map((failure) => (
        <li key={failure.step}>
          <button
            type="button"
            className="rc-problem rc-problem-error"
            onClick={() => {
              selectStep(useView, graph, failure.step);
              openStepLog(failure.step);
            }}
          >
            <span className="rc-problem-icon" aria-label="error">
              failed
            </span>
            <span className="rc-row-main">
              <span className="rc-mono">{failure.step}: </span>
              {[failure.error_type, failure.message].filter(Boolean).join(': ')}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}

export function ProblemsPane() {
  const session = useCurrentSession();
  const preview = useCurrentPreview().data;
  const run = useShownRun();
  if (!session.data) {
    return <div className="rc-placeholder">No session is open.</div>;
  }
  const failures = run ? failedSteps(run) : [];
  const problems = [...session.data.validation.problems, ...previewProblems(preview)];
  return (
    <div className="rc-pane">
      <ProblemList problems={problems} graph={session.data.graph} />
      {failures.length > 0 && (
        <section>
          <h3>Last run</h3>
          <RunFailureList failures={failures} graph={session.data.graph} />
        </section>
      )}
    </div>
  );
}
