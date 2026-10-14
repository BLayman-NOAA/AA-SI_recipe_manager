import type { Graph } from '../api/graph';
import type { Problem } from '../api/models';
import { useCurrentSession } from '../api/queries';
import { selectStep } from '../diagram/locate';

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
            onClick={() => problem.step && selectStep(graph, problem.step)}
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

export function ProblemsPane() {
  const session = useCurrentSession();
  if (!session.data) {
    return <div className="rc-placeholder">No session is open.</div>;
  }
  return (
    <div className="rc-pane">
      <ProblemList problems={session.data.validation.problems} graph={session.data.graph} />
    </div>
  );
}
