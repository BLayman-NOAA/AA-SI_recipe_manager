/**
 * Diagram layout: the ELK compound graph for a view's core, and a grid fallback.
 *
 * Pure. elk.ts runs the ELK graph in a web worker; this module only builds it
 * and reads the result, so it can be tested and timed in node. The core is
 * every node but the inputs and outputs boxes, whose lines buses.ts routes
 * around it.
 */
import type { ElkExtendedEdge, ElkNode, ElkPort } from 'elkjs/lib/elk-api';
import { opLabel } from '../api/graph';
import type { Direction } from '../state/view';
import { clip, firstSentence, plainText } from '../text';
import {
  CONTAINER_KINDS,
  includeMeta,
  IN_HANDLE,
  moreText,
  OUT_HANDLE,
  rowTag,
  rowValue,
  splitRows,
  type InputRow,
  type Port,
  type View,
  type ViewNode,
} from './buildView';

export interface Point {
  x: number;
  y: number;
}

export interface Size {
  width: number;
  height: number;
}

export interface LayoutBox extends Point, Size {}

/** An edge's polyline and label box, in absolute coordinates. */
export interface Route {
  points: Point[];
  label?: LayoutBox;
}

/** A node handle as an ELK port: on the input or output side, at a fraction along it. */
export interface LayoutPort {
  id: string;
  side: 'in' | 'out';
  fraction: number;
}

export interface LayoutNode {
  id: string;
  width: number;
  height: number;
  parent?: string;
  /** Containers get their size from the layout. */
  container?: boolean;
  /** Width of a container's header label. */
  labelWidth?: number;
  ports?: LayoutPort[];
  /** An inputs ('in') or outputs ('out') box, placed by buses.ts instead of ELK. */
  bus?: 'in' | 'out';
}

export interface LayoutEdge {
  id: string;
  source: string;
  target: string;
  /** Handle ids on the two ends; ports when the node has them. */
  sourcePort?: string;
  targetPort?: string;
  /** The label text and its size, so the layout leaves room for it. */
  label?: { text: string; width: number; height: number };
  /** A line of an inputs or outputs box. */
  bus?: 'in' | 'out';
}

export interface LayoutRequest {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  direction: Direction;
}

/** Positions relative to each node's parent, as React Flow wants them. */
export type Positions = Record<string, LayoutBox>;

/** A laid out view: node positions and edge routes. */
export interface Layout {
  positions: Positions;
  routes: Record<string, Route>;
}

/** Room inside a container for its header row. */
export const CONTAINER_HEADER = 40;
export const CONTAINER_PADDING = 16;

export const ROOT_OPTIONS: Record<string, string> = {
  'elk.algorithm': 'layered',
  'elk.edgeRouting': 'ORTHOGONAL',
  'elk.hierarchyHandling': 'INCLUDE_CHILDREN',
  'elk.layered.spacing.nodeNodeBetweenLayers': '80',
  'elk.spacing.nodeNode': '28',
  'elk.layered.spacing.edgeNodeBetweenLayers': '30',
  'elk.layered.spacing.edgeEdgeBetweenLayers': '12',
  'elk.spacing.edgeNode': '20',
  'elk.spacing.edgeEdge': '10',
  'elk.spacing.edgeLabel': '4',
  'elk.edgeLabels.inline': 'true',
  'elk.edgeLabels.placement': 'CENTER',
  'elk.layered.considerModelOrder.strategy': 'NODES_AND_EDGES',
  'elk.layered.nodePlacement.strategy': 'BRANDES_KOEPF',
  'elk.layered.nodePlacement.bk.fixedAlignment': 'BALANCED',
  'elk.layered.mergeEdges': 'false',
};

const PAD = CONTAINER_PADDING;

const CONTAINER_OPTIONS: Record<string, string> = {
  'elk.padding': `[top=${CONTAINER_HEADER},left=${PAD},bottom=${PAD},right=${PAD}]`,
};

/**
 * The diagram's text size relative to the sizes below, as diagram.css applies
 * it through `--rc-text-scale`. Set by useLayout before each layout.
 */
let textScale = 1;

/** Set the text size the size estimates and edge label boxes assume. */
export function setTextScale(scale: number): void {
  textScale = scale;
}

/** A length that grows with the text size. */
function scaled(px: number): number {
  return px * textScale;
}

const CHAR_WIDTH = 7;
const LINE_HEIGHT = 16;

/** Edge labels: 10 px monospace with 4 px side padding on a 16 px line. */
const LABEL_CHAR_WIDTH = 6.5;

/** The height of an edge label. */
export function labelHeight(): number {
  return scaled(16);
}

/** The width of a line of node text. */
export function textWidth(text: string): number {
  return text.length * scaled(CHAR_WIDTH);
}

/** The width of an edge label. */
export function labelWidth(text: string): number {
  return text.length * scaled(LABEL_CHAR_WIDTH) + 8;
}

/** The width of a mapped group's header label. */
export function groupLabelWidth(label: string): number {
  return textWidth(label) + 18;
}

/** Nodes sized to their content: the least and most width per kind (diagram.css follows these). */
export const MIN_WIDTH = { step: 170, include: 220, inputsBox: 220, outputsBox: 200 } as const;
export const MAX_WIDTH = { step: 360, include: 360, inputsBox: 320, outputsBox: 320 } as const;
/** Horizontal padding and borders inside a node, per kind. */
export const SIDE_PAD = { step: 32, include: 32, inputsBox: 24, outputsBox: 24 } as const;

/** Room for one header button (chevron, drill or play) beside a node's header text. */
const TOOLS_WIDTH = 30;

type SizedKind = keyof typeof MIN_WIDTH;

/**
 * A line of text in a node and what CSS does when it is wider than the node:
 * wraps onto more lines, is clipped with an ellipsis, or neither.
 */
export interface NodeLine {
  text: string;
  /** Width taken before the text on the same line (a name column, a button). */
  indent: number;
  overflow: 'wrap' | 'clip' | 'none';
}

function line(text: string, overflow: NodeLine['overflow'], indent = 0): NodeLine {
  return { text, indent, overflow };
}

/**
 * The text lines of a node that set its width or can wrap, mirroring its
 * component: StepNode, IncludeNode, and InputsBoxNode and OutputsBoxNode in
 * SmallNodes.
 */
export function nodeLines(node: ViewNode): NodeLine[] {
  switch (node.kind) {
    case 'step': {
      const { step, expanded, params } = node.data;
      const names = Math.max(0, ...params.map((param) => textWidth(param.name))) + 8;
      return [
        line(step.id, 'wrap', 2 * TOOLS_WIDTH),
        line(opLabel(step), 'wrap'),
        line(firstSentence(plainText(step.description)), 'clip'),
        ...(expanded ? params.map((param) => line(clip(param.value || 'none'), 'wrap', names)) : []),
      ];
    }
    case 'include': {
      const { include, inPorts, outPorts, expanded, rows } = node.data;
      const widest = (ports: Port[]) => Math.max(0, ...ports.map((port) => textWidth(port.label)));
      return [
        line(include.title, 'wrap', 2 * TOOLS_WIDTH),
        line(includeMeta(node.data), 'wrap'),
        line(include.source, 'wrap'),
        ...(inPorts.length + outPorts.length > 0 ? [line('', 'none', widest(inPorts) + widest(outPorts) + 12)] : []),
        ...(expanded ? inputListLines(rows, true, 0) : []),
      ];
    }
    case 'inputsBox': {
      const { title, include, expanded, rows, more } = node.data;
      return [line(title, 'clip', TOOLS_WIDTH), ...(expanded ? inputListLines(rows, include !== null, more) : [])];
    }
    case 'outputsBox': {
      const { ownerLabel, summary, expanded, rows } = node.data;
      return [
        line(`Outputs ${ownerLabel}`, 'clip', TOOLS_WIDTH),
        line(summary, 'wrap'),
        ...(expanded
          ? rows.flatMap((row) => [
              line(`${row.name} ${row.type ?? ''} ${row.kind === 'final' ? 'recipe output' : ''}`, 'wrap'),
              ...(row.kind === 'exit' ? [line(`used by ${row.by.join(', ')}`, 'wrap')] : []),
            ])
          : []),
      ];
    }
    default:
      return [];
  }
}

/** The section headers an inputs list shows: Overrides and Inputs, each when it has rows. */
function sectionCount(rows: InputRow[], sections: boolean): number {
  if (!sections) return 0;
  const { overrides, inputs } = splitRows(rows);
  return Number(overrides.length > 0) + Number(inputs.length > 0);
}

/** The line an `other` row adds under its value. */
function declaredLine(row: InputRow): string | null {
  return row.source === 'other' && row.declaredIn ? `declared in ${row.declaredIn}` : null;
}

/**
 * The lines of an inputs list (InputRows in SmallNodes): the section headers,
 * each row's name, value with its tag and where it comes from, and the more row.
 */
function inputListLines(rows: InputRow[], sections: boolean, more: number): NodeLine[] {
  const rowLines = (row: InputRow) => {
    const tag = rowTag(row);
    const declared = declaredLine(row);
    return [
      line(row.name, 'wrap'),
      line(`${clip(rowValue(row))}${tag ? ` ${tag}` : ''}`, 'wrap'),
      ...(row.from.length > 0 ? [line(`from ${row.from.map((ref) => ref.step).join(', ')}`, 'wrap')] : []),
      ...(declared ? [line(declared, 'wrap')] : []),
    ];
  };
  const { overrides, inputs } = splitRows(rows);
  return [
    ...(sections && overrides.length > 0 ? [line('Overrides', 'none')] : []),
    ...overrides.flatMap(rowLines),
    ...(sections && inputs.length > 0 ? [line('Inputs', 'none')] : []),
    ...inputs.flatMap(rowLines),
    ...(more > 0 ? [line(moreText(more), 'wrap')] : []),
  ];
}

/**
 * The height an expanded inputs list adds beyond 32 px a row: 18 px per section
 * header and for the more row, a line per `from` and per `declared in`.
 */
function listExtra(rows: InputRow[], sections: boolean, more: number): number {
  const notes = rows.filter((row) => row.from.length > 0).length + rows.filter((row) => declaredLine(row)).length;
  return (sectionCount(rows, sections) + Number(more > 0)) * scaled(18) + notes * scaled(LINE_HEIGHT);
}

/** How wide a node grows for its lines, between its least and most width. */
function contentWidth(kind: SizedKind, lines: NodeLine[]): number {
  const widest = Math.max(0, ...lines.map((item) => item.indent + textWidth(item.text)));
  return Math.min(scaled(MAX_WIDTH[kind]), Math.max(scaled(MIN_WIDTH[kind]), widest + SIDE_PAD[kind]));
}

/** The extra height of the lines that wrap at a width. */
function wrapHeight(kind: SizedKind, lines: NodeLine[], width: number): number {
  let extra = 0;
  for (const item of lines) {
    if (item.overflow !== 'wrap') continue;
    const room = Math.max(scaled(CHAR_WIDTH), width - SIDE_PAD[kind] - item.indent);
    extra += Math.max(1, Math.ceil(textWidth(item.text) / room)) - 1;
  }
  return extra * scaled(LINE_HEIGHT);
}

/**
 * A size guess for a node before the browser has measured it.
 *
 * Nodes are as wide as their longest line, between a least and a most width,
 * and lines wider than that wrap, as diagram.css lays them out. Used for the
 * first layout, in tests and as the grid fallback's cell size.
 */
export function estimateSize(node: ViewNode): Size {
  switch (node.kind) {
    case 'step': {
      const { badges, expanded, params } = node.data;
      const lines = nodeLines(node);
      const width = contentWidth('step', lines);
      const list = expanded ? 8 + Math.max(1, params.length) * scaled(LINE_HEIGHT) : 0;
      return { width, height: scaled(66) + (badges.length > 0 ? scaled(20) : 0) + list + wrapHeight('step', lines, width) };
    }
    case 'include': {
      const { inPorts, outPorts, expanded, rows } = node.data;
      const lines = nodeLines(node);
      const width = contentWidth('include', lines);
      const ports = Math.max(inPorts.length, outPorts.length);
      const list = expanded ? 8 + Math.max(1, rows.length) * scaled(32) + listExtra(rows, true, 0) : 0;
      return { width, height: scaled(80) + ports * scaled(LINE_HEIGHT) + list + wrapHeight('include', lines, width) };
    }
    case 'inputsBox': {
      const { include, expanded, rows, more } = node.data;
      const lines = nodeLines(node);
      const width = contentWidth('inputsBox', lines);
      const list = expanded ? rows.length * scaled(32) + listExtra(rows, include !== null, more) : 0;
      return { width, height: scaled(44) + list + wrapHeight('inputsBox', lines, width) };
    }
    case 'outputsBox': {
      const { expanded, rows } = node.data;
      const lines = nodeLines(node);
      const width = contentWidth('outputsBox', lines);
      const list = expanded ? rows.length * scaled(18) + rows.filter((row) => row.kind === 'exit').length * scaled(LINE_HEIGHT) : 0;
      return { width, height: scaled(40) + list + wrapHeight('outputsBox', lines, width) };
    }
    case 'ghost':
      return { width: Math.max(textWidth(node.data.stepId) + 32, 140), height: 52 };
    default:
      return { width: 150, height: 44 };
  }
}

/** Where a handle sits along its side, matching Handles in nodes/common.tsx. */
function handleFraction(node: ViewNode, handle: string): number {
  const at = (index: number, count: number) => (index < 0 ? 0.5 : (index + 1) / (count + 1));
  if (handle === IN_HANDLE || handle === OUT_HANDLE) return 0.5;
  if (node.kind === 'step') {
    const { inputs, outputs } = node.data.step;
    if (handle.startsWith('in:')) return at(inputs.findIndex((port) => `in:${port.name}` === handle), inputs.length);
    return at(outputs.findIndex((port) => `out:${port.name}` === handle), outputs.length);
  }
  if (node.kind === 'include') {
    const ports = handle.startsWith('in:') ? node.data.inPorts : node.data.outPorts;
    return at(ports.findIndex((port) => port.id === handle), ports.length);
  }
  return 0.5;
}

/** The ports each node needs: one per handle a core edge uses. */
function nodePorts(view: View): Map<string, LayoutPort[]> {
  const byId = new Map(view.nodes.map((node) => [node.id, node]));
  const ports = new Map<string, LayoutPort[]>();
  const add = (nodeId: string, handle: string, side: 'in' | 'out') => {
    const node = byId.get(nodeId);
    if (!node || CONTAINER_KINDS.has(node.kind)) return;
    const list = ports.get(nodeId) ?? [];
    if (!list.some((port) => port.id === handle)) {
      list.push({ id: handle, side, fraction: handleFraction(node, handle) });
    }
    ports.set(nodeId, list);
  };
  for (const edge of view.edges) {
    if (edge.bus) continue;
    add(edge.source, edge.sourceHandle, 'out');
    add(edge.target, edge.targetHandle, 'in');
  }
  return ports;
}

/**
 * The layout request for a view.
 *
 * Args:
 *   view: The folded view.
 *   direction: Layout direction.
 *   sizes: Measured node sizes; nodes missing here get estimateSize.
 */
export function layoutRequest(
  view: View,
  direction: Direction,
  sizes: Map<string, Size> = new Map(),
): LayoutRequest {
  const ports = nodePorts(view);
  return {
    direction,
    nodes: view.nodes.map((node) => {
      if (node.kind === 'mapGroup') {
        return {
          id: node.id,
          width: 0,
          height: 0,
          container: true,
          labelWidth: groupLabelWidth(node.data.label),
          ...(node.parentId ? { parent: node.parentId } : {}),
        };
      }
      const bus = node.kind === 'inputsBox' ? 'in' : node.kind === 'outputsBox' ? 'out' : null;
      return {
        id: node.id,
        ...(sizes.get(node.id) ?? estimateSize(node)),
        ...(node.parentId ? { parent: node.parentId } : {}),
        ...(ports.has(node.id) ? { ports: ports.get(node.id) } : {}),
        ...(bus ? { bus } : {}),
      };
    }),
    edges: view.edges.map((edge) => ({
      id: edge.id,
      source: edge.source,
      target: edge.target,
      sourcePort: edge.sourceHandle,
      targetPort: edge.targetHandle,
      ...(edge.label ? { label: { text: edge.label, width: labelWidth(edge.label), height: labelHeight() } } : {}),
      ...(edge.bus ? { bus: edge.bus } : {}),
    })),
  };
}

/** The part of a request ELK lays out: everything but the boxes and their lines. */
export function coreNodes(request: LayoutRequest): LayoutRequest {
  return {
    direction: request.direction,
    nodes: request.nodes.filter((node) => !node.bus),
    edges: request.edges.filter((edge) => !edge.bus),
  };
}

/** Container ids around a node, outermost first. */
function ancestors(id: string, parents: Map<string, string | undefined>): string[] {
  const chain: string[] = [];
  let parent = parents.get(id);
  while (parent && chain.length <= parents.size) {
    chain.unshift(parent);
    parent = parents.get(parent);
  }
  return chain;
}

/**
 * An ELK port at a fixed position on the node's input or output side.
 *
 * Ports are 1 px squares just inside the node's border (a border offset of
 * -1), centered on the handle, so ELK's routes end on the border where React
 * Flow draws the handle.
 */
function elkPort(node: LayoutNode, port: LayoutPort, direction: Direction): ElkPort {
  const across = direction === 'RIGHT' ? node.height : node.width;
  const along = port.side === 'in' ? 0 : (direction === 'RIGHT' ? node.width : node.height) - 1;
  const offset = port.fraction * across - 0.5;
  const sides = direction === 'RIGHT' ? ['WEST', 'EAST'] : ['NORTH', 'SOUTH'];
  return {
    id: `${node.id}.${port.id}`,
    width: 1,
    height: 1,
    ...(direction === 'RIGHT' ? { x: along, y: offset } : { x: offset, y: along }),
    layoutOptions: { 'elk.port.side': port.side === 'in' ? sides[0] : sides[1], 'elk.port.borderOffset': '-1' },
  };
}

/**
 * The ELK graph for the core of a layout request.
 *
 * Containers hold their children; each edge sits in the deepest container
 * holding both its ends, which ELK needs for edges across hierarchy levels.
 * Leaf nodes have fixed ports where their handles are, and edges name them.
 */
export function elkGraph(request: LayoutRequest): ElkNode {
  const core = coreNodes(request);
  const root: ElkNode = {
    id: 'root',
    layoutOptions: { ...ROOT_OPTIONS, 'elk.direction': request.direction },
    children: [],
    edges: [],
  };
  const elkNodes = new Map<string, ElkNode>([['root', root]]);
  const parents = new Map(core.nodes.map((node) => [node.id, node.parent]));
  const portIds = new Set<string>();

  for (const node of core.nodes) {
    if (node.container) {
      elkNodes.set(node.id, { id: node.id, layoutOptions: CONTAINER_OPTIONS, children: [], edges: [] });
      continue;
    }
    const ports = (node.ports ?? []).map((port) => elkPort(node, port, request.direction));
    for (const port of ports) portIds.add(port.id);
    elkNodes.set(node.id, {
      id: node.id,
      width: node.width,
      height: node.height,
      layoutOptions: { 'elk.portConstraints': 'FIXED_POS' },
      ports,
    });
  }
  for (const node of core.nodes) {
    const parent = elkNodes.get(node.parent ?? 'root') ?? root;
    parent.children!.push(elkNodes.get(node.id)!);
  }
  const end = (node: string, port: string | undefined) =>
    port && portIds.has(`${node}.${port}`) ? `${node}.${port}` : node;
  for (const edge of core.edges) {
    if (!elkNodes.has(edge.source) || !elkNodes.has(edge.target)) continue;
    const from = ancestors(edge.source, parents);
    const to = ancestors(edge.target, parents);
    let shared = 'root';
    for (let i = 0; i < Math.min(from.length, to.length) && from[i] === to[i]; i += 1) {
      shared = from[i];
    }
    const elkEdge: ElkExtendedEdge = {
      id: edge.id,
      sources: [end(edge.source, edge.sourcePort)],
      targets: [end(edge.target, edge.targetPort)],
    };
    if (edge.label) elkEdge.labels = [{ ...edge.label }];
    elkNodes.get(shared)!.edges!.push(elkEdge);
  }
  return root;
}

/** Node boxes from a laid out ELK graph, relative to their parents. */
export function readPositions(root: ElkNode): Positions {
  const positions: Positions = {};
  const visit = (node: ElkNode) => {
    for (const child of node.children ?? []) {
      positions[child.id] = {
        x: child.x ?? 0,
        y: child.y ?? 0,
        width: child.width ?? 0,
        height: child.height ?? 0,
      };
      visit(child);
    }
  };
  visit(root);
  return positions;
}

/**
 * Node positions and absolute edge routes from a laid out ELK graph.
 *
 * ELK gives an edge's sections and labels relative to the node named by its
 * `container` ('root' for root edges), so the container's absolute position
 * is added.
 */
export function readLayout(root: ElkNode): Layout {
  const absolute = new Map<string, Point>([['root', { x: 0, y: 0 }]]);
  const place = (node: ElkNode, origin: Point) => {
    for (const child of node.children ?? []) {
      const at = { x: origin.x + (child.x ?? 0), y: origin.y + (child.y ?? 0) };
      absolute.set(child.id, at);
      place(child, at);
    }
  };
  place(root, { x: 0, y: 0 });

  const routes: Record<string, Route> = {};
  const visit = (node: ElkNode) => {
    for (const edge of node.edges ?? []) {
      const origin = absolute.get(edge.container ?? node.id) ?? { x: 0, y: 0 };
      const shift = (point: Point) => ({ x: point.x + origin.x, y: point.y + origin.y });
      const points = (edge.sections ?? []).flatMap((section) =>
        [section.startPoint, ...(section.bendPoints ?? []), section.endPoint].map(shift),
      );
      const label = edge.labels?.[0];
      routes[edge.id] = {
        points,
        ...(label && label.x !== undefined && label.y !== undefined
          ? { label: { ...shift({ x: label.x, y: label.y }), width: label.width ?? 0, height: label.height ?? 0 } }
          : {}),
      };
    }
    for (const child of node.children ?? []) visit(child);
  };
  visit(root);
  return { positions: readPositions(root), routes };
}

/** Absolute boxes of a request's nodes from parent relative positions. */
export function absoluteBoxes(request: LayoutRequest, positions: Positions): Record<string, LayoutBox> {
  const parents = new Map(request.nodes.map((node) => [node.id, node.parent]));
  const boxes: Record<string, LayoutBox> = {};
  for (const node of request.nodes) {
    const box = positions[node.id];
    if (!box) continue;
    let { x, y } = box;
    for (const parent of ancestors(node.id, parents)) {
      x += positions[parent]?.x ?? 0;
      y += positions[parent]?.y ?? 0;
    }
    boxes[node.id] = { x, y, width: box.width, height: box.height };
  }
  return boxes;
}

const GRID_COLUMNS = 4;
const GRID_GAP = 32;

/**
 * A plain grid, used when ELK is unavailable so the diagram is never blank.
 *
 * Children are placed in rows of four inside their container, innermost
 * containers first, so each container's size is known before its parent's.
 * Boxes are placed too; there are no routes.
 */
export function gridLayout(request: LayoutRequest): Positions {
  const positions: Positions = {};
  const sizes = new Map(request.nodes.map((node) => [node.id, { width: node.width, height: node.height }]));
  const parents = new Map(request.nodes.map((node) => [node.id, node.parent]));
  const children = new Map<string, LayoutNode[]>();
  for (const node of request.nodes) {
    const key = node.parent ?? '';
    children.set(key, [...(children.get(key) ?? []), node]);
  }
  const containers = request.nodes
    .filter((node) => node.container)
    .sort((a, b) => ancestors(b.id, parents).length - ancestors(a.id, parents).length);

  const place = (key: string, left: number, top: number) => {
    let x = left;
    let y = top;
    let rowHeight = 0;
    let width = 0;
    (children.get(key) ?? []).forEach((node, index) => {
      if (index > 0 && index % GRID_COLUMNS === 0) {
        x = left;
        y += rowHeight + GRID_GAP;
        rowHeight = 0;
      }
      const size = sizes.get(node.id)!;
      positions[node.id] = { x, y, ...size };
      x += size.width + GRID_GAP;
      width = Math.max(width, x - GRID_GAP - left);
      rowHeight = Math.max(rowHeight, size.height);
    });
    return { width, height: y + rowHeight - top };
  };

  for (const container of containers) {
    const inner = place(container.id, CONTAINER_PADDING, CONTAINER_HEADER);
    sizes.set(container.id, {
      width: Math.max(inner.width + 2 * CONTAINER_PADDING, 160),
      height: inner.height + CONTAINER_HEADER + CONTAINER_PADDING,
    });
  }
  place('', 0, 0);
  return positions;
}
