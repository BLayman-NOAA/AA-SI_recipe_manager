/**
 * Place the inputs and outputs boxes around the laid out core and route their lines.
 *
 * Pure. ELK lays out the steps (layout.ts); this module puts the inputs box
 * above and left of them, with one lane per target in a channel above the
 * core, and the outputs boxes side by side below and right, with one lane per
 * producer in a channel below it. A line that would pass through a node or a
 * label drops in the gap beside its column instead. Lanes are ordered by
 * where their lines turn toward the core, which nests them so no two lines of
 * a channel cross. Labels are placed once every line is routed, clear of
 * nodes and labels and LABEL_CLEARANCE away from every other line. An input
 * line's label sits near its target; an output line's label sits at the box
 * end of the line, so it reads as the box's.
 *
 * The routing is written for left to right. For top to bottom every
 * rectangle is mirrored on the diagonal before routing and every result
 * after, so only the first choice of label place differs between the two.
 *
 * A ghost that ELK puts in a later layer than the steps it feeds can make its
 * line into the inputs box cross an input line; that is rare and left as is.
 */
import type { Direction } from '../state/view';
import {
  absoluteBoxes,
  labelHeight,
  labelWidth,
  type Layout,
  type LayoutBox,
  type LayoutRequest,
  type Point,
  type Positions,
  type Route,
  type Size,
} from './layout';

export interface BusInput {
  direction: Direction;
  /** Absolute boxes of the core's leaf nodes. */
  core: Record<string, LayoutBox>;
  /** Absolute boxes of mapped group containers and their header label widths. */
  containers: Record<string, { box: LayoutBox; labelWidth: number }>;
  coreRoutes: Route[];
  /**
   * The inputs box, the nodes its lines go to and, drilled in, the ghosts
   * with a line into it.
   */
  inputsBox: {
    id: string;
    size: Size;
    targets: { id: string; label: string }[];
    feeders: { id: string; label: string }[];
  } | null;
  outputBoxes: { id: string; owner: string; size: Size; label: string }[];
}

/** Distance between neighbouring lanes: room for a label above a lane, clear of the lane above. */
const LANE = 28;
/** Space between the core and the first lane. */
const GAP = 28;
/** Length of the last leg into a node from a drop beside its column. */
const APPROACH = 10;
/** Distance of a drop beside a column from the column's nodes. */
const DROP_IN = 14;
/** Spacing of neighbouring drops and box exits. */
const DROP_STEP = 8;
/** How many drops beside a column are tried before giving up on a clear one. */
const MAX_DROPS = 16;
/** Space between the inputs box and its lanes. */
const BOX_GAP = 24;
/** Space between the core and the first outputs box. */
const OUT_OFFSET = 32;
/** Space between neighbouring outputs boxes. */
const BOXES_GAP = 16;
/** Distance of the first and last line from the end of a box side. */
const BOX_INSET = 10;
/** Length of the stretch of a group's edge, past its header label, a line into the group may end on. */
const GROUP_LANDING = 80;
/** How far a label moves at a time when its first place is taken. */
const LABEL_SLIDE = 4;
/** The least distance between a label and any line but its own. */
export const LABEL_CLEARANCE = 10;

interface Obstacle extends LayoutBox {
  id: string;
}

/** How a label may move from its first place: a step, how far, and where to look after. */
interface LabelMoves {
  slide: Point;
  room: number;
  /** Search the route's own segments from its end (near the target) first. */
  fromEnd: boolean;
}

/** A label to place once every line is routed. */
interface PendingLabel extends LabelMoves {
  edge: string;
  box: LayoutBox;
}

/** A line segment and the edge it belongs to, or null for a core edge. */
interface Line {
  edge: string | null;
  a: Point;
  b: Point;
}

/**
 * Whether a segment passes through the inside of a rectangle.
 *
 * Args:
 *   a: One end of the segment.
 *   b: The other end.
 *   rect: The rectangle.
 *   pad: Shrinks the rectangle on every side, so touching its edge is not a hit.
 */
export function segmentHitsRect(a: Point, b: Point, rect: LayoutBox, pad = 0): boolean {
  const left = Math.min(a.x, b.x);
  const right = Math.max(a.x, b.x);
  const top = Math.min(a.y, b.y);
  const bottom = Math.max(a.y, b.y);
  return (
    left < rect.x + rect.width - pad &&
    right > rect.x + pad &&
    top < rect.y + rect.height - pad &&
    bottom > rect.y + pad
  );
}

/** Whether two boxes overlap by more than a touch; a zero width box is a segment. */
function overlaps(a: LayoutBox, b: LayoutBox): boolean {
  return segmentHitsRect(a, { x: a.x + a.width, y: a.y + a.height }, b, 0.5);
}

/** A box grown by a margin on every side. */
export function grow(box: LayoutBox, by: number): LayoutBox {
  return { x: box.x - by, y: box.y - by, width: box.width + 2 * by, height: box.height + 2 * by };
}

function segmentBox(a: Point, b: Point): LayoutBox {
  return { x: Math.min(a.x, b.x), y: Math.min(a.y, b.y), width: Math.abs(a.x - b.x), height: Math.abs(a.y - b.y) };
}

function segments(points: Point[]): [Point, Point][] {
  return points.slice(1).map((point, index) => [points[index], point]);
}

function centerX(box: LayoutBox): number {
  return box.x + box.width / 2;
}

/** The bounds of the core: its nodes, every route point and every label. */
function coreExtent(boxes: LayoutBox[], points: Point[]) {
  const all = [...boxes, ...points.map((p) => ({ x: p.x, y: p.y, width: 0, height: 0 }))];
  if (all.length === 0) return { left: 0, top: 0, right: 0, bottom: 0 };
  return {
    left: Math.min(...all.map((box) => box.x)),
    top: Math.min(...all.map((box) => box.y)),
    right: Math.max(...all.map((box) => box.x + box.width)),
    bottom: Math.max(...all.map((box) => box.y + box.height)),
  };
}

/** The header label of a group container, in its top left corner. */
function groupHeader(box: LayoutBox, width: number): LayoutBox {
  return { x: box.x + 10, y: box.y + 8, width, height: 20 };
}

/** Places for a label beside each segment of its own route, as a last resort. */
function labelPlacesAlong(points: Point[], width: number, fromEnd: boolean): LayoutBox[] {
  const legs = segments(points);
  if (fromEnd) legs.reverse();
  const places: LayoutBox[] = [];
  for (const [a, b] of legs) {
    const box = segmentBox(a, b);
    if (box.height === 0) {
      for (let x = box.x + 8; x + width <= box.x + box.width - 8; x += 2 * LABEL_SLIDE) {
        places.push({ x, y: box.y - labelHeight() - 1, width, height: labelHeight() });
        places.push({ x, y: box.y + 1, width, height: labelHeight() });
      }
    } else {
      for (let y = box.y + 8; y + labelHeight() <= box.y + box.height - 8; y += 2 * LABEL_SLIDE) {
        places.push({ x: box.x + 4, y, width, height: labelHeight() });
        places.push({ x: box.x - 4 - width, y, width, height: labelHeight() });
      }
    }
  }
  return places;
}

/**
 * Box positions and line routes for the inputs and outputs boxes.
 *
 * Never throws. A target or producer without a box in the core gets no
 * route, and its edge falls back to a plain path.
 *
 * Args:
 *   input: The laid out core and the boxes to place.
 *
 * Returns:
 *   Absolute box positions by node id and routes by edge id.
 */
export function routeBuses(input: BusInput): Layout {
  const flip = input.direction === 'DOWN';
  const point = (p: Point): Point => (flip ? { x: p.y, y: p.x } : p);
  const rect = (r: LayoutBox): LayoutBox => (flip ? { x: r.y, y: r.x, width: r.height, height: r.width } : r);
  const size = (s: Size): Size => (flip ? { width: s.height, height: s.width } : s);

  // Real coordinates: nodes, group headers and core labels, then core lines.
  const things: Obstacle[] = Object.entries(input.core).map(([id, box]) => ({ id, ...box }));
  for (const [id, { box, labelWidth: width }] of Object.entries(input.containers)) {
    things.push({ id: `${id}#label`, ...groupHeader(box, width) });
  }
  input.coreRoutes.forEach((route, index) => {
    if (route.label) things.push({ id: `#core-label-${index}`, ...route.label });
  });
  const lines: Line[] = input.coreRoutes.flatMap((route) =>
    segments(route.points).map(([a, b]) => ({ edge: null, a, b })),
  );

  // Routing coordinates (mirrored top to bottom); lines keep clear of core labels.
  const obstacles = things.map((thing) => ({
    ...rect(thing.id.startsWith('#core-label') ? grow(thing, LABEL_CLEARANCE + 1) : thing),
    id: thing.id,
  }));
  const extent = coreExtent(
    obstacles,
    input.coreRoutes.flatMap((route) => route.points.map(point)),
  );
  /** The sides of each group a line crosses, which no line may run along. */
  const walls = Object.values(input.containers).flatMap(({ box }) => {
    const r = rect(box);
    return [r.x, r.x + r.width].map((x) => ({ x, top: r.y, bottom: r.y + r.height }));
  });
  const alongWall = (box: LayoutBox) =>
    box.width < 0.5 &&
    walls.some((wall) => Math.abs(box.x - wall.x) < DROP_STEP && box.y < wall.bottom && box.y + box.height > wall.top);
  /** Whether a box hits a node, group header or core label other than the line's end, or runs along a group side. */
  const blocked = (box: LayoutBox, end?: string) =>
    alongWall(box) || obstacles.some((o) => o.id !== end && overlaps(box, o));
  const free = (points: Point[], end: string) => segments(points).every(([a, b]) => !blocked(segmentBox(a, b), end));

  /** Where along a node's side a straight line to it is clear: its center first, then out from it. */
  const straightAt = (node: LayoutBox, fromY: number, toY: number, end: string): number | null => {
    const cx = centerX(node);
    for (let d = 0; d <= node.width / 2 - BOX_INSET; d += DROP_STEP) {
      for (const x of d === 0 ? [cx] : [cx - d, cx + d]) {
        if (free([{ x, y: fromY }, { x, y: toY }], end)) return x;
      }
    }
    return null;
  };

  /** Drops beside a column so far, by the column's rounded edge. */
  const drops = new Map<number, number>();
  /** The first k from this column's next drop whose legs are clear, taken, or null when none is. */
  const clearDrop = (edge: number, legs: (k: number) => Point[], end: string): number | null => {
    const key = Math.round(edge);
    const first = drops.get(key) ?? 0;
    for (let k = first; k < first + MAX_DROPS; k += 1) {
      if (free(legs(k), end)) {
        drops.set(key, k + 1);
        return k;
      }
    }
    return null;
  };
  /** This column's next drop, taken. */
  const nextDrop = (edge: number): number => {
    const key = Math.round(edge);
    const k = drops.get(key) ?? 0;
    drops.set(key, k + 1);
    return k;
  };
  /** The first k from this column's next drop whose legs are clear, or that next drop. */
  const dropBeside = (edge: number, legs: (k: number) => Point[], end: string): number =>
    clearDrop(edge, legs, end) ?? nextDrop(edge);

  const positions: Positions = {};
  const routes: Record<string, Route> = {};
  const pending: PendingLabel[] = [];
  const save = (edge: string, points: Point[], label: LayoutBox, moves: LabelMoves) => {
    routes[edge] = { points: points.map(point) };
    pending.push({ edge, box: rect(label), ...moves, slide: point(moves.slide) });
  };

  /**
   * A target's box and the part of its top edge a line may end on: all of a
   * node's, and for a group, near its header label so the line reads as the
   * group's. Left to right that is a stretch just past the label; top to
   * bottom it is level with the label, above the members and their lines.
   */
  const targetBox = (id: string): { t: LayoutBox; land: LayoutBox } | null => {
    if (input.core[id]) return { t: rect(input.core[id]), land: rect(input.core[id]) };
    const group = input.containers[id];
    if (!group) return null;
    const t = rect(group.box);
    const header = rect(groupHeader(group.box, group.labelWidth));
    if (flip) return { t, land: { ...t, x: header.x, width: header.width } };
    const left = header.x + header.width + DROP_STEP;
    const width = Math.min(GROUP_LANDING, t.x + t.width - BOX_INSET - left);
    return { t, land: width >= 2 * BOX_INSET ? { ...t, x: left, width } : t };
  };

  const box = input.inputsBox;
  if (box) {
    // A target whose column has a node or label above its whole top edge is
    // reached by a drop beside the column.
    const targets = box.targets
      .flatMap((target) => {
        const found = targetBox(target.id);
        return found ? [{ ...target, ...found }] : [];
      })
      .map(({ t, land, ...target }) => {
        const cx = centerX(land);
        const width = labelWidth(target.label);
        const x = straightAt(land, extent.top - 1, t.y, target.id);
        if (x !== null) return { ...target, t, cx: x, width, straight: true, turnX: x, endY: t.y };
        const endY = t.y - APPROACH;
        const left = (k: number) => t.x - DROP_IN - k * DROP_STEP;
        const right = (k: number) => t.x + t.width + DROP_IN + k * DROP_STEP;
        const legs = (at: (k: number) => number) => (k: number) => [
          { x: at(k), y: extent.top - 1 },
          { x: at(k), y: endY },
          { x: cx, y: endY },
          { x: cx, y: t.y },
        ];
        // Left of the column, else right of it, else the next drop left of it.
        const k = clearDrop(t.x, legs(left), target.id);
        const kRight = k === null ? clearDrop(t.x + t.width, legs(right), target.id) : null;
        const turnX = k !== null ? left(k) : kRight !== null ? right(kRight) : left(nextDrop(t.x));
        return { ...target, t, cx, width, straight: false, turnX, endY };
      })
      .sort((a, b) => b.turnX - a.turnX);
    const feeders = box.feeders.filter((feeder) => input.core[feeder.id]);
    const n = targets.length;
    const labelMax = Math.max(0, ...targets.map((target) => target.width));
    const channelTop = extent.top - GAP - n * LANE - (flip ? labelMax : 0);
    const boxSize = size(box.size);
    // Top to bottom the box side facing the lanes is long, and they leave it
    // directly when they fit; otherwise they leave the bottom edge as a
    // nested bracket of exits.
    const direct = flip && boxSize.height >= 2 * BOX_INSET + Math.max(0, n - 1) * LANE;
    const exitRoom = boxSize.width - 2 * BOX_INSET - feeders.length * DROP_STEP;
    const exitStep = n > 1 ? Math.max(2, Math.min(DROP_STEP, exitRoom / (n - 1))) : DROP_STEP;
    // Left to right the box sits beside the core, level with its top node, and
    // the lanes rise from its top edge: the top lane leaves leftmost, so they
    // nest. It moves right until an exit would reach its lane's label, which
    // ends just before its own turn, but stays clear of the core's left edge.
    const exitAt = (i: number) => (flip ? BOX_INSET : BOX_INSET + i * exitStep);
    const place = {
      x: flip
        ? extent.left - BOX_GAP - boxSize.width
        : Math.min(
            extent.left - BOX_GAP - boxSize.width,
            ...targets.map((target, i) => target.turnX - 6 - target.width - 4 - exitAt(i)),
          ),
      y: flip
        ? direct
          ? channelTop - BOX_INSET
          : channelTop - BOX_GAP - boxSize.height
        : Math.min(...Object.values(input.core).map((node) => node.y)),
      ...boxSize,
    };
    positions[box.id] = rect(place);
    const boxRight = place.x + place.width;
    const boxBottom = place.y + place.height;

    targets.forEach((target, i) => {
      const laneY = channelTop + i * LANE;
      const { t, cx, turnX, endY, width } = target;
      const exitX = flip ? boxRight - BOX_INSET - i * exitStep : place.x + exitAt(i);
      const exitY = flip ? boxBottom : place.y;
      const start = direct ? [{ x: boxRight, y: laneY }] : [{ x: exitX, y: exitY }, { x: exitX, y: laneY }];
      const points = target.straight
        ? [...start, { x: cx, y: laneY }, { x: cx, y: t.y }]
        : [...start, { x: turnX, y: laneY }, { x: turnX, y: endY }, { x: cx, y: endY }, { x: cx, y: t.y }];
      if (flip) {
        const label = { x: turnX - labelHeight() - 1, y: endY - 6 - width, width: labelHeight(), height: width };
        const room = label.y - laneY - 4;
        save(`e:${box.id}->${target.id}`, points, label, { slide: { x: 0, y: -LABEL_SLIDE }, room, fromEnd: true });
      } else {
        const label = { x: turnX - 6 - width, y: laneY - labelHeight() - 1, width, height: labelHeight() };
        const room = label.x - (direct ? boxRight : exitX) - 4;
        save(`e:${box.id}->${target.id}`, points, label, { slide: { x: -LABEL_SLIDE, y: 0 }, room, fromEnd: true });
      }
    });

    // Ghost lines run up to a track under the lanes, then into the box. Top to
    // bottom they enter its side when the track meets it, else its bottom left
    // of every lane exit; left to right they drop into its top, right of every
    // lane exit. The rightmost ghost takes the top track, and the tracks nest
    // like the lanes.
    const trackTop = channelTop + n * LANE;
    feeders
      .map((feeder) => ({ ...feeder, g: rect(input.core[feeder.id]) }))
      .sort((a, b) => centerX(b.g) - centerX(a.g))
      .forEach((feeder, j) => {
        const { g } = feeder;
        const gx = centerX(g);
        const trackY = trackTop + j * DROP_STEP;
        const entryX = flip
          ? place.x + BOX_INSET + (feeders.length - 1 - j) * DROP_STEP
          : place.x + exitAt(Math.max(0, n - 1)) + (j + 1) * DROP_STEP;
        let points = [{ x: gx, y: g.y }, { x: gx, y: trackY }];
        let trackStart = gx;
        if (!free(points, feeder.id)) {
          const approachY = g.y - APPROACH;
          const at = (k: number) => g.x - DROP_IN - k * DROP_STEP;
          const legs = (k: number) => [
            { x: gx, y: g.y },
            { x: gx, y: approachY },
            { x: at(k), y: approachY },
            { x: at(k), y: trackY },
          ];
          const k = dropBeside(g.x, legs, feeder.id);
          trackStart = at(k);
          points = legs(k);
        }
        const side = flip && trackY < boxBottom - BOX_INSET / 2;
        const end = side ? boxRight : entryX;
        const entryY = flip ? boxBottom : place.y;
        points.push(...(side ? [{ x: boxRight, y: trackY }] : [{ x: entryX, y: trackY }, { x: entryX, y: entryY }]));
        // Beside the track, sliding along it away from the box.
        const width = labelWidth(feeder.label);
        const label = flip
          ? { x: end + 8, y: trackY + 4, width: labelHeight(), height: width }
          : { x: end + 8, y: trackY - labelHeight() - 1, width, height: labelHeight() };
        const room = trackStart - 4 - label.x - label.width;
        save(`e:${feeder.id}->${box.id}`, points, label, { slide: { x: LABEL_SLIDE, y: 0 }, room, fromEnd: true });
      });
  }

  // A producer whose column has a node or label below its whole bottom edge
  // leaves by a rise beside the column.
  const producers = input.outputBoxes
    .filter((out) => input.core[out.owner])
    .map((out) => {
      const p = rect(input.core[out.owner]);
      const cx = centerX(p);
      const width = labelWidth(out.label);
      const startY = p.y + p.height;
      const x = straightAt(p, startY, extent.bottom + 1, out.owner);
      if (x !== null) return { ...out, p, cx: x, width, straight: true, turnX: x };
      const y1 = startY + APPROACH;
      const at = (k: number) => p.x + p.width + DROP_IN + k * DROP_STEP;
      const legs = (k: number) => [
        { x: cx, y: startY },
        { x: cx, y: y1 },
        { x: at(k), y: y1 },
        { x: at(k), y: extent.bottom + 1 },
      ];
      return { ...out, p, cx, width, straight: false, turnX: at(dropBeside(p.x + p.width, legs, out.owner)) };
    })
    .sort((a, b) => b.turnX - a.turnX);
  const m = producers.length;
  const labelMax = Math.max(0, ...producers.map((producer) => producer.width));
  // Top to bottom the labels sit on the last leg into each box, between the lanes and the boxes.
  const outTop = extent.bottom + GAP;
  const boxesTop = outTop + m * LANE + GAP + (flip ? labelMax : 0);
  // Left to right a label ends just before its box's drop, so each box keeps
  // the previous one clear of its label: the label then reads as its own box's.
  let boxX = extent.right + OUT_OFFSET;
  let previousRight = -Infinity;
  const boxes = new Map<string, LayoutBox>();
  const labelFloor = new Map<string, number>();
  for (const producer of [...producers].reverse()) {
    const boxSize = size(producer.size);
    const floor = previousRight + LABEL_SLIDE;
    const x = flip ? boxX : Math.max(boxX, floor + producer.width + 8 - boxSize.width / 2);
    boxes.set(producer.id, { x, y: boxesTop, ...boxSize });
    labelFloor.set(producer.id, floor);
    previousRight = x + boxSize.width;
    boxX = previousRight + BOXES_GAP;
  }

  producers.forEach((producer, i) => {
    const laneY = outTop + i * LANE;
    const { p, cx, turnX, width } = producer;
    const outBox = boxes.get(producer.id)!;
    positions[producer.id] = rect(outBox);
    const bx = centerX(outBox);
    const start = { x: cx, y: p.y + p.height };
    const end = [{ x: bx, y: laneY }, { x: bx, y: outBox.y }];
    const y1 = start.y + APPROACH;
    const points = producer.straight
      ? [start, { x: cx, y: laneY }, ...end]
      : [start, { x: cx, y: y1 }, { x: turnX, y: y1 }, { x: turnX, y: laneY }, ...end];
    const edge = `e:${producer.owner}->${producer.id}`;
    // At the box end of the line, sliding back along it.
    if (flip) {
      const label = { x: bx - labelHeight() - 1, y: outBox.y - 8 - width, width: labelHeight(), height: width };
      save(edge, points, label, { slide: { x: 0, y: -LABEL_SLIDE }, room: label.y - laneY - 4, fromEnd: true });
    } else {
      const label = { x: bx - 8 - width, y: laneY - labelHeight() - 1, width, height: labelHeight() };
      const room = label.x - Math.max(turnX + 8, labelFloor.get(producer.id)!);
      save(edge, points, label, { slide: { x: -LABEL_SLIDE, y: 0 }, room, fromEnd: true });
    }
  });

  // Labels, in real coordinates, clear of nodes, boxes and labels, off their
  // own line and LABEL_CLEARANCE away from every other line.
  for (const [edge, route] of Object.entries(routes)) {
    lines.push(...segments(route.points).map(([a, b]) => ({ edge, a, b })));
  }
  for (const [id, placed] of Object.entries(positions)) things.push({ id, ...placed });
  const clear = (label: LayoutBox, edge: string) => {
    const wide = grow(label, LABEL_CLEARANCE);
    return (
      !things.some((thing) => overlaps(label, thing)) &&
      !lines.some((line) =>
        line.edge === edge ? segmentHitsRect(line.a, line.b, label, 0.5) : segmentHitsRect(line.a, line.b, wide),
      )
    );
  };
  for (const label of pending) {
    const route = routes[label.edge];
    const steps = Math.max(0, Math.floor(label.room / LABEL_SLIDE));
    const slid = Array.from({ length: steps + 1 }, (_, k) => ({
      ...label.box,
      x: label.box.x + k * label.slide.x,
      y: label.box.y + k * label.slide.y,
    }));
    const places = [...slid, ...labelPlacesAlong(route.points, label.box.width, label.fromEnd)];
    const chosen = places.find((place) => clear(place, label.edge)) ?? label.box;
    route.label = chosen;
    things.push({ id: `#label-${label.edge}`, ...chosen });
  }

  return { positions, routes };
}

/** What routeBuses needs from a layout request and its laid out core. */
export function busInput(request: LayoutRequest, core: Layout): BusInput {
  const boxes = absoluteBoxes(request, core.positions);
  const inLines = request.edges.filter((edge) => edge.bus === 'in');
  const outLines = request.edges.filter((edge) => edge.bus === 'out');
  const input: BusInput = {
    direction: request.direction,
    core: {},
    containers: {},
    coreRoutes: request.edges.filter((edge) => !edge.bus && core.routes[edge.id]).map((edge) => core.routes[edge.id]),
    inputsBox: null,
    outputBoxes: [],
  };
  for (const node of request.nodes) {
    const size = { width: node.width, height: node.height };
    if (node.bus === 'in') {
      input.inputsBox = {
        id: node.id,
        size,
        targets: inLines
          .filter((edge) => edge.source === node.id)
          .map((edge) => ({ id: edge.target, label: edge.label?.text ?? '' })),
        feeders: inLines
          .filter((edge) => edge.target === node.id)
          .map((edge) => ({ id: edge.source, label: edge.label?.text ?? '' })),
      };
    } else if (node.bus === 'out') {
      const edge = outLines.find((candidate) => candidate.target === node.id);
      if (edge) input.outputBoxes.push({ id: node.id, owner: edge.source, size, label: edge.label?.text ?? '' });
    } else if (boxes[node.id]) {
      if (node.container) input.containers[node.id] = { box: boxes[node.id], labelWidth: node.labelWidth ?? 0 };
      else input.core[node.id] = boxes[node.id];
    }
  }
  return input;
}

/**
 * A full layout from the laid out core: the boxes placed and their lines routed.
 *
 * A layout that already places the boxes (the grid fallback) is returned as is.
 */
export function placeBuses(request: LayoutRequest, core: Layout): Layout {
  if (request.nodes.every((node) => !node.bus || core.positions[node.id])) return core;
  const buses = routeBuses(busInput(request, core));
  return {
    positions: { ...core.positions, ...buses.positions },
    routes: { ...core.routes, ...buses.routes },
  };
}
