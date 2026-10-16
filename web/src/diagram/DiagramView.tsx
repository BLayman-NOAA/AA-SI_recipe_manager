/**
 * The diagram of one graph: bar, canvas and legend. It reads its view state
 * from the enclosing view store and nothing from the server or the run store,
 * so the docs site can mount it as the application does.
 */
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
import {
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
  type ReactNode,
} from 'react';
import type { Graph, StepNode as GraphStep } from '../api/graph';
import type { StepBadge } from '../runs/badges';
import { useViewStore } from '../state/view';
import { selectStep } from './locate';
import { RouteEdge } from './edges/RouteEdge';
import { DiagramContext, IconButton, type DiagramActions } from './nodes/common';
import { MapGroupNode } from './nodes/GroupNodes';
import { IncludeNode } from './nodes/IncludeNode';
import { ExternalNode, GhostNode, InputsBoxNode, OutputsBoxNode } from './nodes/SmallNodes';
import { StepNode } from './nodes/StepNode';
import { keepInPlace, useLayout, type FlowNode } from './useLayout';
import { buildView, findSteps, type InputSource, type View, type ViewNodeKind } from './buildView';

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
  { kinds: ['inputsBox'], swatch: 'rc-key-input', label: 'Inputs' },
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

/** Status rows of the legend (front end doc 3.3), each shown while a step in view has it. */
const STATUS_LEGEND: { swatch: string; label: string; has: (badge: StepBadge) => boolean }[] = [
  {
    swatch: 'rc-key-survey',
    label: 'Survey cache (S)',
    has: (b) => b.predicted === 'survey' || b.actual === 'hit-survey-cache',
  },
  { swatch: 'rc-key-user', label: 'User cache (U)', has: (b) => b.predicted === 'user' || b.actual === 'hit-user-cache' },
  { swatch: 'rc-key-marker', label: 'Already done (M)', has: (b) => b.predicted === 'marker' || b.actual === 'marker' },
  {
    swatch: 'rc-key-compute',
    label: 'Compute (outline: will, fill: did)',
    has: (b) => b.predicted === 'compute' || b.actual === 'computed',
  },
  { swatch: 'rc-key-running', label: 'Running', has: (b) => b.actual === 'running' },
  { swatch: 'rc-key-pruned', label: 'Not needed (pruned)', has: (b) => b.predicted === 'pruned' || b.actual === 'pruned' },
  { swatch: 'rc-key-untargeted', label: 'Outside the run target', has: (b) => b.predicted === 'untargeted' },
  { swatch: 'rc-key-unknown', label: 'Instances not yet known', has: (b) => b.predicted === 'unknown' },
  { swatch: 'rc-key-failed', label: 'Failed (click for its log)', has: (b) => b.actual === 'failed' },
  { swatch: 'rc-key-stopped', label: 'Stopped or not run', has: (b) => b.actual === 'stopped' || b.actual === 'not-run' },
  { swatch: 'rc-key-stale', label: 'Changed since the result shown', has: (b) => b.stale },
  { swatch: 'rc-key-saved', label: 'Saved result (disk mark)', has: (b) => b.saved !== null || b.willSave },
  { swatch: 'rc-key-survey-region', label: 'Survey backed', has: (b) => b.surveyBacked },
];

function Legend({ view }: { view: View }) {
  const kinds = new Set(view.nodes.map((node) => node.kind));
  const items = LEGEND.filter((item) => item.kinds.some((kind) => kinds.has(kind)));
  const badges = view.nodes.flatMap((node) => (node.kind === 'step' && node.data.badge ? [node.data.badge] : []));
  const statuses = STATUS_LEGEND.filter((item) => badges.some(item.has));
  return (
    <div className="rc-legend" aria-label="Legend">
      {[...items, ...statuses].map((item) => (
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
  const store = useViewStore();
  const findQuery = store((state) => state.findQuery);
  const findRequest = store((state) => state.findRequest);
  const connected = store((state) => state.connected);
  const { setFindQuery, setConnected, drillTo, drillOut, requestExpandAll } = store.getState();
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

export interface DiagramViewProps {
  graph: Graph;
  /** Changes when the diagram should fit anew: the session id in the app, the slug on the site. */
  scope: string;
  inputValues?: Record<string, unknown>;
  /** Where each recipe input's value comes from, by name; absent on the site. */
  inputSources?: Record<string, InputSource>;
  /** Run status marks by step id; absent draws none. */
  badges?: Record<string, StepBadge>;
  /** Run to a step from the play icon; absent hides the icon. */
  runToHere?: { run: (stepId: string) => void; enabled: boolean };
  /** Called on a step click after selection (the app opens a failed step's log). */
  onStepClick?: (step: GraphStep, badge: StepBadge | null) => void;
  /** Rendered between the diagram bar and the canvas (the app's sub-recipe banner and run hint). */
  notices?: ReactNode;
  /** Plain wheel zooms (app) or scrolls the page, with Ctrl zooming (site). */
  wheel?: 'zoom' | 'page';
}

function DiagramCanvas({
  graph,
  scope,
  inputValues,
  inputSources,
  badges,
  runToHere,
  onStepClick,
  notices,
  wheel = 'zoom',
}: DiagramViewProps) {
  const store = useViewStore();
  const selected = store((state) => state.selected);
  const expandedSteps = store((state) => state.expandedSteps);
  const expandedBoxes = store((state) => state.expandedBoxes);
  const drill = store((state) => state.drill);
  const connected = store((state) => state.connected);
  const direction = store((state) => state.direction);
  const theme = store((state) => state.theme);
  const findQuery = store((state) => state.findQuery);
  const fitRequest = store((state) => state.fitRequest);
  const expandRequest = store((state) => state.expandRequest);
  const surveyRegion = store((state) => state.surveyRegion);
  const textSize = store((state) => state.textSize);
  const run = runToHere?.run;
  const canRun = runToHere?.enabled ?? false;
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
      buildView(graph, {
        drill,
        connected,
        selected,
        findQuery,
        inputValues,
        inputSources,
        expandedSteps,
        expandedBoxes,
        badges,
      }),
    [graph, drill, connected, selected, findQuery, inputValues, inputSources, expandedSteps, expandedBoxes, badges],
  );
  const layout = useLayout(view, direction, textSize);

  const actions = useMemo<DiagramActions>(() => {
    const state = store.getState();
    const hold = (id: string) => {
      fitUntil.current = 0;
      const node = flow.getInternalNode(id);
      anchor.current = node ? { id, at: { ...node.internals.positionAbsolute } } : null;
    };
    return {
      direction,
      runToHere: run,
      canRun,
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
  }, [store, direction, flow, run, canRun]);

  // Expand or collapse everything in view when asked (the view is read then,
  // not on every change), then fit each layout that follows for a moment.
  const firstExpand = useRef(expandRequest.count);
  useEffect(() => {
    if (expandRequest.count === firstExpand.current) return;
    const state = store.getState();
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

  // Fit when a scope, drill path or direction is first laid out.
  useEffect(() => {
    if (!layout.current || layout.version === 0) return;
    const fitScope = `${scope}|${drill.join('>')}|${direction}`;
    if (fittedScope.current === fitScope) return;
    fittedScope.current = fitScope;
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
    selectStep(store, graph, stepId);
    setCenter((previous) => ({ id: stepId, count: (previous?.count ?? 0) + 1 }));
  };

  const onNodeClick: NodeMouseHandler<FlowNode> = (_event, flowNode) => {
    const node = flowNode.data.node;
    if (node.selection) store.getState().select(node.selection);
    if (node.kind === 'step') onStepClick?.(node.data.step, node.data.badge);
  };

  const onNodeDoubleClick: NodeMouseHandler<FlowNode> = (_event, flowNode) => {
    const node = flowNode.data.node;
    if (node.kind === 'include') {
      // The first double click lists the inputs, the second drills in.
      if (node.data.expanded) store.getState().drillInto(node.id);
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
      <div className={`rc-diagram${surveyRegion ? '' : ' rc-no-survey-region'}`}>
        <DiagramBar graph={graph} view={view} onFind={onFind} />
        {notices}
        <div className="rc-diagram-canvas" style={{ '--rc-text-scale': textSize } as CSSProperties}>
          <ReactFlow
            nodes={layout.nodes}
            edges={layout.edges}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            onNodesChange={layout.onNodesChange}
            onNodeClick={onNodeClick}
            onNodeDoubleClick={onNodeDoubleClick}
            onPaneClick={() => store.getState().select(null)}
            colorMode={theme}
            nodesDraggable={false}
            nodesConnectable={false}
            zoomOnDoubleClick={false}
            zoomOnScroll={wheel === 'zoom'}
            preventScrolling={wheel === 'zoom'}
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

/** The diagram of one graph: bar, canvas, legend. Wraps its own ReactFlowProvider. */
export function DiagramView(props: DiagramViewProps) {
  return (
    <ReactFlowProvider>
      <DiagramCanvas {...props} />
    </ReactFlowProvider>
  );
}
