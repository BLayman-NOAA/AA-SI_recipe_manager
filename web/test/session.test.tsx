import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NETWORK_ERROR_MESSAGE } from '../src/api/client';
import { fileUrl, queryKeys } from '../src/api/queries';
import { App } from '../src/App';
import { Diagram } from '../src/diagram/Diagram';
import { RecipesPane } from '../src/recipes/RecipesPane';
import { StatusBar } from '../src/shell/StatusBar';
import { useSessionStore } from '../src/state/session';
import { errorResponse, makeSession, mockApi, renderWithClient, testQueryClient } from './helpers';

const SID = '20261013-153012-a1b2c3';
const HEALTH = {
  status: 'ok',
  version: '9.8.7',
  dev: true,
  state_dir: '/state',
  docs_url: 'https://docs.example.org/',
};

beforeEach(() => {
  useSessionStore.getState().setSid(SID);
});

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().setSid(null);
});

describe('a session that fails to load', () => {
  it('shows the error in the status bar and the diagram', async () => {
    mockApi({
      'GET /api/health': HEALTH,
      [`GET /api/sessions/${SID}`]: () => errorResponse(409, 'Session state is unreadable'),
    });
    renderWithClient(
      <>
        <StatusBar />
        <Diagram />
      </>,
    );
    expect(await screen.findByText('session: Session state is unreadable')).toBeInTheDocument();
    expect(screen.getByText(/Could not load the session: Session state is unreadable/)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'docs' })).toHaveAttribute('href', HEALTH.docs_url);
  });

  it('is fetched again when the server answers a health check after a network failure', async () => {
    let fail = true;
    mockApi({
      'GET /api/health': HEALTH,
      'GET /api/sessions': [],
      'GET /api/recipes': { roots: [], recipes: [] },
      'GET /api/ops': { schema: 1, counts: { ops: 0, implementations: 0, sources_resolved: 0 }, categories: [], ops: [] },
      [`GET /api/sessions/${SID}`]: () => {
        if (fail) throw new TypeError('Failed to fetch');
        return makeSession();
      },
    });
    const client = testQueryClient();
    render(<App client={client} />);
    expect(await screen.findByText('session: ' + NETWORK_ERROR_MESSAGE)).toBeInTheDocument();

    fail = false;
    await client.invalidateQueries({ queryKey: queryKeys.health });
    await waitFor(() => expect(screen.getByText('session: with_sub (read only)')).toBeInTheDocument());
  });

  it('is dropped when the server no longer has it', async () => {
    mockApi({ 'GET /api/health': HEALTH, [`GET /api/sessions/${SID}`]: () => errorResponse(404, 'No session') });
    render(<App client={testQueryClient()} />);
    await waitFor(() => expect(useSessionStore.getState().sid).toBeNull());
  });
});

describe('fileUrl', () => {
  it('encodes the whole path, slashes included', () => {
    expect(fileUrl('s1', 'sub dir/a#b.yaml')).toBe('/api/sessions/s1/files/sub%20dir%2Fa%23b.yaml');
    expect(fileUrl('s1', '../shared/x.yaml')).toBe('/api/sessions/s1/files/..%2Fshared%2Fx.yaml');
  });

  it('keeps a path outside the recipe folder through URL parsing', () => {
    const url = new URL(fileUrl('s1', '../shared/x.yaml'), 'http://127.0.0.1:8765');
    expect(url.pathname).toBe('/api/sessions/s1/files/..%2Fshared%2Fx.yaml');
  });
});

describe('opening a recipe', () => {
  it('says which recipe is opening in the status bar and the diagram', async () => {
    useSessionStore.getState().setSid(null);
    mockApi({
      'GET /api/health': HEALTH,
      'GET /api/sessions': [],
      'GET /api/recipes': {
        roots: ['C:/r'],
        recipes: [
          {
            name: 'client_with_sub',
            description: null,
            version: '1.0',
            path: 'C:/r/with_sub.yaml',
            root: 'C:/r',
            relative: 'with_sub.yaml',
            modified: '2026-10-13T15:30:12Z',
            included_by: [],
          },
        ],
      },
      'POST /api/sessions': () => new Promise(() => {}),
    });
    renderWithClient(
      <>
        <RecipesPane />
        <StatusBar />
        <Diagram />
      </>,
    );
    await userEvent.setup().click(await screen.findByRole('button', { name: 'Open' }));
    expect(await screen.findAllByText('Opening client_with_sub...')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'Open' })).toBeDisabled();
  });
});
