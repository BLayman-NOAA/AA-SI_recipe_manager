/** Diagram view state: selection, expanded steps and boxes, drill path and view options. */
import { create } from 'zustand';
import { readStoredJson, writeStored } from './storage';

export const VIEW_KEY = 'rc.view';

/**
 * What a selection points at. An `inputsBox` id is `inputs` or
 * `overrides:<includeId>`; an `outputsBox` id is the box owner's node id.
 */
export type SelectionKind = 'step' | 'include' | 'input' | 'inputsBox' | 'outputsBox';

export interface Selection {
  kind: SelectionKind;
  id: string;
}

export type Direction = 'RIGHT' | 'DOWN';
export type Theme = 'dark' | 'light';

/** View options remembered per browser. */
export interface ViewPreferences {
  connected: boolean;
  direction: Direction;
  theme: Theme;
}

export interface ViewState extends ViewPreferences {
  selected: Selection | null;
  /** Step ids showing their parameter list. */
  expandedSteps: Set<string>;
  /** Inputs, outputs and sub-recipe box node ids listing their rows. */
  expandedBoxes: Set<string>;
  /** Incremented to ask the diagram to expand (or collapse) everything in view. */
  expandRequest: { count: number; expanded: boolean };
  /** Include id path drilled into, outermost first; empty is the whole recipe. */
  drill: string[];
  findQuery: string;
  /** Incremented to ask the diagram to fit its view. */
  fitRequest: number;
  /** Incremented to ask the diagram to focus its find box. */
  findRequest: number;

  select: (selection: Selection | null) => void;
  toggleStepExpanded: (id: string) => void;
  toggleBoxExpanded: (id: string) => void;
  requestExpandAll: (expanded: boolean) => void;
  /** Expand these steps and boxes, keeping the ones already expanded. */
  expandMany: (steps: string[], boxes: string[]) => void;
  collapseAll: () => void;
  drillInto: (id: string) => void;
  drillTo: (depth: number) => void;
  drillOut: () => void;
  setDrill: (drill: string[]) => void;
  setConnected: (connected: boolean) => void;
  setDirection: (direction: Direction) => void;
  setTheme: (theme: Theme) => void;
  setFindQuery: (query: string) => void;
  requestFit: () => void;
  requestFind: () => void;
  /** Clear selection, expansion, drill and find for a newly opened session. */
  resetScope: () => void;
}

export const DEFAULT_PREFERENCES: ViewPreferences = {
  connected: false,
  direction: 'RIGHT',
  theme: 'dark',
};

/** Stored preferences merged over the defaults, ignoring malformed values. */
export function loadPreferences(): ViewPreferences {
  const stored = readStoredJson<Partial<ViewPreferences>>(VIEW_KEY) ?? {};
  const prefs = { ...DEFAULT_PREFERENCES };
  if (typeof stored.connected === 'boolean') prefs.connected = stored.connected;
  if (stored.direction === 'RIGHT' || stored.direction === 'DOWN') {
    prefs.direction = stored.direction;
  }
  if (stored.theme === 'dark' || stored.theme === 'light') prefs.theme = stored.theme;
  return prefs;
}

/** Remember the preferences of a state. */
function savePreferences(state: ViewPreferences): void {
  writeStored(VIEW_KEY, JSON.stringify(preferencesOf(state)));
}

/** A copy of a set with one id added or removed. */
function toggled(ids: Set<string>, id: string): Set<string> {
  const next = new Set(ids);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

export const useView = create<ViewState>((set, get) => ({
  ...loadPreferences(),
  selected: null,
  expandedSteps: new Set<string>(),
  expandedBoxes: new Set<string>(),
  expandRequest: { count: 0, expanded: true },
  drill: [],
  findQuery: '',
  fitRequest: 0,
  findRequest: 0,

  select: (selected) => set({ selected }),
  toggleStepExpanded: (id) => set((state) => ({ expandedSteps: toggled(state.expandedSteps, id) })),
  toggleBoxExpanded: (id) => set((state) => ({ expandedBoxes: toggled(state.expandedBoxes, id) })),
  requestExpandAll: (expanded) =>
    set((state) => ({ expandRequest: { count: state.expandRequest.count + 1, expanded } })),
  expandMany: (steps, boxes) =>
    set((state) => ({
      expandedSteps: new Set([...state.expandedSteps, ...steps]),
      expandedBoxes: new Set([...state.expandedBoxes, ...boxes]),
    })),
  collapseAll: () => set({ expandedSteps: new Set<string>(), expandedBoxes: new Set<string>() }),
  drillInto: (id) =>
    set((state) => {
      const expandedBoxes = new Set(state.expandedBoxes);
      expandedBoxes.delete(id);
      return { drill: [...state.drill, id], expandedBoxes };
    }),
  drillTo: (depth) => set((state) => ({ drill: state.drill.slice(0, Math.max(0, depth)) })),
  drillOut: () => get().drillTo(get().drill.length - 1),
  setDrill: (drill) => set({ drill }),
  setConnected: (connected) => {
    set({ connected });
    savePreferences(get());
  },
  setDirection: (direction) => {
    set({ direction });
    savePreferences(get());
  },
  setTheme: (theme) => {
    set({ theme });
    savePreferences(get());
  },
  setFindQuery: (findQuery) => set({ findQuery }),
  requestFit: () => set((state) => ({ fitRequest: state.fitRequest + 1 })),
  requestFind: () => set((state) => ({ findRequest: state.findRequest + 1 })),
  resetScope: () =>
    set({
      selected: null,
      expandedSteps: new Set<string>(),
      expandedBoxes: new Set<string>(),
      drill: [],
      findQuery: '',
    }),
}));

/** The preferences part of a view state, as stored. */
export function preferencesOf(state: ViewPreferences): ViewPreferences {
  const { connected, direction, theme } = state;
  return { connected, direction, theme };
}
