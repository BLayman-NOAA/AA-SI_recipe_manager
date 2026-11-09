import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RecipesResponse, SessionSummary } from '../src/api/models';
import { restoredLayer } from '../src/echogram/layers';
import { useEchogramStore } from '../src/echogram/store';
import {
  groupRecipes,
  latestSessionFor,
  parentsOf,
  sessionFolder,
  shortPath,
  tabOn,
} from '../src/recipes/recipeList';
import { RecipesPane } from '../src/recipes/RecipesPane';
import { useSessionStore } from '../src/state/session';
import { useTabs } from '../src/state/tabs';
import { apiErrorResponse, makeSession, mockApi, renderWithClient } from './helpers';

const RECIPES: RecipesResponse = {
  roots: ['C:/work/a', 'C:/work/b'],
  recipes: [
    {
      name: 'client_with_sub',
      description: 'Seed, nested sub-recipe, sum. Then more text.',
      version: '1.0',
      path: 'C:/work/a/with_sub.yaml',
      root: 'C:/work/a',
      relative: 'with_sub.yaml',
      modified: '2026-10-13T15:30:12Z',
      included_by: [],
    },
    {
      name: 'client_chain',
      description: null,
      version: '1.0',
      path: 'C:/work/a/sub/chain.yaml',
      root: 'C:/work/a',
      relative: 'sub/chain.yaml',
      modified: '2026-10-13T15:30:12Z',
      included_by: ['C:/work/a/with_sub.yaml'],
    },
    {
      name: 'dive_analysis',
      description: 'HB1603 dives over ``depth``.',
      version: '1.0',
      path: 'C:/work/b/HB1603/dive_analysis.yaml',
      root: 'C:/work/b',
      relative: 'HB1603/dive_analysis.yaml',
      modified: '2026-10-13T15:30:12Z',
      included_by: [],
    },
    {
      name: 'broken',
      description: null,
      version: null,
      path: 'C:/work/b/broken.yaml',
      root: 'C:/work/b',
      relative: 'broken.yaml',
      modified: '2026-10-13T15:30:12Z',
      included_by: [],
      error: 'mapping values are not allowed here (line 3)',
    },
  ],
};

const SESSION: SessionSummary = {
  id: 'older',
  created: '2026-10-13T10:00:00Z',
  origin: 'C:/work/b/HB1603/dive_analysis.yaml',
  title: 'dive_analysis',
};

beforeEach(() => {
  useTabs.setState({ sids: [], pendingSelection: null });
});

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().setSid(null);
});

describe('recipe list helpers', () => {
  it('groups by root and filters by name, path or description', () => {
    expect(groupRecipes(RECIPES, '').map((group) => group.recipes.length)).toEqual([2, 2]);
    expect(groupRecipes(RECIPES, 'hb1603').map((group) => group.root)).toEqual(['C:/work/b']);
    expect(groupRecipes(RECIPES, 'sub/').flatMap((g) => g.recipes.map((r) => r.name))).toEqual([
      'client_chain',
    ]);
  });

  it('hides sub-recipes unless asked', () => {
    const names = groupRecipes(RECIPES, '', false).flatMap((g) => g.recipes.map((r) => r.name));
    expect(names).not.toContain('client_chain');
  });

  it('shortens roots and finds sessions, folders and parents', () => {
    expect(shortPath('C:/Users/me/AA-SI/example_recipes')).toBe('AA-SI/example_recipes');
    const newer = { ...SESSION, id: 'newer', created: '2026-10-13T12:00:00Z' };
    expect(latestSessionFor([SESSION, newer], SESSION.origin)?.id).toBe('newer');
    expect(latestSessionFor([SESSION], 'C:/other.yaml')).toBeUndefined();
    expect(sessionFolder(SESSION, RECIPES.recipes)).toBe('HB1603');
    expect(sessionFolder({ ...SESSION, origin: 'D:/x/y/z/r.yaml' }, RECIPES.recipes)).toBe('y/z');
    expect(parentsOf('C:/work/a/sub/chain.yaml', RECIPES.recipes).map((r) => r.name)).toEqual([
      'client_with_sub',
    ]);
  });

  it('finds the first open tab on a recipe, in strip order', () => {
    const newer = { ...SESSION, id: 'newer', created: '2026-10-13T12:00:00Z' };
    const other = { ...SESSION, id: 'other', origin: 'C:/other.yaml' };
    const sessions = [newer, SESSION, other];
    expect(tabOn(sessions, ['other', 'older', 'newer'], SESSION.origin)?.id).toBe('older');
    expect(tabOn(sessions, ['newer', 'older'], SESSION.origin)?.id).toBe('newer');
    expect(tabOn(sessions, ['other', 'gone'], SESSION.origin)).toBeUndefined();
    expect(tabOn([], ['older'], SESSION.origin)).toBeUndefined();
  });
});

describe('RecipesPane', () => {
  function serve(sessions: SessionSummary[] = []) {
    return mockApi({
      'GET /api/recipes': RECIPES,
      'GET /api/sessions': sessions,
      'POST /api/sessions': makeSession({ origin: 'C:/work/a/with_sub.yaml' }),
    });
  }

  const posts = (fetchMock: ReturnType<typeof mockApi>) =>
    fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');

  it('lists recipes under short root labels, with a description line and no sub-recipes', async () => {
    serve();
    renderWithClient(<RecipesPane />);
    await screen.findByText('client_with_sub');
    expect(screen.getByRole('heading', { name: 'work/a' })).toHaveAttribute('title', 'C:/work/a');
    expect(screen.getByText('Seed, nested sub-recipe, sum.')).toBeInTheDocument();
    expect(screen.getByText('depth').tagName).toBe('CODE');
    expect(screen.queryByText('client_chain')).toBeNull();

    await userEvent.setup().click(screen.getByLabelText('Show sub-recipes'));
    expect(screen.getByText('client_chain')).toBeInTheDocument();
    expect(window.localStorage.getItem('rc.recipes.subRecipes')).toBe('true');
  });

  it('opens a recipe once on a double click of Open', async () => {
    const user = userEvent.setup();
    const fetchMock = serve();
    renderWithClient(<RecipesPane />);
    const row = (await screen.findByText('client_with_sub')).closest('li')!;
    await user.dblClick(within(row).getByRole('button', { name: 'Open' }));

    await waitFor(() => expect(useSessionStore.getState().sid).toBe(makeSession().id));
    expect(posts(fetchMock)).toHaveLength(1);
    expect(JSON.parse(String(posts(fetchMock)[0][1]!.body))).toEqual({ path: 'C:/work/a/with_sub.yaml' });
  });

  it('switches to an existing session, and New session starts another', async () => {
    const user = userEvent.setup();
    const fetchMock = serve([SESSION]);
    renderWithClient(<RecipesPane />);
    // The session row shows the recipe folder relative to its root.
    expect(await screen.findByText('HB1603')).toBeInTheDocument();
    const recipeRow = (await screen.findByText('HB1603/dive_analysis.yaml')).closest('li')!;

    await user.click(within(recipeRow).getByRole('button', { name: 'Open' }));
    expect(useSessionStore.getState().sid).toBe('older');
    expect(posts(fetchMock)).toHaveLength(0);

    await user.click(within(recipeRow).getByRole('button', { name: 'New session' }));
    await waitFor(() => expect(posts(fetchMock)).toHaveLength(1));
    expect(useTabs.getState().sids).toEqual(['older', makeSession().id]);
    expect(useSessionStore.getState().sid).toBe(makeSession().id);
  });

  it('focuses a tab already open on the recipe before its newest session, without a POST', async () => {
    const user = userEvent.setup();
    const newer = { ...SESSION, id: 'newer', created: '2026-10-13T12:00:00Z' };
    const fetchMock = serve([newer, SESSION]);
    useTabs.setState({ sids: ['older', 'other'] });
    useSessionStore.getState().setSid('other');
    renderWithClient(<RecipesPane />);
    const recipeRow = (await screen.findByText('HB1603/dive_analysis.yaml')).closest('li')!;
    await user.click(within(recipeRow).getByRole('button', { name: 'Open' }));
    expect(useSessionStore.getState().sid).toBe('older');
    expect(useTabs.getState().sids).toEqual(['older', 'other']);
    expect(posts(fetchMock)).toHaveLength(0);
  });

  it('reopens a recent session in a tab', async () => {
    const user = userEvent.setup();
    serve([SESSION]);
    renderWithClient(<RecipesPane />);
    await user.click(await screen.findByRole('button', { name: 'Reopen' }));
    expect(useTabs.getState().sids).toEqual(['older']);
    expect(useSessionStore.getState().sid).toBe('older');
    expect(screen.getByRole('button', { name: 'Reopen' })).toBeDisabled();
  });

  it('shows a recipe that does not parse with its message and no Open', async () => {
    serve();
    renderWithClient(<RecipesPane />);
    const row = (await screen.findByText('broken')).closest('li')!;
    expect(within(row).getByText('does not parse')).toBeInTheDocument();
    expect(within(row).getByText(/mapping values are not allowed/)).toBeInTheDocument();
    expect(within(row).queryByRole('button')).toBeNull();
  });

  it('deletes a session only after a confirm', async () => {
    const user = userEvent.setup();
    const fetchMock = serve([SESSION]);
    const confirm = vi.fn(() => false);
    vi.stubGlobal('confirm', confirm);
    renderWithClient(<RecipesPane />);
    await user.click(await screen.findByRole('button', { name: 'Delete session' }));
    expect(confirm).toHaveBeenCalled();
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === 'DELETE')).toBe(false);
  });

  it('deletes a session after a confirm naming its layers, closing its tab and removing them', async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({
      'GET /api/recipes': RECIPES,
      'GET /api/sessions': [SESSION],
      'DELETE /api/sessions/older': null,
    });
    const confirm = vi.fn(() => true);
    vi.stubGlobal('confirm', confirm);
    useTabs.setState({ sids: ['older'] });
    useSessionStore.getState().setSid('older');
    const saved = { sid: 'older', step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 };
    useEchogramStore.setState({ layers: [restoredLayer(saved), restoredLayer({ ...saved, sid: 'x' })] });
    renderWithClient(<RecipesPane />);
    await user.click(await screen.findByRole('button', { name: 'Delete session' }));
    expect(confirm).toHaveBeenCalledWith(
      'Delete the session "dive_analysis"? Its saved inputs and settings are removed, and 1 echogram layer with them.',
    );
    await waitFor(() => expect(useEchogramStore.getState().layers.map((layer) => layer.sid)).toEqual(['x']));
    expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/sessions/older' && init?.method === 'DELETE')).toBe(true);
    expect(useTabs.getState().sids).toEqual([]);
    expect(useSessionStore.getState().sid).toBeNull();
    useEchogramStore.setState({ layers: [] });
  });

  it("keeps the session's tab and layers and says why when the delete is refused", async () => {
    const user = userEvent.setup();
    const message = "Session 'older' has an unfinished run r1; cancel it first.";
    mockApi({
      'GET /api/recipes': RECIPES,
      'GET /api/sessions': [SESSION],
      'DELETE /api/sessions/older': () => apiErrorResponse(409, 'ActiveRunExists', message),
    });
    vi.stubGlobal('confirm', vi.fn(() => true));
    useTabs.setState({ sids: ['older'] });
    useSessionStore.getState().setSid('older');
    const saved = { sid: 'older', step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 };
    useEchogramStore.setState({ layers: [restoredLayer(saved)] });
    renderWithClient(<RecipesPane />);
    await user.click(await screen.findByRole('button', { name: 'Delete session' }));
    expect(
      await screen.findByText(
        'Could not delete dive_analysis: a run is going in it. Cancel the run or wait for it to finish.',
      ),
    ).toHaveClass('rc-field-error');
    expect(screen.queryByText(/unfinished run/)).toBeNull();
    expect(useTabs.getState().sids).toEqual(['older']);
    expect(useSessionStore.getState().sid).toBe('older');
    expect(useEchogramStore.getState().layers.map((layer) => layer.sid)).toEqual(['older']);
    useEchogramStore.setState({ layers: [] });
  });
});
