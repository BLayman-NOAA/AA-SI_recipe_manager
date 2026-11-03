/** Pieces shared by the diagram node components. */
import { Handle, Position, useUpdateNodeInternals } from '@xyflow/react';
import { createContext, useContext, useEffect, useRef, type MouseEvent, type ReactNode } from 'react';
import type { Direction, Selection } from '../../state/view';
import { BUS_IN_HANDLE, BUS_OUT_HANDLE, IN_HANDLE, OUT_HANDLE, type Port, type ViewNode } from '../buildView';

/** What node components need from the diagram besides their data. */
export interface DiagramActions {
  direction: Direction;
  drillInto: (includeId: string) => void;
  select: (selection: Selection) => void;
  toggleStepExpanded: (stepId: string) => void;
  toggleBoxExpanded: (boxId: string) => void;
  /** Start a run to a step (the play icon); absent hides the icon. */
  runToHere?: (stepId: string) => void;
  /** A run can start now. */
  canRun: boolean;
  /** Draw a step's output in the echogram pane (the eye icon); absent hides the icon. */
  showEchogram?: (stepId: string) => void;
  /** Set (`on`) or remove a step's `checkpoint: always` (the save icon); absent hides the icon. */
  toggleCheckpoint?: (stepId: string, on: boolean) => void;
  /** Whether the session may edit a file, which enables the save icon of its steps. */
  canEdit?: (document: string) => boolean;
}

export const DiagramContext = createContext<DiagramActions>({
  direction: 'RIGHT',
  drillInto: () => {},
  select: () => {},
  toggleStepExpanded: () => {},
  toggleBoxExpanded: () => {},
  canRun: false,
});

export function useDiagram(): DiagramActions {
  return useContext(DiagramContext);
}

/** Props React Flow passes to a node of one view kind. */
export interface ViewNodeProps<K extends ViewNode['kind']> {
  data: { node: Extract<ViewNode, { kind: K }> };
}

/** Class names for the state every node shows: selected, dimmed, find match. */
export function stateClass(node: ViewNode): string {
  return [node.selected && 'rc-node-selected', node.dimmed && 'rc-dimmed', node.match && 'rc-match']
    .filter(Boolean)
    .join(' ');
}

function sides(direction: Direction): { target: Position; source: Position } {
  return direction === 'DOWN'
    ? { target: Position.Top, source: Position.Bottom }
    : { target: Position.Left, source: Position.Right };
}

/** The sides box lines meet a node on, across the flow: top and bottom in left to right. */
export function busSides(direction: Direction): { target: Position; source: Position } {
  return direction === 'DOWN'
    ? { target: Position.Left, source: Position.Right }
    : { target: Position.Top, source: Position.Bottom };
}

function offset(direction: Direction, fraction: number) {
  const value = `${Math.round(fraction * 100)}%`;
  return direction === 'DOWN' ? { left: value } : { top: value };
}

interface HandlesProps {
  inPorts?: Port[];
  outPorts?: Port[];
  /** Add the generic handles used by edges without a port. */
  input?: boolean;
  output?: boolean;
  /** Add the hidden handles of box lines: into the node, out of it, or both. */
  bus?: 'in' | 'out' | 'both';
}

/** A handle no edge is drawn to directly; RouteEdge draws the line along its route. */
function HiddenHandle({ id, type, position }: { id: string; type: 'source' | 'target'; position: Position }) {
  return (
    <Handle id={id} type={type} position={position} className="rc-handle rc-handle-generic" isConnectable={false} />
  );
}

/** Target handles on the input side, source handles on the output side, and box line handles. */
export function Handles({ inPorts = [], outPorts = [], input = false, output = false, bus }: HandlesProps) {
  const { direction } = useDiagram();
  const side = sides(direction);
  const busSide = busSides(direction);
  const spread = (ports: Port[], type: 'target' | 'source', position: Position) =>
    ports.map((port, index) => (
      <Handle
        key={port.id}
        id={port.id}
        type={type}
        position={position}
        className="rc-handle"
        style={offset(direction, (index + 1) / (ports.length + 1))}
        title={port.title}
        isConnectable={false}
      />
    ));
  return (
    <>
      {input && <HiddenHandle id={IN_HANDLE} type="target" position={side.target} />}
      {output && <HiddenHandle id={OUT_HANDLE} type="source" position={side.source} />}
      {(bus === 'in' || bus === 'both') && <HiddenHandle id={BUS_IN_HANDLE} type="target" position={busSide.target} />}
      {(bus === 'out' || bus === 'both') && (
        <HiddenHandle id={BUS_OUT_HANDLE} type="source" position={busSide.source} />
      )}
      {spread(inPorts, 'target', side.target)}
      {spread(outPorts, 'source', side.source)}
    </>
  );
}

/**
 * Have React Flow measure a node's handles again when the set of them changes
 * after the node is first drawn. It measures them when the node's size
 * changes, and a row handle can come or go without that (a run config file
 * chosen while the inputs box is open).
 */
export function useHandleRefresh(nodeId: string, handles: string): void {
  const update = useUpdateNodeInternals();
  const drawn = useRef(handles);
  useEffect(() => {
    if (drawn.current === handles) return;
    drawn.current = handles;
    update(nodeId);
  }, [update, nodeId, handles]);
}

/**
 * A hidden handle of a run config line. The run config box is left of the
 * inputs box in both directions, so its lines leave a right side and enter a
 * left one; in a row the handle sits level with the row.
 */
export function ConfigHandle({ id, type, row = false }: { id: string; type: 'source' | 'target'; row?: boolean }) {
  return (
    <Handle
      id={id}
      type={type}
      position={type === 'source' ? Position.Right : Position.Left}
      className={`rc-handle rc-handle-generic${row ? ' rc-handle-row' : ''}`}
      isConnectable={false}
    />
  );
}

/** The handles of an inputs or outputs box, both named `bus`. */
export function BoxHandles({ source = false, target = false }: { source?: boolean; target?: boolean }) {
  const { direction } = useDiagram();
  const busSide = busSides(direction);
  return (
    <>
      {source && <HiddenHandle id="bus" type="source" position={busSide.source} />}
      {target && <HiddenHandle id="bus" type="target" position={busSide.target} />}
    </>
  );
}

const ICONS = {
  chevronDown: 'M7.41 8.59 12 13.17l4.59-4.58L18 10l-6 6-6-6 1.41-1.41z',
  chevronUp: 'M7.41 15.41 12 10.83l4.59 4.58L18 14l-6-6-6 6 1.41 1.41z',
  up: 'M4 12l1.41 1.41L11 7.83V20h2V7.83l5.58 5.59L20 12l-8-8-8 8z',
  drill: 'M19 19H5V5h7V3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14c1.1 0 2-.9 2-2v-7h-2v7zM14 3v2h3.59l-9.83 9.83 1.41 1.41L19 6.41V10h2V3h-7z',
  play: 'M8 5v14l11-7z',
  eye: 'M12 4.5C7 4.5 2.73 7.61 1 12c1.73 4.39 6 7.5 11 7.5s9.27-3.11 11-7.5c-1.73-4.39-6-7.5-11-7.5zM12 17a5 5 0 1 1 0-10 5 5 0 0 1 0 10zm0-8a3 3 0 1 0 0 6 3 3 0 0 0 0-6z',
  disk: 'M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-4-4zm-5 16a3 3 0 1 1 0-6 3 3 0 0 1 0 6zm3-10H5V5h10v4z',
  diskOutline:
    'M17 3H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V7l-4-4zm2 16H5V5h11.17L19 7.83V19zm-7-7a3 3 0 1 0 0 6 3 3 0 0 0 0-6zM6 6h9v4H6z',
};

export type IconName = keyof typeof ICONS;

/** A small icon, drawn in the current text color. */
export function Icon({ icon }: { icon: IconName }) {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d={ICONS[icon]} />
    </svg>
  );
}

interface IconButtonProps {
  icon: IconName;
  title: string;
  onClick: () => void;
  className?: string;
  disabled?: boolean;
}

/** A small icon button that does not select, drag or pan when inside a node. */
export function IconButton({ icon, title, onClick, className = 'rc-node-tool', disabled }: IconButtonProps) {
  const stop = (event: MouseEvent) => event.stopPropagation();
  return (
    <button
      type="button"
      className={`${className} nodrag nopan`}
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onClick();
      }}
      onDoubleClick={stop}
    >
      <Icon icon={icon} />
    </button>
  );
}

/** The chevron that shows or hides a node's list. */
export function ExpandButton({ expanded, what, onClick }: { expanded: boolean; what: string; onClick: () => void }) {
  return (
    <IconButton
      icon={expanded ? 'chevronUp' : 'chevronDown'}
      title={`${expanded ? 'Hide' : 'Show'} ${what}`}
      onClick={onClick}
      className="rc-node-tool rc-node-button"
    />
  );
}

/** A row of small badges. */
export function Badges({ items }: { items: ReactNode[] }) {
  if (items.length === 0) return null;
  return <div className="rc-node-badges">{items}</div>;
}

/** A spinning ring shown beside a step or sub-recipe while it runs. */
export function Spinner() {
  return <span className="rc-run-mark rc-run-spinner" role="img" aria-label="Running" title="Running" />;
}
