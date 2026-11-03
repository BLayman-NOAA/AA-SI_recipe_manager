/** The preview the client shows: what Ctrl+Enter would do for the current selection. */
import { useEffect, useState } from 'react';
import { useCurrentSession, usePreview } from '../api/queries';
import { useView, type Selection } from '../state/view';
import { previewIntent, RUN_ALL, type RunIntent } from './intent';

/** How long the selection must settle before the preview follows it. */
export const PREVIEW_DEBOUNCE_MS = 300;

/** The selection a preview follows: a step or a sub-recipe, else none. */
function runTarget(selection: Selection | null): Selection | null {
  return selection?.kind === 'step' || selection?.kind === 'include' ? selection : null;
}

function sameTarget(a: Selection | null, b: Selection | null): boolean {
  return a?.kind === b?.kind && a?.id === b?.id;
}

/**
 * Run to the selected step, or through the selected sub-recipe, or run all;
 * follows the selection after a short pause.
 */
export function usePreviewIntent(): RunIntent {
  const target = runTarget(useView((state) => state.selected));
  const graph = useCurrentSession().data?.graph ?? null;
  const [settled, setSettled] = useState(target);
  const same = sameTarget(target, settled);
  useEffect(() => {
    if (same) return;
    const timer = window.setTimeout(() => setSettled(target), PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [target, same]);
  return previewIntent(settled, graph);
}

/** The preview of the open session for the current selection; off while the recipe is invalid. */
export function useCurrentPreview() {
  const session = useCurrentSession().data;
  const intent = usePreviewIntent();
  return usePreview(session?.id ?? null, intent, Boolean(session?.validation.valid));
}

/** The run all preview of the open session, whatever is selected; off while the recipe is invalid. */
export function useRunAllPreview() {
  const session = useCurrentSession().data;
  return usePreview(session?.id ?? null, RUN_ALL, Boolean(session?.validation.valid));
}
