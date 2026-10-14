/** Global keyboard shortcuts. */

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
  { id: 'runToSelected', keys: 'Ctrl+Enter', action: 'Run to selected', milestone: 3 },
  { id: 'runSelectedOnly', keys: 'Ctrl+Shift+Enter', action: 'Run selected only', milestone: 3 },
  { id: 'undo', keys: 'Ctrl+Z', action: 'Undo', milestone: 5 },
  { id: 'redo', keys: 'Ctrl+Y', action: 'Redo', milestone: 5 },
  { id: 'save', keys: 'Ctrl+S', action: 'Save', milestone: 5 },
  { id: 'saveAs', keys: 'Ctrl+Shift+S', action: 'Save As', milestone: 5 },
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

/** The shortcut a key press means, or null. */
export function matchShortcut(input: KeyInput): ShortcutId | null {
  const ctrl = input.ctrlKey || input.metaKey;
  const key = input.key.length === 1 ? input.key.toLowerCase() : input.key;
  if (input.altKey) return null;
  if (ctrl) {
    if (key === 'f' && !input.shiftKey) return 'find';
    if (key === 'Enter') return input.shiftKey ? 'runSelectedOnly' : 'runToSelected';
    if (key === 'z' && !input.shiftKey) return 'undo';
    if (key === 'y' && !input.shiftKey) return 'redo';
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
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || target.isContentEditable;
}

export type ShortcutHandlers = Partial<Record<ShortcutId, () => void>>;

/** Shortcuts that keep the browser's own meaning unless a handler takes them. */
const BROWSER_DEFAULTS = new Set<ShortcutId>(['undo', 'redo', 'delete', 'escape']);

/** Listen for shortcuts on the window; returns the function that stops listening. */
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
  window.addEventListener('keydown', listener);
  return () => window.removeEventListener('keydown', listener);
}
