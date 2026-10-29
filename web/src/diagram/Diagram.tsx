/** The application's diagram: the open session's graph with its run marks and run controls. */
import { useMemo } from 'react';
import { fieldSources } from '../api/fields';
import { findStep, GRAPH_SCHEMA, stepNodes } from '../api/graph';
import type { SessionDocument } from '../api/models';
import { useCurrentSession, useOpeningRecipe } from '../api/queries';
import { showEchogram } from '../echogram/show';
import { stepBadges } from '../runs/badges';
import { RunHint } from '../runs/RunHint';
import { openStepLog, shownRun, shownRunPreview, useRunStore } from '../runs/store';
import { useCurrentPreview } from '../runs/usePreviewIntent';
import { useRunControlsContext } from '../runs/useRunControls';
import { DiagramView } from './DiagramView';
import { SubRecipeBanner } from './SubRecipeBanner';

/** A session's diagram with the marks of the current preview and the run shown. */
function SessionDiagram({ session }: { session: SessionDocument }) {
  const graph = session.graph;
  const preview = useCurrentPreview().data ?? null;
  // Only what the marks read: a batch of log events changes neither, so it does not render the diagram.
  const runSteps = useRunStore((state) => shownRun(state)?.steps ?? null);
  const runLive = useRunStore((state) => shownRun(state)?.finished === null);
  const runPreview = useRunStore(shownRunPreview);
  const runTargets = useRunStore((state) => shownRun(state)?.started?.targets);
  const { runToHere, canRun } = useRunControlsContext();
  // Badges are node data only: useLayout's key ignores them, so a run never moves the nodes.
  const badges = useMemo(
    () =>
      stepBadges(graph, preview, runSteps && { steps: runSteps, live: runLive, preview: runPreview, targets: runTargets }),
    [graph, preview, runSteps, runLive, runPreview, runTargets],
  );
  const run = useMemo(() => ({ run: runToHere, enabled: canRun }), [runToHere, canRun]);
  const sid = session.id;
  const echogram = useMemo(
    () => ({
      show: (stepId: string) => {
        const step = findStep(graph, stepId);
        if (step) showEchogram(sid, step);
      },
    }),
    [sid, graph],
  );
  const declared = session.inputs.declared;
  const inputSources = useMemo(() => fieldSources(declared), [declared]);
  return (
    <DiagramView
      graph={graph}
      scope={session.id}
      inputValues={session.inputs.values}
      inputSources={inputSources}
      badges={badges}
      runSteps={runLive ? runSteps : null}
      runToHere={run}
      echogram={echogram}
      onStepClick={(step, badge) => {
        if (badge?.actual === 'failed') openStepLog(step.id);
      }}
      notices={
        <>
          <SubRecipeBanner origin={session.origin} />
          <RunHint />
        </>
      }
    />
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
  return <SessionDiagram session={data} />;
}
