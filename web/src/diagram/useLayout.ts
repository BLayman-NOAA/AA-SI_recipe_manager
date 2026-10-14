/** Lay out a view: measure nodes, run ELK in a worker, place the boxes and nodes. */
import type { Edge, Node, NodeChange, Viewport, XYPosition } from '@xyflow/react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import type { GraphEdgeKind } from '../api/graph';
import type { Direction } from '../state/view';
import { placeBuses } from './buses';
import { canUseWorker, runLayout } from './elk';
import { layoutRequest, type Positions, type Route } from './layout';
import { CONTAINER_KINDS, rowValue, type View, type ViewEdge, type ViewNode } from './buildView';

/** React Flow node carrying its view node. */
export type FlowNode = Node<{ node: ViewNode }>;

/** What RouteEdge draws: the routed polyline, if any, and the label. */
export interface RouteEdgeData extends Record<string, unknown> {
  route: Route | null;
  label: string;
  names: string[];
  tooltip: string;
  bus: 'in' | 'out' | null;
  kind: GraphEdgeKind;
  dimmed: boolean;
  /** A line into an outputs box none of whose outputs is used later. */
  muted: boolean;
}

export type FlowEdge = Edge<RouteEdgeData, 'route'>;

type Size = { width: number; height: number };

/** How long to wait for the browser to measure nodes before laying out anyway. */
const MEASURE_WAIT_MS = 400;

/** The viewport that keeps a point on screen where it was after the point moved. */
export function keepInPlace(viewport: Viewport, before: XYPosition, after: XYPosition): Viewport {
  return {
    x: viewport.x - (after.x - before.x) * viewport.zoom,
    y: viewport.y - (after.y - before.y) * viewport.zoom,
    zoom: viewport.zoom,
  };
}

/** Changes in the node and edge id lists or the direction call for a new layout. */
export function layoutKey(view: View, direction: Direction): string {
  const nodes = view.nodes.map((node) => `${node.id}<${node.parentId ?? ''}`).join('|');
  const edges = view.edges.map((edge) => edge.id).join('|');
  return `${direction}#${nodes}#${edges}`;
}

/** React Flow nodes for a view at the given positions; unplaced nodes stay hidden. */
export function toFlowNodes(view: View, positions: Positions, measured: Map<string, Size>): FlowNode[] {
  return view.nodes.map((node) => {
    const box = positions[node.id];
    const container = CONTAINER_KINDS.has(node.kind);
    const size = measured.get(node.id);
    return {
      id: node.id,
      type: node.kind,
      position: box ? { x: box.x, y: box.y } : { x: 0, y: 0 },
      data: { node },
      ...(node.parentId ? { parentId: node.parentId } : {}),
      selected: node.selected,
      draggable: false,
      ...(container && box ? { style: { width: box.width, height: box.height } } : {}),
      ...(container ? { zIndex: -1 } : {}),
      // Fresh node objects without measured would be re-measured, hidden meanwhile.
      ...(size ? { measured: size } : {}),
      ...(box ? {} : { className: 'rc-unplaced' }),
    };
  });
}

/**
 * The hover text of an edge: `name = value` per input on an inputs box line,
 * the outside step outputs on a ghost line, otherwise every name.
 */
export function edgeTooltip(view: View, edge: ViewEdge): string {
  const source = view.nodes.find((node) => node.id === edge.source);
  if (source?.kind === 'inputsBox') {
    return edge.names
      .map((name) => {
        const row = source.data.rows.find((candidate) => candidate.name === name);
        return row ? `${name} = ${rowValue(row)}` : name;
      })
      .join('\n');
  }
  if (source?.kind === 'ghost') return edge.names.map((name) => `${source.data.stepId}.${name}`).join('\n');
  return edge.names.join('\n');
}

/** React Flow edges for a view, drawn along their routes where the layout has them. */
export function toFlowEdges(view: View, routes: Record<string, Route>): FlowEdge[] {
  return view.edges.map((edge) => ({
    id: edge.id,
    type: 'route',
    source: edge.source,
    target: edge.target,
    sourceHandle: edge.sourceHandle,
    targetHandle: edge.targetHandle,
    data: {
      route: routes[edge.id] ?? null,
      label: edge.label,
      names: edge.names,
      tooltip: edgeTooltip(view, edge),
      bus: edge.bus,
      kind: edge.kind,
      dimmed: edge.dimmed,
      muted: edge.muted,
    },
    className: [
      'rc-edge',
      `rc-edge-${edge.kind}`,
      edge.bus && `rc-edge-bus-${edge.bus}`,
      edge.muted && 'rc-edge-muted',
      edge.dimmed && 'rc-dimmed',
    ]
      .filter(Boolean)
      .join(' '),
    selectable: false,
  }));
}

export interface LayoutState {
  nodes: FlowNode[];
  edges: FlowEdge[];
  onNodesChange: (changes: NodeChange<FlowNode>[]) => void;
  /** Increments each time a layout is applied. */
  version: number;
  /** The layout on screen is for the current view. */
  current: boolean;
}

interface Applied {
  key: string;
  positions: Positions;
  routes: Record<string, Route>;
  /** The leaf node sizes the layout was computed with. */
  sizes: Map<string, Size>;
  version: number;
}

/** A measured size that differs from the one a layout used (or that it had to guess). */
export function resized(view: View, measured: Map<string, Size>, used: Map<string, Size>): boolean {
  return view.nodes.some((node) => {
    const size = measured.get(node.id);
    const before = used.get(node.id);
    if (!size || CONTAINER_KINDS.has(node.kind)) return false;
    return !before || Math.abs(size.width - before.width) >= 1 || Math.abs(size.height - before.height) >= 1;
  });
}

/**
 * Keep a view laid out.
 *
 * Nodes are rendered hidden first so the browser measures them; once all are
 * measured (or after a short wait, or at once where no worker can run) their
 * sizes go to ELK, the boxes are placed around the result (buses.ts) and the
 * positions and routes are applied. A size measured later than that, or
 * changed since (a step or box expanded), runs the layout again. Selection,
 * dimming and other data changes reuse the last layout.
 */
export function useLayout(view: View, direction: Direction): LayoutState {
  const [measured, setMeasured] = useState<Map<string, Size>>(() => new Map());
  const [layout, setLayout] = useState<Applied>({ key: '', positions: {}, routes: {}, sizes: new Map(), version: 0 });
  const [waited, setWaited] = useState('');
  const running = useRef(false);
  const key = useMemo(() => layoutKey(view, direction), [view, direction]);

  const onNodesChange = useCallback((changes: NodeChange<FlowNode>[]) => {
    const sizes = changes.filter((change) => change.type === 'dimensions' && change.dimensions);
    if (sizes.length === 0) return;
    setMeasured((previous) => {
      const next = new Map(previous);
      let changed = false;
      for (const change of sizes) {
        if (change.type !== 'dimensions' || !change.dimensions) continue;
        const old = next.get(change.id);
        const { width, height } = change.dimensions;
        if (old && old.width === width && old.height === height) continue;
        next.set(change.id, { width, height });
        changed = true;
      }
      return changed ? next : previous;
    });
  }, []);

  useEffect(() => {
    const timer = window.setTimeout(() => setWaited(key), MEASURE_WAIT_MS);
    return () => window.clearTimeout(timer);
  }, [key]);

  const allMeasured = view.nodes.every(
    (node) => CONTAINER_KINDS.has(node.kind) || measured.has(node.id),
  );
  const stale =
    layout.key !== key
      ? allMeasured || waited === key || !canUseWorker()
      : resized(view, measured, layout.sizes);

  useEffect(() => {
    if (!stale || running.current) return;
    running.current = true;
    const request = layoutRequest(view, direction, measured);
    const sizes = new Map<string, Size>();
    for (const node of request.nodes) {
      if (!node.container) sizes.set(node.id, { width: node.width, height: node.height });
    }
    void runLayout(request).then((core) => {
      const { positions, routes } = placeBuses(request, core);
      running.current = false;
      setLayout((previous) => ({ key, positions, routes, sizes, version: previous.version + 1 }));
    });
  }, [stale, key, view, direction, measured, layout.version]);

  const nodes = useMemo(
    () => toFlowNodes(view, layout.positions, measured),
    [view, layout.positions, measured],
  );
  const edges = useMemo(() => toFlowEdges(view, layout.routes), [view, layout.routes]);

  return { nodes, edges, onNodesChange, version: layout.version, current: layout.key === key };
}
