import '@xyflow/react/dist/style.css';
import './diagram.css';
import {
  Controls,
  MiniMap,
  Panel,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type EdgeTypes,
  type NodeMouseHandler,
  type NodeTypes,
  type XYPosition,
} from '@xyflow/react';
import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { GRAPH_SCHEMA, stepNodes, type Graph } from '../api/graph';
import { useCurrentSession, useOpeningRecipe } from '../api/queries';
import { selectStep } from './locate';
import { useView } from '../state/view';
import { RouteEdge } from './edges/RouteEdge';
import { DiagramContext, IconButton, type DiagramActions } from './nodes/common';
import { MapGroupNode } from './nodes/GroupNodes';
import { IncludeNode } from './nodes/IncludeNode';
import { ExternalNode, GhostNode, InputsBoxNode, OutputsBoxNode } from './nodes/SmallNodes';
import { StepNode } from './nodes/StepNode';
import { SubRecipeBanner } from './SubRecipeBanner';
import { keepInPlace, useLayout, type FlowNode } from './useLayout';
import { buildView, findSteps, type View, type ViewNodeKind } from './buildView';

const NODE_TYPES = {
  step: StepNode,
  include: IncludeNode,
  mapGroup: MapGroupNode,
  ghost: GhostNode,
  external: ExternalNode,
  inputsBox: InputsBoxNode,
  outputsBox: OutputsBoxNode,
} as NodeTypes;

const EDGE_TYPES = { route: RouteEdge } as EdgeTypes;

/** How long after Expand all or Collapse all each new layout is fitted to the view. */
const FIT_AFTER_EXPAND_MS = 1500;

const LEGEND: { kinds: ViewNodeKind[]; swatch: string; label: string }[] = [
  { kinds: ['step'], swatch: 'rc-key-step', label: 'Step' },
  { kinds: ['include'], swatch: 'rc-key-include', label: 'Sub-recipe (double click to expand, again to drill in)' },
  { kinds: ['mapGroup'], swatch: 'rc-key-map', label: 'Runs once per item' },
  { kinds: ['inputsBox'], swatch: 'rc-key-input', label: 'Recipe inputs' },
  { kinds: ['external'], swatch: 'rc-key-external', label: 'From another recipe' },
  { kinds: ['outputsBox'], swatch: 'rc-key-output', label: 'Outputs' },
  { kinds: ['ghost'], swatch: 'rc-key-ghost', label: 'Step outside this sub-recipe' },
];

/**
 * Minimap fill per node. React Flow writes it to the rect's style.fill (not the
 * fill attribute), so var(--rc-*) values resolve against the current theme.
 */
function minimapColor(flowNode: FlowNode): string {
  const node = flowNode.data.node;
  if (node.kind === 'step') return 'var(--rc-muted)';
  if (node.kind === 'include') return 'var(--rc-accent)';
  if (node.kind === 'mapGroup') return 'var(--rc-group)';
  return 'var(--rc-faint)';
}

function Legend({ view }: { view: View }) {
  const kinds = new Set(view.nodes.map((node) => node.kind));
  const items = LEGEND.filter((item) => item.kinds.some((kind) => kinds.has(kind)));
  return (
    <div className="rc-legend" aria-label="Legend">
      {items.map((item) => (
        <span key={item.label} className="rc-legend-item">
          <span className={`rc-legend-swatch ${item.swatch}`} />
          {item.label}
        </span>
      ))}
    </div>
  );
}

interface DiagramBarProps {
  graph: Graph;
  view: View;
  onFind: (stepId: string) => void;
}

/** Up button, breadcrumb, find box, the Highlight connected chip and Expand all and Collapse all. */
function DiagramBar({ graph, view, onFind }: DiagramBarProps) {
  const findQuery = useView((state) => state.findQuery);
  const findRequest = useView((state) => state.findRequest);
  const connected = useView((state) => state.connected);
  const { setFindQuery, setConnected, drillTo, drillOut, requestExpandAll } = useView.getState();
  const input = useRef<HTMLInputElement>(null);
  const firstRequest = useRef(findRequest);
  const matches = useMemo(() => findSteps(graph, findQuery), [graph, findQuery]);

  useEffect(() => {
    if (findRequest === firstRequest.current) return;
    input.current?.focus();
    input.current?.select();
  }, [findRequest]);

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter' && matches.length > 0) {
      event.preventDefault();
      onFind(matches[0].id);
    } else if (event.key === 'Escape') {
      event.stopPropagation();
      setFindQuery('');
      input.current?.blur();
    }
  };

  return (
    <div className="rc-diagram-bar">
      {view.breadcrumb.length > 1 && (
        <IconButton icon="up" title="Up one level" onClick={drillOut} className="rc-node-tool rc-breadcrumb-up" />
      )}
      <nav className="rc-breadcrumb" aria-label="Breadcrumb">
        {view.breadcrumb.map((crumb, index) => {
          const last = index === view.breadcrumb.length - 1;
          return (
            <span key={crumb.drill.join('>') || 'root'}>
              {index > 0 && <span className="rc-breadcrumb-sep">{'>'}</span>}
              {last ? (
                <span className="rc-breadcrumb-current">{crumb.label}</span>
              ) : (
                <button
                  type="button"
                  className="rc-breadcrumb-link"
                  onClick={() => drillTo(crumb.drill.length)}
                >
                  {crumb.label}
                </button>
              )}
            </span>
          );
        })}
      </nav>
      <input
        ref={input}
        className="rc-input rc-find"
        type="search"
        placeholder="Find step (id or op)"
        aria-label="Find step"
        value={findQuery}
        onChange={(event) => setFindQuery(event.target.value)}
        onKeyDown={onKeyDown}
      />
      {findQuery.trim() && (
        <span className="rc-find-count">
          {matches.length === 0 ? 'no match' : `${matches.length} match${matches.length === 1 ? '' : 'es'}`}
        </span>
      )}
      <button
        type="button"
        className="rc-chip"
        aria-pressed={connected}
        title="Fade everything not connected to the selected node"
        onClick={() => setConnected(!connected)}
      >
        Highlight connected
      </button>
      <button
        type="button"
        className="rc-button rc-small"
        title="List the parameters of every step and the rows of every box in view"
        onClick={() => requestExpandAll(true)}
      >
        Expand all
      </button>
      <button type="button" className="rc-button rc-small" onClick={() => requestExpandAll(false)}>
        Collapse all
      </button>
    </div>
  );
}

interface CanvasProps {
  graph: Graph;
  sid: string;
  /** Path of the recipe file the session was opened from. */
  origin: string;
  inputValues: Record<string, unknown>;
}

function DiagramCanvas({ graph, sid, origin, inputValues }: CanvasProps) {
  const selected = useView((state) => state.selected);
  const expandedSteps = useView((state) => state.expandedSteps);
  const expandedBoxes = useView((state) => state.expandedBoxes);
  const drill = useView((state) => state.drill);
  const connected = useView((state) => state.connected);
  const direction = useView((state) => state.direction);
  const theme = useView((state) => state.theme);
  const findQuery = useView((state) => state.findQuery);
  const fitRequest = useView((state) => state.fitRequest);
  const expandRequest = useView((state) => state.expandRequest);
  const flow = useReactFlow<FlowNode>();
  const [center, setCenter] = useState<{ id: string; count: number } | null>(null);
  const fittedScope = useRef('');
  const firstFit = useRef(fitRequest);
  /** The node last expanded or collapsed, and where it was, kept in place on screen. */
  const anchor = useRef<{ id: string; at: XYPosition } | null>(null);
  /** Until when each new layout is fitted, after Expand all or Collapse all. */
  const fitUntil = useRef(0);

  const fit = () => window.requestAnimationFrame(() => void flow.fitView({ padding: 0.08, duration: 200 }));

  const view = useMemo(
    () =>
      buildView(graph, { drill, connected, selected, findQuery, inputValues, expandedSteps, expandedBoxes }),
    [graph, drill, connected, selected, findQuery, inputValues, expandedSteps, expandedBoxes],
  );
  const layout = useLayout(view, direction);

  const actions = useMemo<DiagramActions>(() => {
    const state = useView.getState();
    const hold = (id: string) => {
      fitUntil.current = 0;
      const node = flow.getInternalNode(id);
      anchor.current = node ? { id, at: { ...node.internals.positionAbsolute } } : null;
    };
    return {
      direction,
      drillInto: state.drillInto,
      select: state.select,
      toggleStepExpanded: (id) => {
        hold(id);
        state.toggleStepExpanded(id);
      },
      toggleBoxExpanded: (id) => {
        hold(id);
        state.toggleBoxExpanded(id);
      },
    };
  }, [direction, flow]);

  // Expand or collapse everything in view when asked (the view is read then,
  // not on every change), then fit each layout that follows for a moment.
  const firstExpand = useRef(expandRequest.count);
  useEffect(() => {
    if (expandRequest.count === firstExpand.current) return;
    const state = useView.getState();
    anchor.current = null;
    fitUntil.current = Date.now() + FIT_AFTER_EXPAND_MS;
    if (!expandRequest.expanded) {
      state.collapseAll();
      return;
    }
    const ids = (kinds: ViewNodeKind[]) =>
      view.nodes.filter((node) => kinds.includes(node.kind)).map((node) => node.id);
    state.expandMany(ids(['step']), ids(['include', 'inputsBox', 'outputsBox']));
  }, [expandRequest]);

  useEffect(() => {
    if (layout.current && Date.now() < fitUntil.current) fit();
  }, [layout.version]);

  // After a layout, pan so the node last expanded or collapsed stays where it was.
  useEffect(() => {
    if (!anchor.current || !layout.current) return;
    const frame = window.requestAnimationFrame(() => {
      const held = anchor.current;
      const node = held && flow.getInternalNode(held.id);
      if (!held || !node) {
        anchor.current = null;
        return;
      }
      const at = { ...node.internals.positionAbsolute };
      if (at.x === held.at.x && at.y === held.at.y) return;
      void flow.setViewport(keepInPlace(flow.getViewport(), held.at, at));
      anchor.current = { id: held.id, at };
    });
    return () => window.cancelAnimationFrame(frame);
  }, [layout.version, layout.current, flow]);

  // Fit when a session, drill scope or direction is first laid out.
  useEffect(() => {
    if (!layout.current || layout.version === 0) return;
    const scope = `${sid}|${drill.join('>')}|${direction}`;
    if (fittedScope.current === scope) return;
    fittedScope.current = scope;
    anchor.current = null;
    fit();
  });

  useEffect(() => {
    if (fitRequest === firstFit.current) return;
    fit();
  }, [fitRequest]);

  // Center on a found step once it is laid out.
  useEffect(() => {
    if (!center || !layout.current) return;
    const frame = window.requestAnimationFrame(() => {
      const internal = flow.getInternalNode(center.id);
      if (!internal) return;
      const { x, y } = internal.internals.positionAbsolute;
      const width = internal.measured.width ?? 0;
      const height = internal.measured.height ?? 0;
      void flow.setCenter(x + width / 2, y + height / 2, {
        zoom: Math.max(flow.getZoom(), 0.9),
        duration: 300,
      });
      setCenter(null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [center, layout.current, layout.version, flow]);

  const onFind = (stepId: string) => {
    selectStep(graph, stepId);
    setCenter((previous) => ({ id: stepId, count: (previous?.count ?? 0) + 1 }));
  };

  const onNodeClick: NodeMouseHandler<FlowNode> = (_event, flowNode) => {
    const { selection } = flowNode.data.node;
    if (selection) useView.getState().select(selection);
  };

  const onNodeDoubleClick: NodeMouseHandler<FlowNode> = (_event, flowNode) => {
    const node = flowNode.data.node;
    if (node.kind === 'include') {
      // The first double click lists the overrides, the second drills in.
      if (node.data.expanded) useView.getState().drillInto(node.id);
      else actions.toggleBoxExpanded(node.id);
    } else if (node.kind === 'ghost') {
      onFind(node.data.stepId);
    } else if (node.kind === 'step') {
      actions.toggleStepExpanded(node.id);
    } else if (node.kind === 'inputsBox' || node.kind === 'outputsBox') {
      actions.toggleBoxExpanded(node.id);
    }
  };

  return (
    <DiagramContext.Provider value={actions}>
      <div className="rc-diagram">
        <DiagramBar graph={graph} view={view} onFind={onFind} />
        <SubRecipeBanner origin={origin} />
        <div className="rc-diagram-canvas">
          <ReactFlow
            nodes={layout.nodes}
            edges={layout.edges}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            onNodesChange={layout.onNodesChange}
            onNodeClick={onNodeClick}
            onNodeDoubleClick={onNodeDoubleClick}
            onPaneClick={() => useView.getState().select(null)}
            colorMode={theme}
            nodesDraggable={false}
            nodesConnectable={false}
            zoomOnDoubleClick={false}
            onMoveStart={(event) => {
              // A pan or zoom by the user ends the fitting after Expand all.
              if (event) fitUntil.current = 0;
            }}
            minZoom={0.05}
            maxZoom={2}
            attributionPosition="bottom-center"
          >
            <Controls showInteractive={false} fitViewOptions={{ padding: 0.08 }} />
            <MiniMap pannable zoomable nodeColor={minimapColor} nodeStrokeWidth={0} />
            <Panel position="top-left">
              <Legend view={view} />
            </Panel>
          </ReactFlow>
        </div>
      </div>
    </DiagramContext.Provider>
  );
}

/** The flow diagram of the open session. */
export function Diagram() {
  const session = useCurrentSession();
  const opening = useOpeningRecipe();
  const data = session.data;
  if (!data && opening) {
    return <div className="rc-placeholder">Opening {opening}...</div>;
  }
  if (!data && session.error) {
    return <div className="rc-placeholder rc-error-text">Could not load the session: {session.error.message}</div>;
  }
  if (!data) {
    return <div className="rc-placeholder">Open a recipe from the Recipes pane to see its diagram.</div>;
  }
  if (data.graph.schema !== GRAPH_SCHEMA) {
    return (
      <div className="rc-placeholder rc-error-text">
        The server sent graph schema {data.graph.schema}; this client reads schema {GRAPH_SCHEMA}. Rebuild the
        client (npm run build) or update the server so the two match.
      </div>
    );
  }
  if (stepNodes(data.graph).length === 0) {
    return <div className="rc-placeholder">This recipe has no steps.</div>;
  }
  return (
    <ReactFlowProvider>
      <DiagramCanvas graph={data.graph} sid={data.id} origin={data.origin} inputValues={data.inputs.values} />
    </ReactFlowProvider>
  );
}
