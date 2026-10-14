import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { Problem } from '../src/api/models';
import { ProblemList, sortProblems } from '../src/problems/ProblemsPane';
import { useView } from '../src/state/view';
import { withSubGraph } from './helpers';

const PROBLEMS: Problem[] = [
  { severity: 'warning', message: 'Input n is set but unused.', step: null },
  { severity: 'error', message: "Step 'leaf_add' is missing input x.", step: 'leaf_add' },
  { severity: 'warning', message: "Step 'seed' has an odd param.", step: 'seed' },
  { severity: 'error', message: 'Path ./Auxiliary does not exist.', step: null },
];

beforeEach(() => {
  useView.getState().resetScope();
});

describe('sortProblems', () => {
  it('puts errors before warnings and keeps the order within each', () => {
    expect(sortProblems(PROBLEMS).map((problem) => problem.message)).toEqual([
      "Step 'leaf_add' is missing input x.",
      'Path ./Auxiliary does not exist.',
      'Input n is set but unused.',
      "Step 'seed' has an odd param.",
    ]);
  });
});

describe('ProblemList', () => {
  it('lists errors before warnings', () => {
    render(<ProblemList problems={PROBLEMS} graph={withSubGraph} />);
    const severities = screen
      .getAllByRole('button')
      .map((button) => button.querySelector('.rc-problem-icon')?.textContent);
    expect(severities).toEqual(['error', 'error', 'warning', 'warning']);
  });

  it('selects the step and drills into the includes around it on click', async () => {
    const user = userEvent.setup();
    render(<ProblemList problems={PROBLEMS} graph={withSubGraph} />);
    await user.click(screen.getByRole('button', { name: /missing input x/ }));

    const view = useView.getState();
    expect(view.selected).toEqual({ kind: 'step', id: 'leaf_add' });
    expect(view.drill).toEqual(['inc:sub/chain.yaml', 'inc:sub/leaf.yaml']);
  });

  it('does not select anything for a problem without a step', async () => {
    const user = userEvent.setup();
    render(<ProblemList problems={PROBLEMS} graph={withSubGraph} />);
    await user.click(screen.getByRole('button', { name: /Auxiliary/ }));
    expect(useView.getState().selected).toBeNull();
  });

  it('says so when there are no problems', () => {
    render(<ProblemList problems={[]} graph={withSubGraph} />);
    expect(screen.getByText('No problems.')).toBeInTheDocument();
  });
});
