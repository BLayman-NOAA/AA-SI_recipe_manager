/**
 * Keep the echogram layers' freshness current, and draw a step a run was
 * started to save once that run finishes. Mounted by the Shell, so both
 * happen while the pane is closed too.
 */
import { useEffect } from 'react';
import { useRunStore } from '../runs/store';
import { useRunAllPreview } from '../runs/usePreviewIntent';
import { computedSteps } from './layers';
import { useEchogramStore } from './store';

/** Renders nothing; a component of its own, so its updates render nothing else of the Shell. */
export function EchogramWatcher() {
  // The run all preview, since a preview for the selection leaves the other steps untargeted.
  const preview = useRunAllPreview().data ?? null;
  const last = useRunStore((state) => state.last);
  const start = useRunStore((state) => (state.last ? state.previews[state.last.runId] ?? null : null));
  const sid = useEchogramStore((state) => state.sid);
  const pendingRun = useEchogramStore((state) => state.pendingRun?.runId ?? null);
  const followed = useRunStore((state) => [state.current, state.last].some((run) => run && run.runId === pendingRun));

  useEffect(() => {
    useEchogramStore.getState().track(preview, computedSteps(last, start));
  }, [preview, last, start, sid]);

  useEffect(() => {
    if (!last || pendingRun !== last.runId) return;
    useEchogramStore.getState().runFinished(last, start);
  }, [last, start, pendingRun]);

  // A run lost without its run_finished, as when the server restarts, no longer blocks the pane.
  useEffect(() => {
    if (pendingRun && !followed) useEchogramStore.getState().cancelRun();
  }, [pendingRun, followed]);

  return null;
}
