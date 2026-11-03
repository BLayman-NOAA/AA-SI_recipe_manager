/** Commands on the Dockview layout, usable from menus, panes and shortcuts. */
import type {
  AddPanelOptions,
  DockviewApi,
  SerializedDockview,
  SerializedGridObject,
  SerializedPopoutGroup,
} from 'dockview-react';
import { create } from 'zustand';
import { readStoredJson, writeStored } from '../state/storage';
import { PANES, PANE_IDS, isPaneId, paneDef, placementFor, type PaneId } from './panes';

interface DockState {
  api: DockviewApi | null;
  /** Ids of the panes currently in the layout. */
  open: PaneId[];
}

export const useDock = create<DockState>(() => ({ api: null, open: [] }));

/** Where the serialized Dockview layout is saved, per browser. */
export const LAYOUT_KEY = 'rc.dock';

function openIds(api: DockviewApi): Set<string> {
  return new Set(api.panels.map((panel) => panel.id));
}

/** Refresh the open pane list; the layout change listener is its only caller. */
export function syncOpenPanes(): void {
  const api = useDock.getState().api;
  const open = api ? PANE_IDS.filter((id) => api.getPanel(id) !== undefined) : [];
  useDock.setState({ open });
}

function addPane(api: DockviewApi, id: PaneId, activate: boolean): void {
  const pane = paneDef(id);
  const placement = placementFor(id, openIds(api));
  const options: AddPanelOptions = {
    id,
    component: id,
    title: pane.title,
    inactive: !activate,
    initialWidth: pane.initialWidth,
    initialHeight: pane.initialHeight,
  };
  if (placement?.reference) {
    options.position = { referencePanel: placement.reference, direction: placement.direction };
  } else if (placement && placement.direction !== 'within') {
    options.position = { direction: placement.direction };
  }
  api.addPanel(options);
}

/** Build the default layout from scratch. */
export function applyDefaultLayout(api: DockviewApi): void {
  api.clear();
  for (const pane of PANES) {
    addPane(api, pane.id, !pane.inactive);
  }
}

/** True when a serialized layout names only known panes. */
export function isUsableLayout(layout: unknown): layout is SerializedDockview {
  if (!layout || typeof layout !== 'object') return false;
  const panels = (layout as { panels?: unknown }).panels;
  if (!panels || typeof panels !== 'object') return false;
  const ids = Object.keys(panels);
  return ids.length > 0 && ids.every(isPaneId);
}

type GroupState = NonNullable<SerializedPopoutGroup['data']>;
type GridNode = SerializedGridObject<GroupState>;

function gridLeaves(node: GridNode): GridNode[] {
  return node.type === 'leaf' ? [node] : (node.data as GridNode[]).flatMap(gridLeaves);
}

/**
 * A layout with its popout windows docked again: each popout's panes go back
 * into the group they left, or close when that group is gone. A reload then
 * opens no windows, which the browser would block anyway.
 */
export function dockedLayout(layout: SerializedDockview): SerializedDockview {
  const { popoutGroups, ...rest } = layout;
  if (!popoutGroups?.length) return rest;
  const root = structuredClone(layout.grid.root) as GridNode;
  const leaves = new Map(gridLeaves(root).map((leaf) => [(leaf.data as GroupState).id, leaf]));
  const panels = { ...layout.panels };
  for (const popout of popoutGroups) {
    const nested = popout.grid ? gridLeaves(popout.grid.root as GridNode) : [];
    const groups = popout.data ? [popout.data] : nested.map((leaf) => leaf.data as GroupState);
    const views = groups.flatMap((group) => group.views);
    const leaf = popout.gridReferenceGroup ? leaves.get(popout.gridReferenceGroup) : undefined;
    if (leaf) {
      const group = leaf.data as GroupState;
      group.views = [...group.views, ...views];
      group.activeView = groups[0]?.activeView ?? group.activeView;
      delete leaf.visible;
    } else {
      for (const id of views) delete panels[id];
    }
  }
  return { ...rest, grid: { ...layout.grid, root }, panels };
}

/** Restore the saved layout, or build the default one when that fails. */
export function restoreLayout(api: DockviewApi): void {
  const saved = readStoredJson<unknown>(LAYOUT_KEY);
  if (isUsableLayout(saved)) {
    try {
      api.fromJSON(dockedLayout(saved));
      if (api.panels.length > 0) return;
    } catch {
      // A layout saved by another version; the default replaces it.
    }
  }
  applyDefaultLayout(api);
}

/** Remember the layout now, with any popout windows docked. */
export function persistLayout(): void {
  const api = useDock.getState().api;
  if (api) writeStored(LAYOUT_KEY, JSON.stringify(dockedLayout(api.toJSON())));
}

/** Where a popped out pane's window loads from: an empty page dockview fills. */
export const POPOUT_URL = `${import.meta.env.BASE_URL}popout.html`;

/** The popout window a pane is in, or null when it is in the main window or closed. */
export function paneWindow(id: PaneId): Window | null {
  const location = useDock.getState().api?.getPanel(id)?.group.api.location;
  return location?.type === 'popout' ? location.getWindow() : null;
}

/** Open a pane in a window of its own, showing it first when it is closed. */
export function popoutPane(id: PaneId): void {
  const api = useDock.getState().api;
  if (!api) return;
  const open = paneWindow(id);
  if (open) {
    open.focus();
    return;
  }
  if (!api.getPanel(id)) addPane(api, id, true);
  const panel = api.getPanel(id);
  if (panel) void api.addPopoutGroup(panel, { popoutUrl: POPOUT_URL });
}

interface PopoutWatch {
  watch: (target: Window) => () => void;
  /** What undoes the watch on each window it ran on. */
  stops: Map<Window, () => void>;
}

const popoutWindows = new Set<Window>();
const popoutWatches = new Set<PopoutWatch>();

/** Note a popout window dockview opened; DockLayout calls it. */
export function addPopoutWindow(target: Window): void {
  if (popoutWindows.has(target)) return;
  popoutWindows.add(target);
  for (const entry of popoutWatches) entry.stops.set(target, entry.watch(target));
}

/** Note a popout window dockview lets go of; DockLayout calls it. */
export function removePopoutWindow(target: Window): void {
  if (!popoutWindows.delete(target)) return;
  for (const entry of popoutWatches) {
    entry.stops.get(target)?.();
    entry.stops.delete(target);
  }
}

/**
 * Run `watch` on every popout window, those open now and those opened later,
 * such as to listen for keys there. What it returns runs when that window
 * closes or the watch stops.
 *
 * Returns:
 *   The function that stops the watch.
 */
export function onPopoutWindows(watch: (target: Window) => () => void): () => void {
  const entry: PopoutWatch = { watch, stops: new Map() };
  for (const target of popoutWindows) entry.stops.set(target, watch(target));
  popoutWatches.add(entry);
  return () => {
    popoutWatches.delete(entry);
    for (const stop of entry.stops.values()) stop();
    entry.stops.clear();
  };
}

/** Put a popped out pane back into the main window; dockview docks it where it was. */
export function returnPane(id: PaneId): void {
  paneWindow(id)?.close();
}

/** Show a pane, adding it at its default place when it is closed. */
export function showPane(id: PaneId): void {
  const api = useDock.getState().api;
  if (!api) return;
  const panel = api.getPanel(id);
  if (panel) {
    panel.api.setActive();
  } else {
    addPane(api, id, true);
  }
}

/** Close a pane when it is open, otherwise show it. */
export function togglePane(id: PaneId): void {
  const api = useDock.getState().api;
  if (!api) return;
  const panel = api.getPanel(id);
  if (panel) {
    api.removePanel(panel);
  } else {
    showPane(id);
  }
}

/** Forget the saved layout and rebuild the default one. */
export function resetLayout(): void {
  writeStored(LAYOUT_KEY, null);
  const api = useDock.getState().api;
  if (api) applyDefaultLayout(api);
}
