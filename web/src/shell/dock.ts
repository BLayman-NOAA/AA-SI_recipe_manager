/** Commands on the Dockview layout, usable from menus, panes and shortcuts. */
import type { AddPanelOptions, DockviewApi, SerializedDockview } from 'dockview-react';
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

/** Restore the saved layout, or build the default one when that fails. */
export function restoreLayout(api: DockviewApi): void {
  const saved = readStoredJson<unknown>(LAYOUT_KEY);
  if (isUsableLayout(saved)) {
    try {
      api.fromJSON(saved);
      if (api.panels.length > 0) return;
    } catch {
      // A layout saved by another version; the default replaces it.
    }
  }
  applyDefaultLayout(api);
}

/** Remember the layout now. */
export function persistLayout(): void {
  const api = useDock.getState().api;
  if (api) writeStored(LAYOUT_KEY, JSON.stringify(api.toJSON()));
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
