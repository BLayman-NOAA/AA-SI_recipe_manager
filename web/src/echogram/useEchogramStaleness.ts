/**
 * Keep the echogram layers' out of date marks current, and draw a step a run
 * was started to save once that run finishes. Mounted by the Shell, so both
 * happen while the pane is closed too.
 */
import { useEffect } from 'react';
import { useRunStore } from '../runs/store';
import { useCurrentPreview } from '../runs/usePreviewIntent';
import { computedSteps, previewHashes } from './layers';
import { useEchogramStore } from './store';

/** Renders nothing; a component of its own, so its updates render nothing else of the Shell. */
export function EchogramWatcher() {
  const preview = useCurrentPreview().data;
  const last = useRunStore((state) => state.last);
  // The fields the marks read, so a mark's own change does not run this again.
  const drawn = useEchogramStore((state) =>
    state.layers.map((layer) => `${layer.id}@${layer.stepHash}@${layer.addedAt}`).join('|'),
  );
  const pendingRun = useEchogramStore((state) => state.pendingRun?.runId ?? null);

  useEffect(() => {
    useEchogramStore.getState().markOutOfDate(previewHashes(preview), computedSteps(last));
  }, [preview, last, drawn]);

  useEffect(() => {
    if (!last || pendingRun !== last.runId) return;
    const start = useRunStore.getState().previews[last.runId] ?? null;
    useEchogramStore.getState().runFinished(last, start);
  }, [last, pendingRun]);

  return null;
}
