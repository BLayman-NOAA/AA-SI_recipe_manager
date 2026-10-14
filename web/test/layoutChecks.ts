/**
 * Overlap checks for a laid out view: lines through nodes or labels, labels on
 * nodes or on each other, crossing box lines, and where the boxes sit.
 */
import type { View } from '../src/diagram/buildView';
import { grow, LABEL_CLEARANCE, segmentHitsRect } from '../src/diagram/buses';
import { absoluteBoxes, type Layout, type LayoutBox, type LayoutRequest, type Point } from '../src/diagram/layout';

export interface LayoutProblems {
  /** Core edge segments through a node that is not one of the edge's ends. */
  coreHits: string[];
  /** Box line segments through a node or box that is not one of the line's ends. */
  busHits: string[];
  /** Labels on a node or on another label. */
  labelHits: string[];
  /** Lines through their own label, or nearer another label than LABEL_CLEARANCE. */
  lineLabelHits: string[];
  /** Input lines crossing each other, or output lines crossing each other. */
  crossings: string[];
  /** Boxes on the wrong side of the core. */
  placement: string[];
  /** Box lines without a route. */
  missing: string[];
  /**
   * Output lines whose box is not their source's, whose label names an output
   * the box does not list, or whose label is nearer another box than its own.
   */
  labelMatches: string[];
}

const PAD = 0.5;

function segments(points: Point[]): [Point, Point][] {
  return points.slice(1).map((point, index) => [points[index], point]);
}

/** Whether two axis aligned segments cross or overlap, ends excluded. */
function segmentsMeet([a, b]: [Point, Point], [c, d]: [Point, Point]): boolean {
  const box = (p: Point, q: Point) => ({
    x: Math.min(p.x, q.x),
    y: Math.min(p.y, q.y),
    width: Math.abs(p.x - q.x),
    height: Math.abs(p.y - q.y),
  });
  const first = box(a, b);
  const second = box(c, d);
  const overlapX = Math.min(first.x + first.width, second.x + second.width) - Math.max(first.x, second.x);
  const overlapY = Math.min(first.y + first.height, second.y + second.height) - Math.max(first.y, second.y);
  if (overlapX < -PAD || overlapY < -PAD) return false;
  const horizontal = (s: LayoutBox) => s.height < PAD;
  if (horizontal(first) !== horizontal(second)) {
    const h = horizontal(first) ? first : second;
    const v = horizontal(first) ? second : first;
    return v.x > h.x + PAD && v.x < h.x + h.width - PAD && h.y > v.y + PAD && h.y < v.y + v.height - PAD;
  }
  if (horizontal(first)) return Math.abs(first.y - second.y) < PAD && overlapX > PAD;
  return Math.abs(first.x - second.x) < PAD && overlapY > PAD;
}

function linesCross(name: string, routes: [string, Point[]][]): string[] {
  const found: string[] = [];
  routes.forEach(([a, first], i) => {
    for (const [b, second] of routes.slice(i + 1)) {
      if (segments(first).some((s) => segments(second).some((t) => segmentsMeet(s, t)))) {
        found.push(`${name}: ${a} x ${b}`);
      }
    }
  });
  return found;
}

/**
 * Every overlap problem of a full layout (core plus boxes).
 *
 * Args:
 *   view: The view laid out.
 *   request: Its layout request.
 *   layout: Positions and routes, boxes included.
 */
export function layoutProblems(view: View, request: LayoutRequest, layout: Layout): LayoutProblems {
  const boxes = absoluteBoxes(request, layout.positions);
  const leaves = request.nodes.filter((node) => !node.container && boxes[node.id]);
  const core = leaves.filter((node) => !node.bus);
  const problems: LayoutProblems = {
    coreHits: [],
    busHits: [],
    labelHits: [],
    lineLabelHits: [],
    crossings: [],
    placement: [],
    missing: [],
    labelMatches: [],
  };

  for (const edge of view.edges) {
    const route = layout.routes[edge.id];
    if (!route) {
      if (edge.bus) problems.missing.push(edge.id);
      continue;
    }
    const hits = edge.bus ? problems.busHits : problems.coreHits;
    for (const [a, b] of segments(route.points)) {
      for (const node of edge.bus ? leaves : core) {
        if (node.id === edge.source || node.id === edge.target) continue;
        if (segmentHitsRect(a, b, boxes[node.id], PAD)) hits.push(`${edge.id} through ${node.id}`);
      }
    }
  }

  const labels = view.edges
    .filter((edge) => layout.routes[edge.id]?.label)
    .map((edge) => ({ id: edge.id, box: layout.routes[edge.id].label! }));
  labels.forEach((label, i) => {
    const corner = { x: label.box.x + label.box.width, y: label.box.y + label.box.height };
    for (const node of leaves) {
      if (segmentHitsRect(label.box, corner, boxes[node.id], PAD)) {
        problems.labelHits.push(`${label.id} label on ${node.id}`);
      }
    }
    for (const other of labels.slice(i + 1)) {
      if (segmentHitsRect(label.box, corner, other.box, PAD)) {
        problems.labelHits.push(`${label.id} label on ${other.id} label`);
      }
    }
  });

  for (const edge of view.edges) {
    for (const [a, b] of segments(layout.routes[edge.id]?.points ?? [])) {
      for (const label of labels) {
        const hit =
          label.id === edge.id
            ? segmentHitsRect(a, b, label.box, PAD)
            : segmentHitsRect(a, b, grow(label.box, LABEL_CLEARANCE));
        if (hit) problems.lineLabelHits.push(`${edge.id} through or near ${label.id} label`);
      }
    }
  }

  const routesOf = (bus: 'in' | 'out') =>
    view.edges
      .filter((edge) => edge.bus === bus && layout.routes[edge.id])
      .map((edge): [string, Point[]] => [edge.id, layout.routes[edge.id].points]);
  problems.crossings.push(...linesCross('in', routesOf('in')), ...linesCross('out', routesOf('out')));

  if (core.length > 0) {
    const coreBoxes = core.map((node) => boxes[node.id]);
    const left = Math.min(...coreBoxes.map((box) => box.x));
    const top = Math.min(...coreBoxes.map((box) => box.y));
    const right = Math.max(...coreBoxes.map((box) => box.x + box.width));
    const bottom = Math.max(...coreBoxes.map((box) => box.y + box.height));
    const down = request.direction === 'DOWN';
    for (const node of leaves.filter((leaf) => leaf.bus)) {
      const box = boxes[node.id];
      if (node.bus === 'in') {
        const ok = down ? box.y + box.height <= top : box.x + box.width <= left;
        if (!ok) problems.placement.push(`${node.id} not ${down ? 'above' : 'left of'} the core`);
      } else if (box.x < right || box.y < bottom) {
        problems.placement.push(`${node.id} not right of and below the core`);
      }
    }
  }
  problems.labelMatches.push(...labelMatches(view, boxes, layout));
  return problems;
}

/** The distance from a point to a box, zero inside it. */
function distance(point: Point, box: LayoutBox): number {
  const dx = Math.max(box.x - point.x, 0, point.x - (box.x + box.width));
  const dy = Math.max(box.y - point.y, 0, point.y - (box.y + box.height));
  return Math.hypot(dx, dy);
}

/** Output lines whose label does not read as their box's. */
function labelMatches(view: View, boxes: Record<string, LayoutBox>, layout: Layout): string[] {
  const found: string[] = [];
  const outBoxes = view.nodes.filter((node) => node.kind === 'outputsBox' && boxes[node.id]);
  for (const edge of view.edges.filter((candidate) => candidate.bus === 'out')) {
    const box = view.nodes.find((node) => node.id === edge.target);
    if (box?.kind !== 'outputsBox' || box.data.owner !== edge.source) {
      found.push(`${edge.id} goes to a box of another producer`);
      continue;
    }
    const outputs = box.data.rows.map((row) => row.name.slice(row.name.lastIndexOf('.') + 1));
    const named = edge.label.replace(/ \+\d+ more$/, '').split(', ');
    for (const name of named.filter((item) => !outputs.includes(item))) {
      found.push(`${edge.id} label names ${name}, which its box does not list`);
    }
    const label = layout.routes[edge.id]?.label;
    if (!label) continue;
    const center = { x: label.x + label.width / 2, y: label.y + label.height / 2 };
    const own = distance(center, boxes[box.id]);
    const nearer = outBoxes.find((other) => other.id !== box.id && distance(center, boxes[other.id]) < own);
    if (nearer) found.push(`${edge.id} label is nearer ${nearer.id} than its own box`);
  }
  return found;
}

/** The number of problems of each kind. */
export function problemCounts(problems: LayoutProblems): Record<keyof LayoutProblems, number> {
  return Object.fromEntries(Object.entries(problems).map(([key, list]) => [key, list.length])) as Record<
    keyof LayoutProblems,
    number
  >;
}
