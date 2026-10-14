import { BaseEdge, EdgeLabelRenderer, getSmoothStepPath, Position, type EdgeProps } from '@xyflow/react';
import type { Point } from '../layout';
import type { FlowEdge } from '../useLayout';

const CORNER = 4;
const ARROW_LENGTH = 8;
const ARROW_WIDTH = 7;

/** An SVG path along an orthogonal polyline, its corners rounded. */
export function polylinePath(points: Point[], radius = CORNER): string {
  if (points.length === 0) return '';
  const parts = [`M ${points[0].x} ${points[0].y}`];
  for (let i = 1; i < points.length - 1; i += 1) {
    const [before, at, after] = [points[i - 1], points[i], points[i + 1]];
    const into = Math.hypot(at.x - before.x, at.y - before.y);
    const out = Math.hypot(after.x - at.x, after.y - at.y);
    const r = Math.min(radius, into / 2, out / 2);
    if (r <= 0) continue;
    const start = { x: at.x + ((before.x - at.x) / into) * r, y: at.y + ((before.y - at.y) / into) * r };
    const end = { x: at.x + ((after.x - at.x) / out) * r, y: at.y + ((after.y - at.y) / out) * r };
    parts.push(`L ${start.x} ${start.y}`, `Q ${at.x} ${at.y} ${end.x} ${end.y}`);
  }
  const last = points[points.length - 1];
  parts.push(`L ${last.x} ${last.y}`);
  return parts.join(' ');
}

/** A filled arrowhead whose tip is the end of a line coming from a point before it. */
export function arrowPath(from: Point, tip: Point): string {
  const length = Math.hypot(tip.x - from.x, tip.y - from.y);
  if (length === 0) return '';
  const along = { x: (tip.x - from.x) / length, y: (tip.y - from.y) / length };
  const base = { x: tip.x - along.x * ARROW_LENGTH, y: tip.y - along.y * ARROW_LENGTH };
  const half = ARROW_WIDTH / 2;
  const left = { x: base.x - along.y * half, y: base.y + along.x * half };
  const right = { x: base.x + along.y * half, y: base.y - along.x * half };
  return `M ${tip.x} ${tip.y} L ${left.x} ${left.y} L ${right.x} ${right.y} Z`;
}

/** The point an edge comes from into a handle on the given side of its target. */
function before(tip: Point, side: Position): Point {
  switch (side) {
    case Position.Left:
      return { x: tip.x - 1, y: tip.y };
    case Position.Right:
      return { x: tip.x + 1, y: tip.y };
    case Position.Top:
      return { x: tip.x, y: tip.y - 1 };
    default:
      return { x: tip.x, y: tip.y + 1 };
  }
}

/** The label class: box lines take their box's color, muted lines a muted one. */
function labelClass(data: FlowEdge['data']): string {
  if (!data?.bus) return '';
  if (data.muted) return ' rc-edge-label-muted';
  return data.bus === 'in' ? ' rc-edge-label-in' : ' rc-edge-label-out';
}

/**
 * An edge drawn along its laid out route, with an arrowhead at its target and
 * its label in the route's label box.
 *
 * Without a route (the grid fallback) it is a smooth step path with the label
 * at its middle.
 */
export function RouteEdge(props: EdgeProps<FlowEdge>) {
  const { id, data, style, targetX, targetY, targetPosition } = props;
  const route = data?.route;
  let path: string;
  let arrow: string;
  let transform: string;
  if (route && route.points.length > 1) {
    const points = route.points;
    path = polylinePath(points);
    arrow = arrowPath(points[points.length - 2], points[points.length - 1]);
    const box = route.label;
    transform = box ? `translate(${box.x}px, ${box.y}px)` : '';
  } else {
    const [smooth, labelX, labelY] = getSmoothStepPath(props);
    const tip = { x: targetX, y: targetY };
    path = smooth;
    arrow = arrowPath(before(tip, targetPosition), tip);
    transform = `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`;
  }
  return (
    <>
      <BaseEdge id={id} path={path} style={style} />
      <path className="rc-edge-arrow" d={arrow} />
      {data?.label && transform && (
        <EdgeLabelRenderer>
          <div
            className={`rc-edge-label nodrag nopan${labelClass(data)}${data.dimmed ? ' rc-dimmed' : ''}`}
            style={{ transform }}
            title={data.tooltip}
          >
            {data.label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
