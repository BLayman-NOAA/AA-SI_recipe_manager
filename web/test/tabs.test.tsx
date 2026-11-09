import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RunRecord, SessionSummary } from '../src/api/models';
import { queryKeys } from '../src/api/queries';
import { restoredLayer } from '../src/echogram/layers';
import { useEchogramStore } from '../src/echogram/store';
import { TabStrip, tabStatus } from '../src/shell/TabStrip';
import { SESSION_KEY, useSessionStore } from '../src/state/session';
import { closeTab, openTab, readStoredTabs, TABS_KEY, useTabs } from '../src/state/tabs';
import { useView } from '../src/state/view';
import { errorResponse, makeSession, mockApi, renderWithClient } from './helpers';

const stored = () => JSON.parse(window.localStorage.getItem(TABS_KEY)!);

beforeEach(() => {
  useTabs.setState({ sids: [], pendingSelection: null });
  useSessionStore.getState().setSid(null);
});

afterEach(() => {
  useSessionStore.getState().setSid(null);
  vi.unstubAllGlobals();
});

describe('the saved tabs', () => {
  it('reads the saved order, tolerating a missing or malformed value', () => {
    expect(readStoredTabs()).toEqual([]);
    window.localStorage.setItem(TABS_KEY, JSON.stringify(['A', 'B', 'A', 3, '', null, 'C']));
    expect(readStoredTabs()).toEqual(['A', 'B', 'C']);
    window.localStorage.setItem(TABS_KEY, '{not json');
    expect(readStoredTabs()).toEqual([]);
    window.localStorage.setItem(TABS_KEY, JSON.stringify({ sids: ['A'] }));
    expect(readStoredTabs()).toEqual([]);
  });

  it('always includes the open session, as one tab for a client that had only it', () => {
    window.localStorage.setItem(SESSION_KEY, 'S');
    expect(readStoredTabs()).toEqual(['S']);
    window.localStorage.setItem(TABS_KEY, JSON.stringify(['A', 'S']));
    expect(readStoredTabs()).toEqual(['A', 'S']);
    window.localStorage.setItem(TABS_KEY, JSON.stringify(['A']));
    expect(readStoredTabs()).toEqual(['A', 'S']);
  });
});

describe('opening, closing and moving tabs', () => {
  it('opens a tab once and brings it to the front', () => {
    openTab('A');
    openTab('B');
    openTab('A');
    expect(useTabs.getState().sids).toEqual(['A', 'B']);
    expect(useSessionStore.getState().sid).toBe('A');
    expect(stored()).toEqual(['A', 'B']);
    expect(useTabs.getState().pendingSelection).toBeNull();
  });

  it('keeps the step to select when a tab is opened for one', () => {
    openTab('A', 'crop_survey_range');
    expect(useTabs.getState().pendingSelection).toEqual({ sid: 'A', step: 'crop_survey_range' });
    useTabs.getState().clearSelection();
    expect(useTabs.getState().pendingSelection).toBeNull();
  });

  it('brings the left neighbour of a closed front tab to the front, else the right, else none', () => {
    openTab('A');
    openTab('B');
    openTab('C');
    expect(closeTab('C')).toBe('B');
    expect(useSessionStore.getState().sid).toBe('B');
    openTab('A');
    expect(closeTab('A')).toBe('B');
    expect(useSessionStore.getState().sid).toBe('B');
    expect(closeTab('B')).toBeNull();
    expect(useSessionStore.getState().sid).toBeNull();
    expect(useTabs.getState().sids).toEqual([]);
    expect(stored()).toEqual([]);
  });

  it('leaves the front alone when a tab behind it closes, and drops its pending selection', () => {
    openTab('A');
    openTab('B', 'sv');
    openTab('A');
    useTabs.setState({ pendingSelection: { sid: 'B', step: 'sv' } });
    expect(closeTab('B')).toBe('A');
    expect(useSessionStore.getState().sid).toBe('A');
    expect(useTabs.getState()).toMatchObject({ sids: ['A'], pendingSelection: null });
  });

  it('moves a tab, clamping the index, and saves the order', () => {
    openTab('A');
    openTab('B');
    openTab('C');
    useTabs.getState().move('C', 0);
    expect(useTabs.getState().sids).toEqual(['C', 'A', 'B']);
    useTabs.getState().move('C', 10);
    expect(useTabs.getState().sids).toEqual(['A', 'B', 'C']);
    useTabs.getState().move('X', 0);
    expect(stored()).toEqual(['A', 'B', 'C']);
  });
});

function run(id: string, status: RunRecord['status']): RunRecord {
  return { id, session: 'A', status, created: '2026-11-03T10:00:00Z' } as RunRecord;
}

describe('tabStatus', () => {
  it('reads the newest run', () => {
    expect(tabStatus(undefined)).toBe('none');
    expect(tabStatus([])).toBe('none');
    expect(tabStatus([run('r2', 'running'), run('r1', 'failed')])).toBe('active');
    expect(tabStatus([run('r1', 'queued')])).toBe('active');
    expect(tabStatus([run('r1', 'completed')])).toBe('completed');
    expect(tabStatus([run('r1', 'failed')])).toBe('failed');
    expect(tabStatus([run('r1', 'cancelled')])).toBe('stopped');
    expect(tabStatus([run('r1', 'interrupted')])).toBe('stopped');
  });
});

const SUMMARIES: SessionSummary[] = [
  { id: 'A', title: 'demo_survey_sv', origin: 'C:/r/demo_survey_sv.yaml', created: '2026-11-03T10:00:00Z' },
  { id: 'B', title: 'demo_dive_analysis', origin: 'C:/r/demo_dive_analysis.yaml', created: '2026-11-03T11:00:00Z' },
];

function serve(routes: Record<string, unknown> = {}) {
  return mockApi({
    'GET /api/sessions': SUMMARIES,
    'GET /api/sessions/A': makeSession({ id: 'A', title: 'demo_survey_sv' }),
    'GET /api/sessions/B': makeSession({ id: 'B', title: 'demo_dive_analysis', dirty: ['with_sub.yaml'] }),
    'GET /api/sessions/A/runs': [],
    'GET /api/sessions/B/runs': [],
    ...routes,
  });
}

function renderStrip() {
  return renderWithClient(<TabStrip />);
}

function layer(sid: string, step: string) {
  return restoredLayer({ sid, step, output: 'ds_Sv', variable: 'Sv', label: step, stepHash: 'h', addedAt: 0 });
}

const tabOf = (sid: string) => document.querySelector<HTMLElement>(`.rc-session-tab[data-sid="${sid}"]`)!;

describe('the tab strip', () => {
  beforeEach(() => {
    useView.getState().resetScope();
    useEchogramStore.setState({ layers: [], bySession: {} });
  });

  it('shows a tab per open session, titled, the front one selected, with the dirty mark', async () => {
    const user = userEvent.setup();
    serve();
    openTab('A');
    openTab('B');
    openTab('A');
    renderStrip();
    const survey = await screen.findByRole('tab', { name: 'demo_survey_sv' });
    const dive = screen.getByRole('tab', { name: /^demo_dive_analysis/ });
    expect(screen.getAllByRole('tab')).toEqual([survey, dive]);
    expect(survey).toHaveAttribute('aria-selected', 'true');
    expect(dive).toHaveAttribute('aria-selected', 'false');
    await waitFor(() => expect(within(dive).getByTitle('Unsaved edits')).toHaveTextContent('*'));
    expect(within(survey).queryByTitle('Unsaved edits')).toBeNull();
    expect(screen.getByRole('button', { name: 'Close demo_survey_sv' })).toBeInTheDocument();

    await user.click(dive);
    expect(useSessionStore.getState().sid).toBe('B');
    expect(dive).toHaveAttribute('aria-selected', 'true');
  });

  it('shows a dot from the newest run, and refetches the previews when the run ends', async () => {
    let status: RunRecord['status'] = 'running';
    serve({ 'GET /api/sessions/A/runs': () => [run('r1', status)] });
    openTab('B');
    openTab('A');
    const { client } = renderStrip();
    await waitFor(() =>
      expect(tabOf('A').querySelector('.rc-session-tab-dot-active')).toHaveAttribute('title', 'run going'),
    );
    expect(tabOf('B').querySelector('.rc-session-tab-dot')).toBeNull();

    const invalidate = vi.spyOn(client, 'invalidateQueries');
    status = 'completed';
    await act(() => client.invalidateQueries({ queryKey: queryKeys.runs('A') }));
    await waitFor(() => expect(tabOf('A').querySelector('.rc-session-tab-dot-completed')).not.toBeNull());
    expect(invalidate).toHaveBeenCalledWith({ queryKey: queryKeys.previews('A') });
    expect(invalidate).not.toHaveBeenCalledWith({ queryKey: queryKeys.previews('B') });
  });

  it('closes the tabs of sessions the server no longer lists, and drops their layers', async () => {
    serve({ 'GET /api/sessions': [SUMMARIES[1]] });
    openTab('B');
    openTab('A');
    useEchogramStore.setState({ layers: [layer('A', 'sv'), layer('B', 'mvbs'), layer('C', 'old')] });
    renderStrip();
    await waitFor(() => expect(useTabs.getState().sids).toEqual(['B']));
    expect(useSessionStore.getState().sid).toBe('B');
    expect(useEchogramStore.getState().layers.map((entry) => entry.sid)).toEqual(['B']);
  });

  it('closes the tab of a session that answers 404, and drops its layers', async () => {
    serve({
      'GET /api/sessions': () => new Promise(() => {}),
      'GET /api/sessions/A': () => errorResponse(404, 'No session'),
    });
    openTab('A');
    openTab('B');
    useEchogramStore.setState({ layers: [layer('A', 'sv'), layer('B', 'mvbs')] });
    renderStrip();
    await waitFor(() => expect(useTabs.getState().sids).toEqual(['B']));
    expect(useSessionStore.getState().sid).toBe('B');
    expect(useEchogramStore.getState().layers.map((entry) => entry.sid)).toEqual(['B']);
  });

  it('prunes nothing while the session list fails', async () => {
    serve({ 'GET /api/sessions': () => errorResponse(500, 'down') });
    openTab('A');
    renderStrip();
    await screen.findByRole('tab', { name: 'demo_survey_sv' });
    expect(useTabs.getState().sids).toEqual(['A']);
  });

  it('closes a tab with no confirm, keeping the session and its layers', async () => {
    const user = userEvent.setup();
    const fetchMock = serve();
    const confirm = vi.fn(() => false);
    vi.stubGlobal('confirm', confirm);
    openTab('A');
    openTab('B');
    useEchogramStore.setState({ layers: [layer('A', 'sv')] });
    renderStrip();
    await user.click(await screen.findByRole('button', { name: 'Close demo_survey_sv' }));
    expect(screen.queryByRole('tab', { name: 'demo_survey_sv' })).toBeNull();
    expect(useTabs.getState().sids).toEqual(['B']);
    expect(useSessionStore.getState().sid).toBe('B');
    expect(confirm).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
    expect(useEchogramStore.getState().layers.map((entry) => entry.sid)).toEqual(['A']);
  });
});
