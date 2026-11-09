/** The open session tabs, in order, remembered across reloads (FR-7). */
import { create } from 'zustand';
import { SESSION_KEY, useSessionStore } from './session';
import { readStored, readStoredJson, writeStored } from './storage';

export const TABS_KEY = 'rc.tabs';

/** A step to select in a session once its document is loaded. */
export interface PendingSelection {
  sid: string;
  step: string;
}

interface TabsState {
  /** Open sessions, left to right. The front one is `useSessionStore.sid`. */
  sids: string[];
  /** A step to select once the session a layer row pointed at is loaded. */
  pendingSelection: PendingSelection | null;
  /** Open a tab on a session, or bring its tab to the front; `step` selects a step once it is in front. */
  open: (sid: string, step?: string) => void;
  /**
   * Close a tab; when it was in front, the one to its left (else right, else
   * none) comes to the front. Returns the new front.
   */
  close: (sid: string) => string | null;
  /** Move a tab to an index of the strip. */
  move: (sid: string, index: number) => void;
  clearSelection: () => void;
}

/** The saved tab order, tolerating a missing or malformed value; `rc.sid` is always included. */
export function readStoredTabs(): string[] {
  const stored = readStoredJson<unknown>(TABS_KEY);
  const sids = Array.isArray(stored) ? stored.filter((sid): sid is string => typeof sid === 'string' && sid !== '') : [];
  const front = readStored(SESSION_KEY);
  if (front) sids.push(front);
  return [...new Set(sids)];
}

function saveTabs(sids: string[]): void {
  writeStored(TABS_KEY, JSON.stringify(sids));
}

export const useTabs = create<TabsState>((set, get) => ({
  sids: readStoredTabs(),
  pendingSelection: null,

  open: (sid, step) => {
    const { sids } = get();
    const next = sids.includes(sid) ? sids : [...sids, sid];
    saveTabs(next);
    set({ sids: next, pendingSelection: step ? { sid, step } : null });
    useSessionStore.getState().setSid(sid);
  },

  close: (sid) => {
    const { sids, pendingSelection } = get();
    const index = sids.indexOf(sid);
    const rest = sids.filter((entry) => entry !== sid);
    const current = useSessionStore.getState().sid;
    const next = current !== sid ? current : ((index > 0 ? rest[index - 1] : rest[0]) ?? null);
    saveTabs(rest);
    set({ sids: rest, pendingSelection: pendingSelection?.sid === sid ? null : pendingSelection });
    if (next !== current) useSessionStore.getState().setSid(next);
    return next;
  },

  move: (sid, index) => {
    const { sids } = get();
    if (!sids.includes(sid)) return;
    const next = sids.filter((entry) => entry !== sid);
    next.splice(Math.max(0, Math.min(index, next.length)), 0, sid);
    saveTabs(next);
    set({ sids: next });
  },

  clearSelection: () => set({ pendingSelection: null }),
}));

/** Open a tab on a session, or bring it to the front. */
export function openTab(sid: string, step?: string): void {
  useTabs.getState().open(sid, step);
}

/** Close a session's tab; returns the new front. */
export function closeTab(sid: string): string | null {
  return useTabs.getState().close(sid);
}
