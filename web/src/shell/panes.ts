/** The client's dockable panes and where each sits in the default layout. */

export type PaneId =
  | 'recipes'
  | 'ops'
  | 'diagram'
  | 'yaml'
  | 'inspector'
  | 'inputs'
  | 'runSettings'
  | 'logs'
  | 'outputs'
  | 'problems'
  | 'echogram';

export type PaneDirection = 'left' | 'right' | 'above' | 'below' | 'within';

/** Placement relative to another pane, or along an edge of the whole layout. */
export interface PanePlacement {
  direction: PaneDirection;
  reference?: PaneId;
}

export interface PaneDef {
  id: PaneId;
  title: string;
  placement?: PanePlacement;
  /** Added without becoming the active tab of its group. */
  inactive?: boolean;
  initialWidth?: number;
  initialHeight?: number;
}

/** Panes in the order the default layout adds them. */
export const PANES: PaneDef[] = [
  { id: 'diagram', title: 'Diagram' },
  { id: 'yaml', title: 'YAML', placement: { reference: 'diagram', direction: 'within' }, inactive: true },
  { id: 'recipes', title: 'Recipes', placement: { reference: 'diagram', direction: 'left' }, initialWidth: 280 },
  { id: 'ops', title: 'Ops', placement: { reference: 'recipes', direction: 'below' } },
  { id: 'inspector', title: 'Inspector', placement: { reference: 'diagram', direction: 'right' }, initialWidth: 340 },
  { id: 'inputs', title: 'Inputs', placement: { reference: 'inspector', direction: 'within' }, inactive: true },
  { id: 'runSettings', title: 'Run Settings', placement: { reference: 'inspector', direction: 'within' }, inactive: true },
  { id: 'logs', title: 'Logs', placement: { direction: 'below' }, initialHeight: 220 },
  { id: 'outputs', title: 'Outputs', placement: { reference: 'logs', direction: 'within' }, inactive: true },
  { id: 'problems', title: 'Problems', placement: { reference: 'logs', direction: 'within' } },
  { id: 'echogram', title: 'Echogram', placement: { reference: 'logs', direction: 'right' }, initialWidth: 420 },
];

export const PANE_IDS: PaneId[] = PANES.map((pane) => pane.id);

export function paneDef(id: PaneId): PaneDef {
  const pane = PANES.find((entry) => entry.id === id);
  if (!pane) throw new Error(`Unknown pane ${id}`);
  return pane;
}

export function isPaneId(id: string): id is PaneId {
  return (PANE_IDS as string[]).includes(id);
}

/**
 * Where to add a pane given the panes already open.
 *
 * Uses the pane's default placement when its reference is open; otherwise
 * falls back to the reference's own placement, and so on up the chain. Null
 * means "anywhere" (the active group).
 */
export function placementFor(id: PaneId, open: ReadonlySet<string>): PanePlacement | null {
  let placement = paneDef(id).placement;
  const seen = new Set<string>([id]);
  while (placement) {
    if (!placement.reference || open.has(placement.reference)) return placement;
    if (seen.has(placement.reference)) return null;
    seen.add(placement.reference);
    placement = paneDef(placement.reference).placement;
  }
  return null;
}
