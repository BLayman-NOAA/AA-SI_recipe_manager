/**
 * The YAML editors' states and the sender of their typing. App only.
 *
 * The states live outside the pane, since the dock unmounts a hidden tab and
 * typed text must survive a look at the diagram. The Shell runs the sender
 * once, so typing is sent whether the pane is open or not.
 */
import { useIsMutating, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { create } from 'zustand';
import { editProblems, isApiError } from '../api/client';
import type { HistoryEntry } from '../api/models';
import { EDITING_KEY, postEdit, refetchSessionFile } from '../api/queries';
import { replaceText } from '../editing/operations';
import { useSessionStore } from '../state/session';
import { DEBOUNCE_MS, lf, needsSend, nextState, type EditorEvent, type EditorState } from './editorState';

interface EditorsState {
  /** Each edited file's editor state, by path. */
  files: Record<string, EditorState>;
  dispatch: (path: string, event: EditorEvent) => void;
}

export const useYamlEditors = create<EditorsState>((set) => ({
  files: {},
  dispatch: (path, event) =>
    set((state) => {
      const next = nextState(state.files[path], event);
      if (next === state.files[path]) return state;
      const files = { ...state.files };
      if (next) files[path] = next;
      return { files };
    }),
}));

/** How many times the editors were reset; a send's answer for an earlier session is dropped. */
let generation = 0;

/** Sends out, each settled once its answer is recorded. */
const inFlight = new Set<Promise<void>>();

/** How many rounds flush sends before it gives up on a file that keeps meeting newer versions. */
const FLUSH_ROUNDS = 3;

function dispatchIn(gen: number, path: string, event: EditorEvent) {
  if (gen === generation) useYamlEditors.getState().dispatch(path, event);
}

/** The files whose typing waits to be sent. */
export function dueFiles(files: Record<string, EditorState>): [string, EditorState][] {
  return Object.entries(files).filter(([, state]) => needsSend(state));
}

/**
 * Send a file's text as one ReplaceText and record the answer. A stale
 * version is not a refusal: the session and the file are refetched, and the
 * text is due again.
 */
function send(client: QueryClient, sid: string, path: string, text: string, gen = generation): Promise<void> {
  dispatchIn(gen, path, { type: 'send' });
  const answer = postEdit(client, sid, [replaceText(path, text)]).then(
    () => dispatchIn(gen, path, { type: 'accepted', text }),
    async (error: Error) => {
      if (!isApiError(error, 'StaleVersion')) {
        dispatchIn(gen, path, { type: 'rejected', text, error: editProblems(error).join('\n') });
        return;
      }
      const server = await refetchSessionFile(client, sid, path).catch(() => null);
      if (server !== null) dispatchIn(gen, path, { type: 'load', text: lf(server) });
      dispatchIn(gen, path, { type: 'stale' });
    },
  );
  inFlight.add(answer);
  void answer.finally(() => inFlight.delete(answer));
  return answer;
}

/**
 * The files the history entries of an answer changed, leaving out the
 * editor's own send of a file.
 */
export function changedFiles(entries: HistoryEntry[], files: Record<string, EditorState>): string[] {
  const paths = new Set<string>();
  for (const entry of entries) {
    const op = entry.op as { kind?: unknown; text?: unknown };
    for (const path of entry.documents) {
      const own = op.kind === 'replace_text' && files[path]?.sending === op.text;
      if (!own) paths.add(path);
    }
  }
  return [...paths];
}

/**
 * Mark the editors of files another change touched (an edit, undo, redo,
 * reload or sync answer with entries), so typing in a hidden tab waits for
 * Reload or Keep instead of being sent over the change.
 */
function useMarkChangedFiles(client: QueryClient) {
  useEffect(
    () =>
      client.getMutationCache().subscribe((event) => {
        if (event.type !== 'updated' || event.action.type !== 'success') return;
        const data = event.action.data as { entries?: HistoryEntry[]; session?: { id: string } } | undefined;
        if (!Array.isArray(data?.entries) || data.session?.id !== useSessionStore.getState().sid) return;
        const { files, dispatch } = useYamlEditors.getState();
        for (const path of changedFiles(data.entries, files)) dispatch(path, { type: 'changed' });
      }),
    [client],
  );
}

/**
 * Send typing in any file 800 ms after the last key, one request at a time
 * and never while another change is pending. Call once, in Shell.
 */
export function useYamlSender() {
  const client = useQueryClient();
  const sid = useSessionStore((state) => state.sid);
  const busy = useIsMutating({ mutationKey: EDITING_KEY }) > 0;
  const duePath = useYamlEditors((state) => dueFiles(state.files)[0]?.[0]);
  const dueText = useYamlEditors((state) => dueFiles(state.files)[0]?.[1].text);
  useMarkChangedFiles(client);

  useEffect(() => {
    if (!sid || duePath === undefined || dueText === undefined || busy) return;
    const timer = setTimeout(() => {
      // A flush may have sent it, or started another change, since the last render.
      const state = useYamlEditors.getState().files[duePath];
      if (!state || !needsSend(state) || state.text !== dueText) return;
      if (client.isMutating({ mutationKey: EDITING_KEY }) > 0) return;
      void send(client, sid, duePath, dueText);
    }, DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [client, sid, duePath, dueText, busy]);
}

/** Resolve once no change to the recipe is pending. */
function editingIdle(client: QueryClient): Promise<void> {
  const idle = () => client.isMutating({ mutationKey: EDITING_KEY }) === 0;
  if (idle()) return Promise.resolve();
  return new Promise((resolve) => {
    const stop = client.getMutationCache().subscribe(() => {
      if (!idle()) return;
      stop();
      resolve();
    });
  });
}

/** Whether typing waits to be sent or a send is out. */
export function typingPending(): boolean {
  return inFlight.size > 0 || dueFiles(useYamlEditors.getState().files).length > 0;
}

/**
 * Send the typing waiting in every file now, without the debounce, and
 * resolve once the sends have their answers. Save, Save As and a run start
 * call it first, so they see the text typed last.
 */
export async function flush(client: QueryClient): Promise<void> {
  const sid = useSessionStore.getState().sid;
  const gen = generation;
  for (let round = 0; sid && round < FLUSH_ROUNDS && typingPending(); round++) {
    await Promise.all([...inFlight]);
    await editingIdle(client);
    if (gen !== generation) return;
    for (const [path, state] of dueFiles(useYamlEditors.getState().files)) {
      await send(client, sid, path, state.text, gen);
    }
  }
}

/** Run an action once the typing waiting is sent: now when none waits. */
export function afterFlush(client: QueryClient, action: () => void) {
  if (!typingPending()) action();
  else void flush(client).then(action);
}

/**
 * Forget the editors, for another session. Typing still waiting in the
 * session left is sent to it first; its answers are not recorded.
 */
export function resetEditors(client: QueryClient, left: string | null) {
  const waiting = dueFiles(useYamlEditors.getState().files);
  const gen = generation;
  generation += 1;
  useYamlEditors.setState({ files: {} });
  if (!left || waiting.length === 0) return;
  void (async () => {
    for (const [path, state] of waiting) await send(client, left, path, state.text, gen);
  })();
}
