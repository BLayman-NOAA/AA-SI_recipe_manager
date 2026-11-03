import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { act, renderHook, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { EditResponse, SaveAsResult, SessionDocument } from '../src/api/models';
import {
  applyEdit,
  queryKeys,
  STALE_VERSION_MESSAGE,
  useEdit,
  useRedo,
  useStartRun,
  useSync,
  useUndo,
} from '../src/api/queries';
import { historyLabel, setCheckpoint, setParam } from '../src/editing/operations';
import { defaultSaveAsPath, rehashText, SaveAsDialog } from '../src/editing/SaveAsDialog';
import { SaveDialog } from '../src/editing/SaveDialog';
import { useEditingStore } from '../src/editing/store';
import { RUN_ALL } from '../src/runs/intent';
import { useSessionStore } from '../src/state/session';
import {
  apiErrorResponse,
  editableSession,
  makePreview,
  makeSession,
  mockApi,
  previewStep,
  renderWithClient,
  testQueryClient,
} from './helpers';

const SID = makeSession().id;
const BASE = `/api/sessions/${SID}`;
const RUN_ALL_KEY = queryKeys.preview(SID, [], false, false, []);

function wrapperFor(client: QueryClient) {
  return ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function editResponse(session: SessionDocument, changedHashes: string[] | null = ['leaf_add', 'total']): EditResponse {
  return {
    session,
    changed: true,
    changed_hashes: changedHashes,
    entries: [
      {
        seq: 1,
        time: '2026-10-29T12:00:00Z',
        op: setParam('leaf_add', 'k', 7) as unknown as Record<string, unknown>,
        inverse: setParam('leaf_add', 'k', 1) as unknown as Record<string, unknown>,
        documents: ['sub/leaf.yaml'],
      },
    ],
  };
}

/** A client holding the session at a version, a file text and a run all preview. */
function clientWithSession(session = editableSession({ version: 4 })): QueryClient {
  const client = testQueryClient();
  client.setQueryData(queryKeys.session(SID), session);
  client.setQueryData(queryKeys.file(SID, 'sub/leaf.yaml'), { path: 'sub/leaf.yaml', text: 'k: 1\n' });
  client.setQueryData(
    RUN_ALL_KEY,
    makePreview([
      previewStep('seed', { held_by: ['user'] }),
      previewStep('chain_inc'),
      previewStep('leaf_add', { held_by: ['survey'] }),
      previewStep('total'),
    ]),
  );
  return client;
}

function bodyOf(fetchMock: ReturnType<typeof mockApi>, key: string): unknown {
  const call = fetchMock.mock.calls.find(([url, init]) => `${init?.method ?? 'GET'} ${url}` === key);
  return call ? JSON.parse(String(call[1]?.body)) : undefined;
}

beforeEach(() => {
  useEditingStore.getState().reset();
  useSessionStore.getState().setSid(SID);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('operations', () => {
  it('build the wire shape the server reads', () => {
    expect(JSON.stringify(setParam('leaf_add', 'k', 7))).toBe(
      '{"kind":"set_param","step":"leaf_add","name":"k","value":7,"path":[],"position":null}',
    );
    expect(setCheckpoint('total', null)).toEqual({ kind: 'set_checkpoint', step: 'total', value: null, position: null });
  });

  it('label the history items', () => {
    expect(historyLabel('Undo', 'set_param')).toBe('Undo parameter change');
    expect(historyLabel('Redo', 'something_new')).toBe('Redo something new');
    expect(historyLabel('Undo', null)).toBe('Undo');
  });
});

describe('useEdit', () => {
  it('posts the operations with the session version and takes the answer', async () => {
    const client = clientWithSession();
    useEditingStore.getState().setError('an old failure');
    const after = editableSession({ version: 5, dirty: ['sub/leaf.yaml'] });
    const fetchMock = mockApi({ [`POST ${BASE}/edits`]: editResponse(after, ['total']) });
    const { result } = renderHook(() => useEdit(SID), { wrapper: wrapperFor(client) });

    await act(() => result.current.mutateAsync([setParam('leaf_add', 'k', 7)]));

    const call = fetchMock.mock.calls[0];
    expect(call[0]).toBe(`${BASE}/edits`);
    expect(call[1]?.body).toBe(
      '{"operations":[{"kind":"set_param","step":"leaf_add","name":"k","value":7,"path":[],"position":null}],"version":4}',
    );
    const session = client.getQueryData<SessionDocument>(queryKeys.session(SID))!;
    expect(session.version).toBe(5);
    expect(session.dirty).toEqual(['sub/leaf.yaml']);
    expect(client.getQueryState(RUN_ALL_KEY)?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.file(SID, 'sub/leaf.yaml'))?.isInvalidated).toBe(true);
    expect(useEditingStore.getState().error).toBeNull();
    // total is not survey backed, so no warning.
    expect(useEditingStore.getState().surveyWarning).toBeNull();
  });

  it('opens the survey warning when a survey backed step changes hash', async () => {
    const client = clientWithSession();
    mockApi({ [`POST ${BASE}/edits`]: editResponse(editableSession({ version: 5 }), ['leaf_add', 'total']) });
    const { result } = renderHook(() => useEdit(SID), { wrapper: wrapperFor(client) });

    await act(() => result.current.mutateAsync([setParam('leaf_add', 'k', 7)]));

    expect(useEditingStore.getState().surveyWarning).toEqual({ changedHashes: ['leaf_add', 'total'], lost: ['leaf_add'] });
  });

  it('refetches the session on a 409 StaleVersion and says the edit was not applied', async () => {
    const client = clientWithSession();
    mockApi({
      [`POST ${BASE}/edits`]: () => apiErrorResponse(409, 'StaleVersion', 'The session is at version 6.', { version: 6 }),
    });
    const { result } = renderHook(() => useEdit(SID), { wrapper: wrapperFor(client) });

    await act(() => result.current.mutateAsync([setParam('leaf_add', 'k', 7)]).catch(() => {}));

    expect(useEditingStore.getState().error).toBe(STALE_VERSION_MESSAGE);
    expect(client.getQueryState(queryKeys.session(SID))?.isInvalidated).toBe(true);
    expect(client.getQueryState(RUN_ALL_KEY)?.isInvalidated).toBe(false);
  });

  it('leaves other errors to the caller', async () => {
    const client = clientWithSession();
    mockApi({
      [`POST ${BASE}/edits`]: () =>
        apiErrorResponse(422, 'EditRejected', 'k must be a number.', { problems: ['k must be a number.'] }),
    });
    const { result } = renderHook(() => useEdit(SID), { wrapper: wrapperFor(client) });

    await act(() => result.current.mutateAsync([setParam('leaf_add', 'k', 'x')]).catch(() => {}));

    await waitFor(() => expect(result.current.error?.message).toBe('k must be a number.'));
    expect(useEditingStore.getState().error).toBeNull();
    // A refused single operation leaves the session's version as it was.
    expect(client.getQueryState(queryKeys.session(SID))?.isInvalidated).toBe(false);
  });

  it('takes the answer for a session left without opening its warning in the open one', async () => {
    const client = clientWithSession();
    useEditingStore.getState().setError('a failure in the open session');
    useSessionStore.getState().setSid('another-session');
    mockApi({ [`POST ${BASE}/edits`]: editResponse(editableSession({ version: 5 }), ['leaf_add', 'total']) });
    const { result } = renderHook(() => useEdit(SID), { wrapper: wrapperFor(client) });

    await act(() => result.current.mutateAsync([setParam('leaf_add', 'k', 7)]));

    expect(client.getQueryData<SessionDocument>(queryKeys.session(SID))?.version).toBe(5);
    expect(useEditingStore.getState().surveyWarning).toBeNull();
    expect(useEditingStore.getState().error).toBe('a failure in the open session');
  });
});

describe('applyEdit', () => {
  it('returns the answer and takes it', async () => {
    const client = clientWithSession();
    mockApi({ [`POST ${BASE}/edits`]: editResponse(editableSession({ version: 5 }), ['total']) });
    const response = await applyEdit(client, SID, [setCheckpoint('total', 'always')]);
    expect(response?.changed).toBe(true);
    expect(client.getQueryData<SessionDocument>(queryKeys.session(SID))?.version).toBe(5);
  });

  it('shows any failure in the status bar', async () => {
    const client = clientWithSession();
    mockApi({
      [`POST ${BASE}/edits`]: () =>
        apiErrorResponse(422, 'EditRejected', 'collect needs total checkpointed.', { problems: ['collect needs it'] }),
    });
    expect(await applyEdit(client, SID, [setCheckpoint('total', null)])).toBeNull();
    expect(useEditingStore.getState().error).toBe('collect needs total checkpointed.');
  });
});

describe('useUndo and useRedo', () => {
  it('send the version and take the answer', async () => {
    const client = clientWithSession();
    const after = editableSession({ version: 5 });
    const fetchMock = mockApi({ [`POST ${BASE}/undo`]: editResponse(after) });
    const { result } = renderHook(() => useUndo(SID), { wrapper: wrapperFor(client) });

    await act(() => result.current.mutateAsync());

    expect(bodyOf(fetchMock, `POST ${BASE}/undo`)).toEqual({ version: 4 });
    expect(client.getQueryData<SessionDocument>(queryKeys.session(SID))?.version).toBe(5);
    expect(client.getQueryState(RUN_ALL_KEY)?.isInvalidated).toBe(true);
  });

  it('show a failure in the status bar', async () => {
    const client = clientWithSession();
    mockApi({ [`POST ${BASE}/redo`]: () => apiErrorResponse(400, 'WorkspaceError', 'Nothing to redo.') });
    const { result } = renderHook(() => useRedo(SID), { wrapper: wrapperFor(client) });

    await act(() => result.current.mutateAsync().catch(() => {}));

    expect(useEditingStore.getState().error).toBe('Nothing to redo.');
  });
});

describe('useSync', () => {
  async function sync(session: SessionDocument, reloaded: string[]) {
    const client = clientWithSession();
    mockApi({ [`POST ${BASE}/sync`]: { session, reloaded, conflicts: [], entries: [] } });
    const { result } = renderHook(() => useSync(SID), { wrapper: wrapperFor(client) });
    await act(() => result.current.mutateAsync());
    return client;
  }

  it('keeps the file texts when nothing changed', async () => {
    const client = await sync(editableSession({ version: 4 }), []);
    expect(client.getQueryState(queryKeys.file(SID, 'sub/leaf.yaml'))?.isInvalidated).toBe(false);
    expect(client.getQueryState(RUN_ALL_KEY)?.isInvalidated).toBe(false);
  });

  it('refetches them after a reload, its own or one the server made before a run', async () => {
    const reloaded = await sync(editableSession({ version: 5 }), ['sub/leaf.yaml']);
    expect(reloaded.getQueryState(queryKeys.file(SID, 'sub/leaf.yaml'))?.isInvalidated).toBe(true);
    const newer = await sync(editableSession({ version: 6 }), []);
    expect(newer.getQueryState(queryKeys.file(SID, 'sub/leaf.yaml'))?.isInvalidated).toBe(true);
    expect(newer.getQueryData<SessionDocument>(queryKeys.session(SID))?.version).toBe(6);
  });
});

describe('SaveDialog', () => {
  const dirty = editableSession({ version: 4, dirty: ['sub/leaf.yaml'] });

  it('lists the edited files and saves them', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const fetchMock = mockApi({
      [`POST ${BASE}/save`]: { session: editableSession({ version: 5 }), written: ['C:/recipes/sub/leaf.yaml'] },
    });
    const { client } = renderWithClient(<SaveDialog session={dirty} onClose={onClose} />, clientWithSession(dirty));

    const dialog = screen.getByRole('dialog', { name: 'Save' });
    expect(within(dialog).getByText('sub/leaf.yaml')).toBeInTheDocument();
    expect(within(dialog).getByText('C:/recipes/sub/leaf.yaml')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(bodyOf(fetchMock, `POST ${BASE}/save`)).toEqual({ overwrite: false });
    expect(useEditingStore.getState().notice).toBe('Saved 1 file');
    expect(client.getQueryData<SessionDocument>(queryKeys.session(SID))?.dirty).toEqual([]);
  });

  it('offers Overwrite on a 409 ChangedOnDisk', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    let first = true;
    const fetchMock = mockApi({
      [`POST ${BASE}/save`]: () => {
        if (!first) return { session: editableSession({ version: 6 }), written: ['C:/recipes/sub/leaf.yaml'] };
        first = false;
        return apiErrorResponse(409, 'ChangedOnDisk', 'Changed on disk.', { paths: ['C:/recipes/sub/leaf.yaml'] });
      },
    });
    renderWithClient(<SaveDialog session={dirty} onClose={onClose} />, clientWithSession(dirty));

    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('These files changed on disk since the session read them:')).toBeInTheDocument();
    expect(screen.getByText('sub/leaf.yaml')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Overwrite' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const bodies = fetchMock.mock.calls
      .filter(([url]) => url === `${BASE}/save`)
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies).toEqual([{ overwrite: false }, { overwrite: true }]);
  });

  it('reloads the changed files from disk instead', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const fetchMock = mockApi({
      [`POST ${BASE}/save`]: () =>
        apiErrorResponse(409, 'ChangedOnDisk', 'Changed on disk.', { paths: ['C:/recipes/sub/leaf.yaml'] }),
      [`POST ${BASE}/reload`]: editResponse(editableSession({ version: 5 })),
    });
    renderWithClient(<SaveDialog session={dirty} onClose={onClose} />, clientWithSession(dirty));

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await user.click(await screen.findByRole('button', { name: 'Reload from disk' }));

    expect(onClose).toHaveBeenCalled();
    await waitFor(() => expect(bodyOf(fetchMock, `POST ${BASE}/reload`)).toEqual({ files: ['sub/leaf.yaml'], version: 4 }));
  });

  it('cancels without saving', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const fetchMock = mockApi({});
    renderWithClient(<SaveDialog session={dirty} onClose={onClose} />, clientWithSession(dirty));
    await user.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(onClose).toHaveBeenCalled();
    expect(fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST')).toEqual([]);
  });
});

describe('SaveAsDialog', () => {
  const session = editableSession({ version: 4 });
  const dryRun: SaveAsResult = {
    root: 'C:/elsewhere/with_sub_copy.yaml',
    written: ['C:/elsewhere/with_sub_copy.yaml', 'C:/elsewhere/sub/chain.yaml', 'C:/elsewhere/sub/leaf.yaml'],
    rewrites: {
      'with_sub_copy.yaml': [{ where: 'steps.seed.params.path', old: './data', new: '../recipes/data', absolute: false }],
    },
    absolute: ['C:/data/raw'],
    config: 'C:/elsewhere/with_sub_copy.config.yaml',
    rehashed: ['seed', 'chain_inc', 'leaf_add', 'total'],
    existing: [],
    dry_run: true,
  };

  it('offers a copy beside the original', () => {
    expect(defaultSaveAsPath('C:/recipes/with_sub.yaml')).toBe('C:/recipes/with_sub_copy.yaml');
    expect(rehashText([], 4)).toBe('All steps hash as in the original.');
    expect(rehashText(['seed'], 4)).toBe('1 of 4 steps hash differently from the original and start a new cache: seed.');
  });

  it('previews the rewrites, the rehashed steps and the survey hits', async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({ [`POST ${BASE}/save-as`]: { session, result: dryRun } });
    renderWithClient(<SaveAsDialog session={session} onClose={() => {}} />, clientWithSession(session));

    const path = screen.getByRole('textbox', { name: 'Save As path' });
    expect(path).toHaveValue('C:/recipes/with_sub_copy.yaml');
    expect(screen.getByText('Beside the original, edited sub-recipes are written in place.')).toBeInTheDocument();
    const keep = screen.getByRole('checkbox', { name: /Keep relative paths/ });
    expect(keep).toBeDisabled();
    await user.clear(path);
    await user.type(path, 'C:/elsewhere/with_sub_copy.yaml');
    expect(screen.getByText('The copy runs from its own folder.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Preview' }));

    expect(await screen.findByText('steps.seed.params.path')).toBeInTheDocument();
    expect(screen.getByText('../recipes/data')).toBeInTheDocument();
    expect(screen.getByText('The run config is copied to C:/elsewhere/with_sub_copy.config.yaml.')).toBeInTheDocument();
    expect(screen.getByText('Absolute paths the copy keeps as written: C:/data/raw.')).toBeInTheDocument();
    expect(
      screen.getByText(
        '4 of 4 steps hash differently from the original and start a new cache: seed, chain_inc, leaf_add, total.',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText('1 survey backed step among them would be recomputed in your user cache: leaf_add.'),
    ).toBeInTheDocument();
    expect(keep).toBeEnabled();
    expect(bodyOf(fetchMock, `POST ${BASE}/save-as`)).toEqual({
      path: 'C:/elsewhere/with_sub_copy.yaml',
      keep_paths: false,
      copy_config: true,
      overwrite: false,
      dry_run: true,
    });
  });

  it('ticks Overwrite on a 409 TargetExists and saves when asked again', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const saved = { ...dryRun, root: 'C:/recipes/with_sub_copy.yaml', dry_run: false };
    const fetchMock = mockApi({
      [`POST ${BASE}/save-as`]: (init: RequestInit | undefined) => {
        const body = JSON.parse(String(init?.body));
        if (!body.overwrite) {
          return apiErrorResponse(409, 'TargetExists', 'Exists.', { paths: ['C:/recipes/with_sub_copy.yaml'] });
        }
        return { session, result: saved };
      },
      'GET /api/sessions': [],
      'GET /api/recipes': { roots: [], recipes: [] },
    });
    renderWithClient(<SaveAsDialog session={session} onClose={onClose} />, clientWithSession(session));

    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Would replace: C:/recipes/with_sub_copy.yaml.')).toBeInTheDocument();
    expect(screen.getByRole('checkbox', { name: 'Overwrite' })).toBeChecked();
    expect(onClose).not.toHaveBeenCalled();
    await user.click(screen.getByRole('button', { name: 'Save' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    const bodies = fetchMock.mock.calls
      .filter(([url]) => url === `${BASE}/save-as`)
      .map(([, init]) => JSON.parse(String(init?.body)).overwrite);
    expect(bodies).toEqual([false, true]);
    expect(useEditingStore.getState().notice).toBe('Saved as C:/recipes/with_sub_copy.yaml');
  });

  it('asks again before overwriting once the path changes', async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const fetchMock = mockApi({
      [`POST ${BASE}/save-as`]: (init: RequestInit | undefined) => {
        const body = JSON.parse(String(init?.body));
        if (!body.overwrite) return apiErrorResponse(409, 'TargetExists', 'Exists.', { paths: [body.path] });
        return { session, result: { ...dryRun, root: body.path, dry_run: false } };
      },
      'GET /api/sessions': [],
      'GET /api/recipes': { roots: [], recipes: [] },
    });
    renderWithClient(<SaveAsDialog session={session} onClose={onClose} />, clientWithSession(session));

    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Would replace: C:/recipes/with_sub_copy.yaml.')).toBeInTheDocument();
    const path = screen.getByRole('textbox', { name: 'Save As path' });
    await user.clear(path);
    await user.type(path, 'C:/recipes/other.yaml');
    expect(screen.queryByRole('checkbox', { name: 'Overwrite' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Save' }));

    expect(await screen.findByText('Would replace: C:/recipes/other.yaml.')).toBeInTheDocument();
    expect(onClose).not.toHaveBeenCalled();
    const bodies = fetchMock.mock.calls
      .filter(([url]) => url === `${BASE}/save-as`)
      .map(([, init]) => JSON.parse(String(init?.body)));
    expect(bodies.map((body) => [body.path, body.overwrite])).toEqual([
      ['C:/recipes/with_sub_copy.yaml', false],
      ['C:/recipes/other.yaml', false],
    ]);
  });

  it("turns Save off for the recipe's own file, and shows the server's refusal inline", async () => {
    const user = userEvent.setup();
    mockApi({
      [`POST ${BASE}/save-as`]: () =>
        apiErrorResponse(400, 'WorkspaceError', "'WITH_SUB.yaml' is the recipe's own file; use Save."),
    });
    renderWithClient(<SaveAsDialog session={session} onClose={() => {}} />, clientWithSession(session));

    const path = screen.getByRole('textbox', { name: 'Save As path' });
    await user.clear(path);
    await user.type(path, 'C:\\recipes\\with_sub.yaml');
    expect(screen.getByText("'with_sub.yaml' is the recipe's own file; use Save.")).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Preview' })).toBeDisabled();

    await user.clear(path);
    await user.type(path, 'C:/recipes/WITH_SUB.yaml');
    await user.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText("'WITH_SUB.yaml' is the recipe's own file; use Save.")).toBeInTheDocument();
    expect(screen.getByRole('dialog', { name: 'Save As' })).toBeInTheDocument();
  });
});

describe('a session answer older than the one held', () => {
  it('is not taken', async () => {
    const client = clientWithSession(editableSession({ version: 6 }));
    mockApi({
      [`POST ${BASE}/sync`]: {
        session: editableSession({ version: 5 }),
        reloaded: ['sub/leaf.yaml'],
        conflicts: [],
        entries: [],
      },
    });
    const { result } = renderHook(() => useSync(SID), { wrapper: wrapperFor(client) });
    await act(() => result.current.mutateAsync());
    expect(client.getQueryData<SessionDocument>(queryKeys.session(SID))?.version).toBe(6);
  });
});

describe('an edit that fails', () => {
  it('refetches the session, whose version a batch failing part way has moved', async () => {
    const client = clientWithSession();
    mockApi({
      [`POST ${BASE}/edits`]: () => apiErrorResponse(422, 'EditRejected', 'bad', { problems: ['bad'] }),
    });
    const { result } = renderHook(() => useEdit(SID), { wrapper: wrapperFor(client) });
    await act(() =>
      result.current.mutateAsync([setParam('leaf_add', 'k', 7), setParam('leaf_add', 'k', 'x')]).catch(() => {}),
    );
    expect(client.getQueryState(queryKeys.session(SID))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.file(SID, 'sub/leaf.yaml'))?.isInvalidated).toBe(false);
  });
});

describe('files the server reloads from disk itself', () => {
  it('are named after a save', async () => {
    const user = userEvent.setup();
    const dirty = editableSession({ version: 4, dirty: ['sub/leaf.yaml'] });
    mockApi({
      [`POST ${BASE}/save`]: {
        session: editableSession({ version: 6 }),
        written: ['C:/recipes/sub/leaf.yaml'],
        reloaded: ['sub/chain.yaml'],
      },
    });
    renderWithClient(<SaveDialog session={dirty} onClose={() => {}} />, clientWithSession(dirty));
    await user.click(within(screen.getByRole('dialog', { name: 'Save' })).getByRole('button', { name: 'Save' }));
    await waitFor(() =>
      expect(useEditingStore.getState().notice).toBe('Saved 1 file; sub/chain.yaml reloaded from disk'),
    );
  });

  it('are named after a run starts, and the session and its texts are refetched', async () => {
    const client = clientWithSession();
    mockApi({ [`POST ${BASE}/runs`]: { id: 'r1', session: SID, status: 'starting', reloaded: ['sub/leaf.yaml'] } });
    const { result } = renderHook(() => useStartRun(SID), { wrapper: wrapperFor(client) });
    await act(() => result.current.mutateAsync(RUN_ALL));
    expect(useEditingStore.getState().notice).toBe('sub/leaf.yaml reloaded from disk');
    expect(client.getQueryState(queryKeys.session(SID))?.isInvalidated).toBe(true);
    expect(client.getQueryState(queryKeys.file(SID, 'sub/leaf.yaml'))?.isInvalidated).toBe(true);
    expect(client.getQueryState(RUN_ALL_KEY)?.isInvalidated).toBe(true);
  });

  it('leave the session alone when there are none', async () => {
    const client = clientWithSession();
    mockApi({ [`POST ${BASE}/runs`]: { id: 'r1', session: SID, status: 'starting', reloaded: [] } });
    const { result } = renderHook(() => useStartRun(SID), { wrapper: wrapperFor(client) });
    await act(() => result.current.mutateAsync(RUN_ALL));
    expect(useEditingStore.getState().notice).toBeNull();
    expect(client.getQueryState(queryKeys.session(SID))?.isInvalidated).toBe(false);
  });
});
