/** The preview the client shows: what Ctrl+Enter would do for the current selection. */
import { useEffect, useState } from 'react';
import { useCurrentSession, usePreview } from '../api/queries';
import { useView } from '../state/view';
import { previewIntent, type RunIntent } from './intent';

/** How long the selection must settle before the preview follows it. */
export const PREVIEW_DEBOUNCE_MS = 300;

/** Run to the selected step, or run all; follows the selection after a short pause. */
export function usePreviewIntent(): RunIntent {
  const selected = useView((state) => state.selected);
  const target = selected?.kind === 'step' ? selected.id : null;
  const [settled, setSettled] = useState(target);
  useEffect(() => {
    if (target === settled) return;
    const timer = window.setTimeout(() => setSettled(target), PREVIEW_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [target, settled]);
  return previewIntent(settled === null ? null : { kind: 'step', id: settled });
}

/** The preview of the open session for the current selection; off while the recipe is invalid. */
export function useCurrentPreview() {
  const session = useCurrentSession().data;
  const intent = usePreviewIntent();
  return usePreview(session?.id ?? null, intent, Boolean(session?.validation.valid));
}
