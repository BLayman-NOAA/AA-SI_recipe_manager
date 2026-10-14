import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import { stepLine } from '../src/yaml/findLine';
import { YamlPane } from '../src/yaml/YamlPane';
import { makeSession, mockApi, renderWithClient } from './helpers';

const SID = '20261013-153012-a1b2c3';

/** Find a YAML code line by its whole text; each line is split into coloured tokens. */
function findLine(text: string) {
  return screen.findByText(
    (_, element) => element?.classList.contains('rc-yaml-code') === true && element.textContent === text,
  );
}
const BASE = `/api/sessions/${SID}`;

const ROOT = [
  'name: client_with_sub',
  'steps:',
  '  - id: seed',
  '    op: custom',
  '  - include: sub/chain.yaml',
].join('\n');
const CHAIN = [
  'name: client_chain',
  'steps:',
  '  - id: chain_inc_first',
  '    op: custom',
  '  - id: chain_inc',
  '    op: custom',
].join('\n');

describe('stepLine', () => {
  it('finds the `- id:` line the real recipes use', () => {
    const text = 'steps:\n\n  - id: dive_sv\n    op: x\n  - id: dive_depth\n';
    expect(stepLine(text, 'dive_depth')).toBe(4);
  });

  it('does not match a longer id that starts the same', () => {
    expect(stepLine(CHAIN, 'chain_inc')).toBe(4);
  });

  it('accepts quotes, a trailing comment and CRLF line ends', () => {
    expect(stepLine('steps:\r\n  - id: "a.b"  # note\r\n', 'a.b')).toBe(1);
    expect(stepLine("- id: 'x'", 'x')).toBe(0);
  });

  it('falls back to an id that is not the first key', () => {
    expect(stepLine('steps:\n  - op: custom\n    id: late\n', 'late')).toBe(2);
  });

  it('returns null when the step is not in the text', () => {
    expect(stepLine(ROOT, 'missing')).toBeNull();
  });
});

describe('YamlPane', () => {
  beforeEach(() => {
    useSessionStore.getState().setSid(null);
    useView.getState().resetScope();
    mockApi({
      [`GET ${BASE}`]: makeSession(),
      [`GET ${BASE}/files/with_sub.yaml`]: { path: 'with_sub.yaml', text: ROOT },
      [`GET ${BASE}/files/sub%2Fchain.yaml`]: { path: 'sub/chain.yaml', text: CHAIN },
      [`GET ${BASE}/files/sub%2Fleaf.yaml`]: { path: 'sub/leaf.yaml', text: 'name: client_leaf' },
    });
    useSessionStore.getState().setSid(SID);
  });

  it('shows the root file with line numbers in a tab per file', async () => {
    renderWithClient(<YamlPane />);
    expect(await findLine('name: client_with_sub')).toBeInTheDocument();
    expect(document.querySelector('.rc-tok-key')).toHaveTextContent('name');
    expect(screen.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'with_sub.yaml',
      'sub/chain.yaml',
      'sub/leaf.yaml',
    ]);
    expect(screen.getByRole('tab', { name: 'with_sub.yaml' })).toHaveAttribute('aria-selected', 'true');
  });

  it('switches to the selected step file and highlights its line', async () => {
    renderWithClient(<YamlPane />);
    await findLine('name: client_with_sub');
    act(() => useView.getState().select({ kind: 'step', id: 'chain_inc' }));

    await waitFor(() =>
      expect(screen.getByRole('tab', { name: 'sub/chain.yaml' })).toHaveAttribute('aria-selected', 'true'),
    );
    const line = await findLine('  - id: chain_inc');
    expect(line.closest('.rc-yaml-line')).toHaveClass('rc-yaml-highlight');
    expect(document.querySelectorAll('.rc-yaml-highlight')).toHaveLength(1);
  });

  it('lets the user pick another tab', async () => {
    const user = userEvent.setup();
    renderWithClient(<YamlPane />);
    await findLine('name: client_with_sub');
    await user.click(screen.getByRole('tab', { name: 'sub/leaf.yaml' }));
    expect(await findLine('name: client_leaf')).toBeInTheDocument();
  });
});
