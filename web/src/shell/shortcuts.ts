/** Global keyboard shortcuts. */
import { onPopoutWindows } from './dock';

export type ShortcutId =
  | 'fit'
  | 'find'
  | 'escape'
  | 'runToSelected'
  | 'runSelectedOnly'
  | 'undo'
  | 'redo'
  | 'save'
  | 'saveAs'
  | 'delete';

export interface ShortcutDef {
  id: ShortcutId;
  keys: string;
  action: string;
  /** Milestone that makes the shortcut do something; absent when it works now. */
  milestone?: number;
}

/** The shortcut table shown in Help, Keyboard Shortcuts. */
export const SHORTCUTS: ShortcutDef[] = [
  { id: 'fit', keys: 'F', action: 'Fit the diagram' },
  { id: 'find', keys: 'Ctrl+F or /', action: 'Find a step' },
  { id: 'escape', keys: 'Escape', action: 'Clear the selection, or drill out' },
  { id: 'runToSelected', keys: 'Ctrl+Enter', action: 'Run to selected' },
  { id: 'runSelectedOnly', keys: 'Ctrl+Shift+Enter', action: 'Run selected only' },
  { id: 'undo', keys: 'Ctrl+Z', action: 'Undo' },
  { id: 'redo', keys: 'Ctrl+Y', action: 'Redo' },
  { id: 'save', keys: 'Ctrl+S', action: 'Save' },
  { id: 'saveAs', keys: 'Ctrl+Shift+S', action: 'Save As' },
  { id: 'delete', keys: 'Delete', action: 'Delete the selection', milestone: 9 },
];

/** The parts of a keyboard event the matcher reads. */
export interface KeyInput {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  shiftKey: boolean;
  altKey: boolean;
  /** The event happened in a text field. */
  typing: boolean;
}

/** The shortcut a key press means, or null. Undo and redo in a text field are the field's own. */
export function matchShortcut(input: KeyInput): ShortcutId | null {
  const ctrl = input.ctrlKey || input.metaKey;
  const key = input.key.length === 1 ? input.key.toLowerCase() : input.key;
  if (input.altKey) return null;
  if (ctrl) {
    if (key === 'f' && !input.shiftKey) return 'find';
    if (key === 'Enter') return input.shiftKey ? 'runSelectedOnly' : 'runToSelected';
    if (key === 'z' && !input.shiftKey) return input.typing ? null : 'undo';
    if (key === 'y' && !input.shiftKey) return input.typing ? null : 'redo';
    if (key === 's') return input.shiftKey ? 'saveAs' : 'save';
    return null;
  }
  if (input.typing) return null;
  if (key === 'Escape') return 'escape';
  if (key === 'f' && !input.shiftKey) return 'fit';
  if (key === '/') return 'find';
  if (key === 'Delete') return 'delete';
  return null;
}

function isTyping(target: EventTarget | null): boolean {
  // Not instanceof: an element in a popout window has that window's HTMLElement.
  const element = target as HTMLElement | null;
  if (typeof element?.tagName !== 'string') return false;
  const tag = element.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || element.isContentEditable;
}

export type ShortcutHandlers = Partial<Record<ShortcutId, () => void>>;

/** Shortcuts that keep the browser's own meaning unless a handler takes them. */
const BROWSER_DEFAULTS = new Set<ShortcutId>(['undo', 'redo', 'delete', 'escape']);

/**
 * Listen for shortcuts on the window and on every popout window while it is
 * open; returns the function that stops listening.
 */
export function installShortcuts(handlers: ShortcutHandlers): () => void {
  const listener = (event: KeyboardEvent) => {
    const id = matchShortcut({
      key: event.key,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      shiftKey: event.shiftKey,
      altKey: event.altKey,
      typing: isTyping(event.target),
    });
    if (id === null) return;
    const handler = handlers[id];
    if (handler) {
      event.preventDefault();
      handler();
    } else if (!BROWSER_DEFAULTS.has(id)) {
      event.preventDefault();
    }
  };
  const listen = (target: Window) => {
    target.addEventListener('keydown', listener);
    return () => target.removeEventListener('keydown', listener);
  };
  const stopMain = listen(window);
  const stopPopouts = onPopoutWindows(listen);
  return () => {
    stopMain();
    stopPopouts();
  };
}
