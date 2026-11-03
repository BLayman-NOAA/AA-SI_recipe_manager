// The YAML pane in write mode: the editor state machine, then the pane against
// a mocked server, typing through change events.
import { act, fireEvent, renderHook, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { HistoryEntry } from '../src/api/models';
import { applyEdit, queryKeys } from '../src/api/queries';
import { setParam } from '../src/editing/operations';
import { useEditingStore } from '../src/editing/store';
import { useDiskSync } from '../src/editing/useDiskSync';
import { useCommands } from '../src/shell/commands';
import { useDialogs } from '../src/shell/dialogs';
import { installShortcuts } from '../src/shell/shortcuts';
import { NO_RUN_CONTROLS } from '../src/runs/useRunControls';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import { DEBOUNCE_MS, editorStatus, needsSend, nextState } from '../src/yaml/editorState';
import { changedFiles, flush, resetEditors, useYamlEditors, useYamlSender } from '../src/yaml/editors';
import { stepLine } from '../src/yaml/findLine';
import { YamlPane } from '../src/yaml/YamlPane';
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

const SID = '20261013-153012-a1b2c3';
const BASE = `/api/sessions/${SID}`;
const EDITS = `POST ${BASE}/edits`;

const ROOT = [
  'name: client_with_sub',
  'steps:',
  '  - id: seed',
  '    op: custom',
  '  - include: sub/chain.yaml',
].join('\n');

describe('the editor state', () => {
  const loaded = nextState(undefined, { type: 'load', text: 'a: 1' })!;

  it('sends typing once, and is saved once the server has it', () => {
    let state = nextState(loaded, { type: 'type', text: 'a: 2\r\n' })!;
    expect(state.text).toBe('a: 2\n');
    expect(editorStatus(state)).toBe('typing');
    expect(needsSend(state)).toBe(true);
    state = nextState(state, { type: 'send' })!;
    expect(editorStatus(state)).toBe('sending');
    expect(needsSend(state)).toBe(false);
    state = nextState(state, { type: 'accepted', text: 'a: 2\n' })!;
    expect(editorStatus(state)).toBe('saved');
    // The refetch after the edit brings the same text: nothing changes.
    expect(nextState(state, { type: 'load', text: 'a: 2\n' })).toBe(state);
  });

  it('keeps a refused text with its error and does not send it again', () => {
    let state = nextState(loaded, { type: 'type', text: 'a: [' })!;
    state = nextState(nextState(state, { type: 'send' }), { type: 'rejected', text: 'a: [', error: 'bad' })!;
    expect(state).toMatchObject({ text: 'a: [', base: 'a: 1', error: 'bad' });
    expect(editorStatus(state)).toBe('rejected');
    expect(needsSend(state)).toBe(false);
    state = nextState(state, { type: 'type', text: 'a: [1]' })!;
    expect(needsSend(state)).toBe(true);
    expect(state.error).toBe('bad');
  });

  it('takes a server change with no typing pending, and asks with typing pending', () => {
    expect(nextState(loaded, { type: 'load', text: 'a: 5' })).toMatchObject({ text: 'a: 5', external: false });
    let state = nextState(loaded, { type: 'type', text: 'a: 2' })!;
    state = nextState(state, { type: 'load', text: 'a: 5' })!;
    expect(state).toMatchObject({ text: 'a: 2', base: 'a: 5', external: true });
    expect(needsSend(state)).toBe(false);
    expect(nextState(state, { type: 'reload' })).toMatchObject({ text: 'a: 5', external: false });
    const kept = nextState(state, { type: 'keep' })!;
    expect(kept).toMatchObject({ text: 'a: 2', external: false });
    expect(needsSend(kept)).toBe(true);
  });

  it('keeps typing done while a send was out waiting to be sent', () => {
    let state = nextState(loaded, { type: 'type', text: 'a: 2' })!;
    state = nextState(state, { type: 'send' })!;
    state = nextState(state, { type: 'type', text: 'a: 23' })!;
    state = nextState(state, { type: 'accepted', text: 'a: 2' })!;
    expect(state).toMatchObject({ text: 'a: 23', base: 'a: 2' });
    expect(needsSend(state)).toBe(true);
  });

  it('sends a text again after a stale version, without calling it refused', () => {
    let state = nextState(loaded, { type: 'type', text: 'a: 2' })!;
    state = nextState(nextState(state, { type: 'send' }), { type: 'stale' })!;
    expect(state).toMatchObject({ sending: null, rejected: null, error: null });
    expect(editorStatus(state)).toBe('typing');
    expect(needsSend(state)).toBe(true);
  });

  it('holds typing over a change made elsewhere, and ignores one when nothing is typed', () => {
    expect(nextState(loaded, { type: 'changed' })).toBe(loaded);
    const typed = nextState(loaded, { type: 'type', text: 'a: 2' })!;
    const changed = nextState(typed, { type: 'changed' })!;
    expect(changed).toMatchObject({ text: 'a: 2', external: true });
    expect(needsSend(changed)).toBe(false);
  });
});

describe('changedFiles', () => {
  function entry(op: Record<string, unknown>, documents: string[]): HistoryEntry {
    return { seq: 1, time: '2026-10-29T12:00:00Z', op, inverse: {}, documents };
  }

  it('names the files an answer changed, except the editor sending its own text', () => {
    const typed = nextState(nextState(undefined, { type: 'load', text: 'a: 1' }), { type: 'type', text: 'a: 2' })!;
    const files = { 'a.yaml': nextState(typed, { type: 'send' })!, 'b.yaml': typed };
    const entries = [
      entry({ kind: 'replace_text', document: 'a.yaml', text: 'a: 2' }, ['a.yaml']),
      entry({ kind: 'set_param', step: 's', name: 'k', value: 1 }, ['b.yaml']),
      entry({ kind: 'replace_text', document: 'c.yaml', text: 'x' }, ['c.yaml']),
    ];
    expect(changedFiles(entries, files)).toEqual(['b.yaml', 'c.yaml']);
  });
});

describe('YamlPane in write mode', () => {
  let root: string;
  let edits: { operations: { kind: string; text?: string }[] }[];

  /** The routes of an editable session whose root file text follows the edits. */
  function editRoutes(answer?: () => Response) {
    root = ROOT;
    edits = [];
    const session = editableSession();
    const routes: Parameters<typeof mockApi>[0] = {
      [`GET ${BASE}`]: session,
      [`GET ${BASE}/files/with_sub.yaml`]: () => ({ path: 'with_sub.yaml', text: root }),
      [`GET ${BASE}/files/sub%2Fchain.yaml`]: { path: 'sub/chain.yaml', text: 'name: client_chain' },
      [EDITS]: (init?: RequestInit) => {
        const body = JSON.parse(String(init?.body));
        edits.push(body);
        if (answer) return answer();
        root = body.operations[0].text;
        return { session, changed: true, changed_hashes: [], entries: [] };
      },
    };
    return { session, routes };
  }

  function textarea(): HTMLTextAreaElement {
    return screen.getByRole('textbox', { name: 'with_sub.yaml text' }) as HTMLTextAreaElement;
  }

  /** The Shell's part: the sender, run once outside the pane. */
  function Sender({ sync = false }: { sync?: boolean }) {
    useYamlSender();
    useDiskSync(sync ? SID : null);
    return null;
  }

  /** The pane and, in its own tree as in the Shell, the sender. */
  async function openEditor(routes: Parameters<typeof mockApi>[0], options: { sync?: boolean } = {}) {
    const fetchMock = mockApi(routes);
    useSessionStore.getState().setSid(SID);
    const client = testQueryClient();
    renderWithClient(<Sender sync={options.sync} />, client);
    const rendered = renderWithClient(<YamlPane />, client);
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'with_sub.yaml text' })).toHaveValue(ROOT));
    return { ...rendered, fetchMock };
  }

  function sentTexts(): string[] {
    const ops = edits.flatMap((body) => body.operations);
    return ops.filter((op) => op.kind === 'replace_text').map((op) => op.text ?? '');
  }

  async function advance(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  beforeEach(() => {
    useSessionStore.getState().setSid(null);
    useView.getState().resetScope();
    resetEditors(testQueryClient(), null);
    useEditingStore.getState().reset();
    useDialogs.getState().close();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('sends one replace_text with the whole text and LF line ends after the last key', async () => {
    await openEditor(editRoutes().routes);
    expect(screen.getByText('editing')).toBeInTheDocument();
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n  - id: more` } });
    act(() => vi.advanceTimersByTime(500));
    fireEvent.change(textarea(), { target: { value: `${ROOT}\r\n  - id: more2` } });
    act(() => vi.advanceTimersByTime(DEBOUNCE_MS - 50));
    expect(edits).toHaveLength(0);
    await advance(50);
    await waitFor(() => expect(edits).toHaveLength(1));
    expect(edits[0]).toEqual({
      operations: [{ kind: 'replace_text', document: 'with_sub.yaml', text: `${ROOT}\n  - id: more2` }],
      version: 0,
    });
    await advance(2 * DEBOUNCE_MS);
    expect(edits).toHaveLength(1);
    expect(textarea()).toHaveValue(`${ROOT}\n  - id: more2`);
    expect(screen.getByText('editing')).toBeInTheDocument();
  });

  it('keeps refused text with the error inline, and the session it had', async () => {
    const problem = 'with_sub.yaml line 6: expected a mapping';
    const { routes, session } = editRoutes(() =>
      apiErrorResponse(422, 'EditRejected', problem, { problems: [problem] }),
    );
    const { client } = await openEditor(routes);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const broken = `${ROOT}\n  - id: [`;
    fireEvent.change(textarea(), { target: { value: broken } });
    await advance(DEBOUNCE_MS);
    expect(await screen.findByRole('alert')).toHaveTextContent(problem);
    expect(textarea()).toHaveValue(broken);
    expect(screen.getByText('not applied')).toBeInTheDocument();
    expect(screen.getByText('The diagram shows the last text that loaded.')).toBeInTheDocument();
    // The diagram draws the session's graph, which the refusal leaves as it was.
    expect(client.getQueryData(queryKeys.session(SID))).toEqual(session);
    await advance(2 * DEBOUNCE_MS);
    expect(edits).toHaveLength(1);
  });

  it('takes a form edit when nothing is typed, and asks when typing waits', async () => {
    const { client } = await openEditor(editRoutes().routes);
    const second = ROOT.replace('seed', 'seed2');
    root = second;
    await act(() => client.invalidateQueries({ queryKey: queryKeys.files(SID) }));
    await waitFor(() => expect(textarea()).toHaveValue(second));
    expect(screen.queryByText('This file was changed outside the editor.')).toBeNull();

    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.change(textarea(), { target: { value: `${second}\n# mine` } });
    root = ROOT.replace('seed', 'seed3');
    await act(() => client.invalidateQueries({ queryKey: queryKeys.files(SID) }));
    expect(await screen.findByText('This file was changed outside the editor.')).toBeInTheDocument();
    expect(textarea()).toHaveValue(`${second}\n# mine`);
    await advance(2 * DEBOUNCE_MS);
    expect(edits).toHaveLength(0);

    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    expect(textarea()).toHaveValue(ROOT.replace('seed', 'seed3'));
    expect(screen.queryByText('This file was changed outside the editor.')).toBeNull();
  });

  it('sends the typed text over a form edit the user keeps', async () => {
    const { client } = await openEditor(editRoutes().routes);
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# mine` } });
    root = ROOT.replace('seed', 'seed3');
    await act(() => client.invalidateQueries({ queryKey: queryKeys.files(SID) }));
    fireEvent.click(await screen.findByRole('button', { name: 'Keep my text' }));
    await advance(DEBOUNCE_MS);
    await waitFor(() => expect(edits).toHaveLength(1));
    expect(edits[0]).toMatchObject({ operations: [{ text: `${ROOT}\n# mine` }] });
  });

  it('shows a change on disk the session did not take in, with Reload from disk', async () => {
    const { routes, session } = editRoutes();
    const reloads: unknown[] = [];
    await openEditor({
      ...routes,
      [`GET ${BASE}`]: { ...session, conflicts: [{ path: 'with_sub.yaml', disk_sha256: 'd1', error: null }] },
      [`POST ${BASE}/reload`]: (init?: RequestInit) => {
        reloads.push(JSON.parse(String(init?.body)));
        return { session, changed: true, changed_hashes: [], entries: [] };
      },
    });
    expect(screen.getByText('with_sub.yaml changed on disk and has unsaved edits here.')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Reload from disk' }));
    await waitFor(() => expect(reloads).toEqual([{ files: ['with_sub.yaml'], version: 0 }]));
  });

  it('offers no reload for a disk text that does not load, and hides a kept one', async () => {
    const { routes, session } = editRoutes();
    await openEditor({
      ...routes,
      [`GET ${BASE}`]: { ...session, conflicts: [{ path: 'with_sub.yaml', disk_sha256: 'd2', error: 'bad YAML' }] },
    });
    expect(screen.getByText('with_sub.yaml changed on disk but does not load: bad YAML.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Reload from disk' })).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: "Keep the session's version" }));
    expect(screen.queryByText(/changed on disk/)).toBeNull();
  });

  it('numbers every line, follows the scroll and highlights the selected step', async () => {
    await openEditor(editRoutes().routes);
    const numbers = () =>
      [...document.querySelectorAll('.rc-yaml-gutter .rc-yaml-number')].map((number) => number.textContent);
    expect(numbers()).toEqual(['1', '2', '3', '4', '5']);
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n\n# end` } });
    expect(numbers()).toHaveLength(7);

    const area = textarea();
    let top = 36;
    Object.defineProperty(area, 'scrollTop', {
      configurable: true,
      get: () => top,
      set: (value: number) => {
        top = value;
      },
    });
    fireEvent.scroll(area);
    const editor = document.querySelector<HTMLElement>('.rc-yaml-editor')!;
    expect(editor.style.getPropertyValue('--rc-yaml-scroll')).toBe('-36px');

    act(() => useView.getState().select({ kind: 'step', id: 'seed' }));
    const line = stepLine(ROOT, 'seed')!;
    await waitFor(() =>
      expect(document.querySelector('.rc-yaml-number.rc-yaml-highlight')).toHaveTextContent(String(line + 1)),
    );
    const band = document.querySelector<HTMLElement>('.rc-yaml-editor-band')!;
    expect(band.style.getPropertyValue('--rc-yaml-band')).toBe(String(line));
    // Scrolled to the line: jsdom has no layout, so a 20 px line in a 0 px tall box.
    expect(top).toBe(line * 20);
  });

  it('colours the text behind the textarea with the read only token classes', async () => {
    await openEditor(editRoutes().routes);
    const colors = () => document.querySelector<HTMLElement>('pre.rc-yaml-editor-colors')!;
    expect(colors()).toHaveAttribute('aria-hidden', 'true');
    expect(colors().textContent).toBe(textarea().value);
    const keys = [...colors().querySelectorAll('.rc-tok-key')].map((token) => token.textContent);
    expect(keys).toEqual(['name', 'steps', 'id', 'op', 'include']);
    expect(colors().querySelector('.rc-tok-string')).toHaveTextContent('client_with_sub');

    const typed = `${ROOT}\n    factor: 2.5  # scale\n`;
    fireEvent.change(textarea(), { target: { value: typed } });
    expect(colors().querySelector('.rc-tok-comment')).toHaveTextContent('# scale');
    expect(colors().querySelector('.rc-tok-number')).toHaveTextContent('2.5');
    // A trailing newline gets one more, so the pre draws the empty last line.
    expect(colors().textContent).toBe(`${typed}\n`);
    fireEvent.change(textarea(), { target: { value: `${typed}x` } });
    expect(colors().textContent).toBe(`${typed}x`);
  });

  it('moves the coloured text with the textarea scroll', async () => {
    await openEditor(editRoutes().routes);
    const area = textarea();
    Object.defineProperty(area, 'scrollTop', { configurable: true, value: 54 });
    Object.defineProperty(area, 'scrollLeft', { configurable: true, value: 30 });
    fireEvent.scroll(area);
    const colors = document.querySelector<HTMLElement>('pre.rc-yaml-editor-colors')!;
    expect(colors.style.transform).toBe('translate(-30px, -54px)');
    const editor = document.querySelector<HTMLElement>('.rc-yaml-editor')!;
    expect(editor.style.getPropertyValue('--rc-yaml-scroll')).toBe('-54px');
  });

  it('marks a file with unsaved edits on its tab', async () => {
    const { routes, session } = editRoutes();
    const files = session.files.map((file) => ({ ...file, dirty: file.path === 'sub/chain.yaml' }));
    await openEditor({ ...routes, [`GET ${BASE}`]: { ...session, files } });
    expect(screen.getByRole('tab', { name: 'sub/chain.yaml' }).querySelector('.rc-tab-dot')).not.toBeNull();
    expect(screen.getByRole('tab', { name: 'with_sub.yaml' }).querySelector('.rc-tab-dot')).toBeNull();
  });

  it('keeps typed text while the pane is hidden, and the sender sends it with the pane closed', async () => {
    const { unmount, client } = await openEditor(
      editRoutes(() => apiErrorResponse(422, 'EditRejected', 'bad', { problems: ['bad'] })).routes,
    );
    fireEvent.change(textarea(), { target: { value: 'a: [' } });
    unmount();
    await waitFor(() => expect(edits).toHaveLength(1), { timeout: 3000 });
    renderWithClient(<YamlPane />, client);
    await waitFor(() => expect(screen.getByRole('textbox', { name: 'with_sub.yaml text' })).toHaveValue('a: ['));
    expect(await screen.findByRole('alert')).toHaveTextContent('bad');
  });

  it('sends typing that waits while the pane is closed during another edit', async () => {
    let finish: (value: unknown) => void = () => {};
    const { routes, session } = editRoutes();
    const formEdit = new Promise((resolve) => {
      finish = resolve;
    });
    const { unmount, client } = await openEditor({
      ...routes,
      [EDITS]: (init?: RequestInit) => {
        const body = JSON.parse(String(init?.body));
        if (body.operations[0].kind === 'set_param') return formEdit;
        edits.push(body);
        root = body.operations[0].text;
        return { session, changed: true, changed_hashes: [], entries: [] };
      },
    });
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# mine` } });
    const pending = applyEdit(client, SID, [setParam('seed', 'k', 1)]);
    unmount();
    finish({ session, changed: true, changed_hashes: [], entries: [] });
    await act(() => pending);
    await waitFor(() => expect(sentTexts()).toEqual([`${ROOT}\n# mine`]), { timeout: 3000 });
  });

  it('holds typing in a hidden tab that a form edit changed, for Reload or Keep', async () => {
    const { routes, session } = editRoutes();
    const { client } = await openEditor({
      ...routes,
      [EDITS]: (init?: RequestInit) => {
        const body = JSON.parse(String(init?.body));
        edits.push(body);
        const op = body.operations[0];
        if (op.kind === 'replace_text') {
          root = op.text;
          return { session, changed: true, changed_hashes: [], entries: [] };
        }
        root = ROOT.replace('seed', 'seed3');
        const entry = { seq: 1, time: '2026-10-29T12:00:00Z', op, inverse: {}, documents: ['with_sub.yaml'] };
        return { session, changed: true, changed_hashes: [], entries: [entry] };
      },
    });
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# mine` } });
    fireEvent.click(screen.getByRole('tab', { name: 'sub/chain.yaml' }));
    await act(() => applyEdit(client, SID, [setParam('seed', 'k', 1)]));
    await advance(3 * DEBOUNCE_MS);
    expect(sentTexts()).toEqual([]);

    fireEvent.click(screen.getByRole('tab', { name: 'with_sub.yaml' }));
    expect(await screen.findByText('This file was changed outside the editor.')).toBeInTheDocument();
    expect(textarea()).toHaveValue(`${ROOT}\n# mine`);
    fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
    await waitFor(() => expect(textarea()).toHaveValue(ROOT.replace('seed', 'seed3')));
  });

  it('waits for a disk sync before sending, and sends again after a stale version', async () => {
    let finishSync: (value: unknown) => void = () => {};
    const { routes, session } = editRoutes();
    let stale = true;
    await openEditor(
      {
        ...routes,
        [`POST ${BASE}/sync`]: () =>
          new Promise((resolve) => {
            finishSync = resolve;
          }),
        [EDITS]: (init?: RequestInit) => {
          const body = JSON.parse(String(init?.body));
          edits.push(body);
          if (stale) {
            stale = false;
            return apiErrorResponse(409, 'StaleVersion', 'stale', { version: 1 });
          }
          root = body.operations[0].text;
          return { session, changed: true, changed_hashes: [], entries: [] };
        },
      },
      { sync: true },
    );
    vi.useFakeTimers({ shouldAdvanceTime: true });
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# mine` } });
    await advance(3 * DEBOUNCE_MS);
    expect(edits).toHaveLength(0);

    await act(async () => finishSync({ session, reloaded: [], conflicts: [], entries: [] }));
    await advance(DEBOUNCE_MS);
    await waitFor(() => expect(edits).toHaveLength(1));
    await advance(DEBOUNCE_MS);
    await waitFor(() => expect(edits).toHaveLength(2));
    expect(sentTexts()).toEqual([`${ROOT}\n# mine`, `${ROOT}\n# mine`]);
    await waitFor(() => expect(screen.getByText('editing')).toBeInTheDocument());
    expect(screen.queryByText('not applied')).toBeNull();
    expect(screen.queryByRole('alert')).toBeNull();
  });

  it('flushes typing at once, and Save opens only after the text is sent', async () => {
    const { routes } = editRoutes();
    const dirty = editableSession({ dirty: ['with_sub.yaml'] });
    const { client } = await openEditor({
      ...routes,
      [EDITS]: (init?: RequestInit) => {
        const body = JSON.parse(String(init?.body));
        edits.push(body);
        root = body.operations[0].text;
        return { session: dirty, changed: true, changed_hashes: [], entries: [] };
      },
    });
    const wrapper = ({ children }: { children: ReactNode }) => (
      <QueryClientProvider client={client}>{children}</QueryClientProvider>
    );
    const commands = renderHook(() => useCommands(NO_RUN_CONTROLS), { wrapper }).result.current;
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# mine` } });
    act(() => commands.save());
    expect(useDialogs.getState().open).toBeNull();
    await waitFor(() => expect(useDialogs.getState().open).toBe('save'));
    expect(sentTexts()).toEqual([`${ROOT}\n# mine`]);

    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# more` } });
    await act(() => flush(client));
    expect(sentTexts()).toEqual([`${ROOT}\n# mine`, `${ROOT}\n# more`]);
  });

  it('sends the typing of a session left to it, and starts the next one empty', async () => {
    const { client } = await openEditor(editRoutes().routes);
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# mine` } });
    act(() => {
      useSessionStore.getState().setSid(null);
      resetEditors(client, SID);
    });
    expect(useYamlEditors.getState().files).toEqual({});
    await waitFor(() => expect(sentTexts()).toEqual([`${ROOT}\n# mine`]));
    expect(useYamlEditors.getState().files).toEqual({});
  });

  it('opens no survey warning in the next session for typing sent to the one left', async () => {
    const { routes, session } = editRoutes();
    const sent: string[] = [];
    const { client } = await openEditor({
      ...routes,
      [EDITS]: (init?: RequestInit) => {
        sent.push(JSON.parse(String(init?.body)).operations[0].text);
        return { session, changed: true, changed_hashes: ['seed'], entries: [] };
      },
    });
    client.setQueryData(
      queryKeys.preview(SID, [], false, false, []),
      makePreview([previewStep('seed', { held_by: ['survey'] })]),
    );
    fireEvent.change(textarea(), { target: { value: `${ROOT}\n# mine` } });
    act(() => {
      useSessionStore.getState().setSid('20261029-090000-d4e5f6');
      resetEditors(client, SID);
    });
    await waitFor(() => expect(sent).toEqual([`${ROOT}\n# mine`]));
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(useEditingStore.getState().surveyWarning).toBeNull();
  });

  it('leaves Ctrl+Z inside the textarea to the browser', async () => {
    await openEditor(editRoutes().routes);
    const undo = vi.fn();
    const stop = installShortcuts({ undo });
    try {
      const event = new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
      textarea().dispatchEvent(event);
      expect(undo).not.toHaveBeenCalled();
      expect(event.defaultPrevented).toBe(false);
    } finally {
      stop();
    }
  });

  it('stays read only in a read only session', async () => {
    mockApi({
      [`GET ${BASE}`]: makeSession(),
      [`GET ${BASE}/files/with_sub.yaml`]: { path: 'with_sub.yaml', text: ROOT },
    });
    useSessionStore.getState().setSid(SID);
    renderWithClient(<YamlPane />);
    expect(await screen.findByText('read only')).toBeInTheDocument();
    await waitFor(() => expect(document.querySelector('.rc-yaml-line')).not.toBeNull());
    expect(screen.queryByRole('textbox')).toBeNull();
  });
});
