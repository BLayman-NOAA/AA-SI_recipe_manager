import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DiskConflict, SessionDocument, SyncResponse } from '../src/api/models';
import { useCurrentSession, useEdit } from '../src/api/queries';
import { DiskBanner } from '../src/editing/DiskBanner';
import { setParam } from '../src/editing/operations';
import { NOTICE_MS, reloadNotice, useEditingStore } from '../src/editing/store';
import { DISK_SYNC_MS, useDiskSync } from '../src/editing/useDiskSync';
import { useDialogs } from '../src/shell/dialogs';
import { addPopoutWindow, removePopoutWindow } from '../src/shell/dock';
import { ShellDialogs } from '../src/shell/ShellDialogs';
import { StatusBar } from '../src/shell/StatusBar';
import { useSessionStore } from '../src/state/session';
import { apiErrorResponse, editableSession, makeSession, mockApi, renderWithClient } from './helpers';

const SID = makeSession().id;
const BASE = `/api/sessions/${SID}`;
const HEALTH = { status: 'ok', version: '9.8.7', dev: true, state_dir: '/state', docs_url: 'https://docs.example.org/' };

function syncAnswer(session: SessionDocument, reloaded: string[] = []): SyncResponse {
  return { session, reloaded, conflicts: session.conflicts, entries: [] };
}

function conflict(digest: string, error: string | null = null): DiskConflict {
  return { path: 'sub/leaf.yaml', error, disk_sha256: digest };
}

/** The disk sync with what it feeds: the banner, the status bar and the dialogs. */
function Harness() {
  useDiskSync(SID);
  const session = useCurrentSession().data;
  if (!session) return null;
  return (
    <>
      <DiskBanner sid={SID} conflicts={session.conflicts} />
      <StatusBar />
      <ShellDialogs />
    </>
  );
}

/** Answer each sync with the next session in a list (the last one repeats), counting the posts. */
function serve(sessions: SessionDocument[], reloaded: string[][] = [], extra: Record<string, unknown> = {}) {
  let count = 0;
  const fetchMock = mockApi({
    'GET /api/health': HEALTH,
    [`GET ${BASE}`]: sessions[0],
    [`GET ${BASE}/runs`]: [],
    [`POST ${BASE}/sync`]: () => {
      const index = Math.min(count, sessions.length - 1);
      const answer = syncAnswer(sessions[index], reloaded[count] ?? []);
      count += 1;
      return answer;
    },
    ...extra,
  });
  return { fetchMock, posts: () => count };
}

beforeEach(() => {
  useSessionStore.getState().setSid(SID);
  useEditingStore.getState().reset();
  useDialogs.getState().close();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
  useSessionStore.getState().setSid(null);
});

describe('the disk sync', () => {
  it('posts a sync when the session opens and when the window gains focus', async () => {
    const { posts } = serve([editableSession()]);
    renderWithClient(<Harness />);
    await waitFor(() => expect(posts()).toBe(1));
    fireEvent.focus(window);
    await waitFor(() => expect(posts()).toBe(2));
  });

  it('posts a sync when a popout window gains focus or becomes visible', async () => {
    // An iframe stands in for the popout window dockview opens.
    const frame = document.createElement('iframe');
    document.body.append(frame);
    const popout = frame.contentWindow!;
    const { posts } = serve([editableSession()]);
    addPopoutWindow(popout);
    try {
      renderWithClient(<Harness />);
      await waitFor(() => expect(posts()).toBe(1));
      fireEvent.focus(popout);
      await waitFor(() => expect(posts()).toBe(2));
      fireEvent(popout.document, new Event('visibilitychange'));
      await waitFor(() => expect(posts()).toBe(3));
      removePopoutWindow(popout);
      fireEvent.focus(popout);
      fireEvent.focus(window);
      await waitFor(() => expect(posts()).toBe(4));
    } finally {
      removePopoutWindow(popout);
      frame.remove();
    }
  });

  it('posts again every DISK_SYNC_MS while the window is visible', async () => {
    // Faking setInterval would stop waitFor, which polls with it; take the hook's timer instead.
    const setInterval = vi.spyOn(window, 'setInterval');
    const { posts } = serve([editableSession()]);
    renderWithClient(<Harness />);
    await waitFor(() => expect(posts()).toBe(1));
    const tick = setInterval.mock.calls.find(([, delay]) => delay === DISK_SYNC_MS)?.[0] as () => void;
    act(() => tick());
    await waitFor(() => expect(posts()).toBe(2));
    setInterval.mockRestore();
  });

  it('shows a notice for the files it reloaded', async () => {
    serve([editableSession({ version: 1 })], [['sub/leaf.yaml']]);
    renderWithClient(<Harness />);
    expect(await screen.findByText('sub/leaf.yaml reloaded from disk')).toBeInTheDocument();
    expect(reloadNotice(['a.yaml', 'b.yaml'])).toBe('2 files reloaded from disk: a.yaml, b.yaml');
  });

  it('shows a conflict in the banner; Keep hides it until the disk changes again', async () => {
    const user = userEvent.setup();
    const kept = editableSession({ dirty: ['sub/leaf.yaml'], conflicts: [conflict('aaa')] });
    const changedAgain = editableSession({ dirty: ['sub/leaf.yaml'], conflicts: [conflict('bbb')] });
    const { posts } = serve([kept, kept, changedAgain]);
    renderWithClient(<Harness />);

    expect(await screen.findByText('sub/leaf.yaml changed on disk and has unsaved edits here.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload from disk' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: "Keep the session's version" }));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    expect(screen.getByText('1 file differs from disk')).toBeInTheDocument();

    // The same disk text again: still kept.
    fireEvent.focus(window);
    await waitFor(() => expect(posts()).toBe(2));
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
    // A new disk text: the banner is back.
    fireEvent.focus(window);
    expect(await screen.findByText('sub/leaf.yaml changed on disk and has unsaved edits here.')).toBeInTheDocument();
    expect(screen.queryByText('1 file differs from disk')).not.toBeInTheDocument();
  });

  it('names the error of a disk text that does not load, with no reload', async () => {
    serve([editableSession({ conflicts: [conflict('ccc', "Line 3: mapping values are not allowed here.")] })]);
    renderWithClient(<Harness />);
    expect(
      await screen.findByText(
        'sub/leaf.yaml changed on disk but does not load: Line 3: mapping values are not allowed here.',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reload from disk' })).not.toBeInTheDocument();
  });

  it('reloads a conflict from the banner', async () => {
    const user = userEvent.setup();
    const session = editableSession({ version: 3, dirty: ['sub/leaf.yaml'], conflicts: [conflict('aaa')] });
    const { fetchMock } = serve([session], [], {
      [`POST ${BASE}/reload`]: {
        session: editableSession({ version: 4 }),
        changed: true,
        changed_hashes: ['leaf_add', 'total'],
        entries: [],
      },
    });
    renderWithClient(<Harness />);
    await user.click(await screen.findByRole('button', { name: 'Reload from disk' }));
    await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
    const call = fetchMock.mock.calls.find(([url]) => url === `${BASE}/reload`);
    expect(JSON.parse(String(call?.[1]?.body))).toEqual({ files: ['sub/leaf.yaml'], version: 3 });
  });

  it('after Keep, Save offers Overwrite and writes the session version', async () => {
    const user = userEvent.setup();
    const session = editableSession({ version: 3, dirty: ['sub/leaf.yaml'], conflicts: [conflict('aaa')] });
    const saves: boolean[] = [];
    serve([session], [], {
      [`POST ${BASE}/save`]: (init: RequestInit | undefined) => {
        const overwrite = JSON.parse(String(init?.body)).overwrite as boolean;
        saves.push(overwrite);
        if (!overwrite) {
          return apiErrorResponse(409, 'ChangedOnDisk', 'Changed on disk.', { paths: ['C:/recipes/sub/leaf.yaml'] });
        }
        return { session: editableSession({ version: 4 }), written: ['C:/recipes/sub/leaf.yaml'] };
      },
    });
    renderWithClient(<Harness />);
    await user.click(await screen.findByRole('button', { name: "Keep the session's version" }));
    act(() => useDialogs.getState().show('save'));

    await user.click(screen.getByRole('button', { name: 'Save' }));
    await user.click(await screen.findByRole('button', { name: 'Overwrite' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(saves).toEqual([false, true]);
    expect(screen.getByText('Saved 1 file')).toBeInTheDocument();
  });

  it('does not post while an edit is being sent', async () => {
    const user = userEvent.setup();
    const { posts } = serve([editableSession()], [], { [`POST ${BASE}/edits`]: new Promise(() => {}) });
    function Editor() {
      const edit = useEdit(SID);
      return (
        <button type="button" onClick={() => edit.mutate([setParam('leaf_add', 'k', 7)])}>
          edit
        </button>
      );
    }
    renderWithClient(
      <>
        <Harness />
        <Editor />
      </>,
    );
    await waitFor(() => expect(posts()).toBe(1));
    await user.click(screen.getByRole('button', { name: 'edit' }));
    fireEvent.focus(window);
    fireEvent.focus(window);
    await new Promise((resolve) => setTimeout(resolve, 50));
    expect(posts()).toBe(1);
  });
});

describe('the notice', () => {
  it('clears itself after NOTICE_MS', () => {
    vi.useFakeTimers();
    useEditingStore.getState().setNotice('sub/leaf.yaml reloaded from disk');
    vi.advanceTimersByTime(NOTICE_MS - 1);
    expect(useEditingStore.getState().notice).toBe('sub/leaf.yaml reloaded from disk');
    vi.advanceTimersByTime(1);
    expect(useEditingStore.getState().notice).toBeNull();
  });
});
