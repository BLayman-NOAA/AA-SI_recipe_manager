import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { buildView } from '../src/diagram/buildView';
import { useEditingStore } from '../src/editing/store';
import { groupByInclude, surveyHitsLost, surveyTierLost } from '../src/editing/survey';
import { SurveyWarningDialog } from '../src/editing/SurveyWarningDialog';
import { useView } from '../src/state/view';
import { editableSession, makePreview, makeSession, mockApi, previewStep, renderWithClient, withSubGraph } from './helpers';

const SID = makeSession().id;

const PREVIEW = makePreview([
  previewStep('seed', { held_by: ['user', 'survey'] }),
  previewStep('chain_inc', { held_by: ['survey'] }),
  previewStep('leaf_add', { held_by: ['survey'] }),
  previewStep('total', { held_by: ['user'] }),
]);

beforeEach(() => {
  useEditingStore.getState().reset();
  useView.getState().resetScope();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('surveyHitsLost', () => {
  it('keeps the changed steps the survey tier held, in run order', () => {
    expect(surveyHitsLost(['chain_inc', 'leaf_add', 'total'], PREVIEW)).toEqual(['chain_inc', 'leaf_add']);
    expect(surveyHitsLost(['total'], PREVIEW)).toEqual([]);
  });

  it('finds none without hashes or a preview', () => {
    expect(surveyHitsLost(null, PREVIEW)).toEqual([]);
    expect(surveyHitsLost(['leaf_add'], null)).toEqual([]);
  });
});

describe('surveyTierLost', () => {
  it('keeps the steps survey backed before and not after, in run order', () => {
    const after = makePreview([
      previewStep('seed', { held_by: ['user'] }),
      previewStep('chain_inc', { held_by: ['survey'] }),
      previewStep('leaf_add'),
      previewStep('total', { held_by: ['user'] }),
    ]);
    expect(surveyTierLost(PREVIEW, after)).toEqual(['seed', 'leaf_add']);
    expect(surveyTierLost(PREVIEW, PREVIEW)).toEqual([]);
    expect(surveyTierLost(PREVIEW, null)).toEqual([]);
  });
});

describe('groupByInclude', () => {
  it('groups steps by their innermost sub-recipe, the top level first', () => {
    const groups = groupByInclude(withSubGraph, ['leaf_add', 'seed', 'chain_inc']);
    expect(groups.map((group) => [group.include?.id ?? null, group.steps])).toEqual([
      [null, ['seed']],
      ['inc:sub/leaf.yaml', ['leaf_add']],
      ['inc:sub/chain.yaml', ['chain_inc']],
    ]);
  });
});

describe('SurveyWarningDialog', () => {
  const warning = { changedHashes: ['chain_inc', 'leaf_add', 'total'], lost: ['chain_inc', 'leaf_add'] };

  function renderDialog() {
    const fetchMock = mockApi({
      [`POST /api/sessions/${SID}/undo`]: {
        session: editableSession({ version: 2 }),
        changed: true,
        changed_hashes: ['chain_inc', 'leaf_add', 'total'],
        entries: [],
      },
    });
    useEditingStore.getState().showSurveyWarning(warning);
    renderWithClient(<SurveyWarningDialog sid={SID} graph={withSubGraph} />);
    return fetchMock;
  }

  it('lists the steps by sub-recipe with Undo focused', () => {
    renderDialog();
    const dialog = screen.getByRole('dialog', { name: 'Survey backed steps' });
    expect(
      within(dialog).getByText(
        'This edit changes the hash of 2 survey backed steps, which would now be recomputed in your user cache:',
      ),
    ).toBeInTheDocument();
    expect(within(dialog).getByText('client_chain')).toBeInTheDocument();
    expect(within(dialog).getByText('client_leaf')).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Undo' })).toHaveFocus();
  });

  it('undoes the edit', async () => {
    const user = userEvent.setup();
    const fetchMock = renderDialog();
    await user.click(screen.getByRole('button', { name: 'Undo' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => url === `/api/sessions/${SID}/undo`)).toBe(true),
    );
  });

  it('keeps the change', async () => {
    const user = userEvent.setup();
    const fetchMock = renderDialog();
    await user.click(screen.getByRole('button', { name: 'Keep change' }));
    expect(useEditingStore.getState().surveyWarning).toBeNull();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toEqual([]);
  });

  it('shows the steps on the diagram until the selection changes', async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.click(screen.getByRole('button', { name: 'Show on diagram' }));
    expect(useEditingStore.getState().surveyWarning).toBeNull();
    expect([...useView.getState().highlight]).toEqual(['chain_inc', 'leaf_add']);

    const view = buildView(withSubGraph, {
      drill: [],
      connected: false,
      selected: null,
      highlight: useView.getState().highlight,
      expandedSteps: new Set(),
      expandedBoxes: new Set(['inc:sub/chain.yaml']),
    });
    const matched = view.nodes.filter((node) => node.match).map((node) => node.id);
    expect(matched).toContain('inc:sub/chain.yaml');
    expect(matched).not.toContain('seed');

    useView.getState().select({ kind: 'step', id: 'seed' });
    expect(useView.getState().highlight.size).toBe(0);
  });
});
