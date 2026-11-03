/**
 * The YAML editor's state for one file, as a pure state machine: what the
 * pane shows, what the server holds, and the send in flight. The sender in
 * editors.ts runs the debounce timer and the requests; this decides what each
 * event means.
 */

/** How long after the last key the text is sent. */
export const DEBOUNCE_MS = 800;

export type EditorStatus = 'saved' | 'typing' | 'sending' | 'rejected';

export interface EditorState {
  /** The text the pane shows. */
  text: string;
  /** The file's text on the server, as last heard. */
  base: string;
  /** The text being sent, or null. */
  sending: string | null;
  /** The last text the server refused, which is not sent again. */
  rejected: string | null;
  /** Why the server refused it, or why the send failed. */
  error: string | null;
  /** The server's text changed while local typing was pending (a form edit, a reload). */
  external: boolean;
}

export type EditorEvent =
  /** The server's text arrived (first load or a refetch). */
  | { type: 'load'; text: string }
  /** The user typed. */
  | { type: 'type'; text: string }
  /** The debounce fired and the text is sent. */
  | { type: 'send' }
  | { type: 'accepted'; text: string }
  | { type: 'rejected'; text: string; error: string }
  /** The send met a newer session version; the text is sent again. */
  | { type: 'stale' }
  /** Another change to the file was applied, whose text may not have loaded yet. */
  | { type: 'changed' }
  /** Take the server's text, dropping the local typing. */
  | { type: 'reload' }
  /** Keep the local typing over the server's text; it is sent as usual. */
  | { type: 'keep' };

/** Line ends as the server keeps them. */
export function lf(text: string): string {
  return text.replace(/\r\n?/g, '\n');
}

function fresh(text: string): EditorState {
  return { text, base: text, sending: null, rejected: null, error: null, external: false };
}

/** The state after an event; a first `load` creates it. */
export function nextState(state: EditorState | undefined, event: EditorEvent): EditorState | undefined {
  if (!state) return event.type === 'load' ? fresh(event.text) : undefined;
  switch (event.type) {
    case 'load': {
      if (event.text === state.base) return state;
      if (event.text === state.text) return { ...fresh(event.text), sending: state.sending };
      if (state.text === state.base) return { ...fresh(event.text), sending: state.sending };
      return { ...state, base: event.text, external: true };
    }
    case 'type': {
      const text = lf(event.text);
      if (text === state.base) return { ...state, text, rejected: null, error: null, external: false };
      return { ...state, text };
    }
    case 'send':
      return { ...state, sending: state.text };
    case 'accepted': {
      const synced = state.text === event.text;
      return {
        ...state,
        base: event.text,
        sending: null,
        rejected: null,
        error: null,
        external: synced ? false : state.external,
      };
    }
    case 'rejected':
      return { ...state, sending: null, rejected: event.text, error: event.error };
    case 'stale':
      return { ...state, sending: null };
    case 'changed':
      return state.text === state.base ? state : { ...state, external: true };
    case 'reload':
      return { ...fresh(state.base), sending: state.sending };
    case 'keep':
      return { ...state, external: false };
  }
}

/** What the pane says about the file. */
export function editorStatus(state: EditorState): EditorStatus {
  if (state.sending !== null) return 'sending';
  if (state.text === state.base) return 'saved';
  if (state.text === state.rejected) return 'rejected';
  return 'typing';
}

/**
 * Whether the text is waiting to be sent: typed, not on the server, not
 * refused already, and not waiting for the user to reload or keep.
 */
export function needsSend(state: EditorState): boolean {
  return editorStatus(state) === 'typing' && !state.external;
}
