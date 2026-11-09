/**
 * Keep the echogram layers' freshness current, and draw a step a run was
 * started to save once that run finishes. Mounted by the Shell, so both
 * happen while the pane is closed too.
 */
import { useEffect, useMemo } from 'react';
import { ApiError } from '../api/client';
import { useSession, useSessions } from '../api/queries';
import { useRunStore } from '../runs/store';
import { useRunAllPreviewFor } from '../runs/usePreviewIntent';
import { useTabs } from '../state/tabs';
import { computedSteps } from './layers';
import { useEchogramStore } from './store';

/**
 * Keeps one session's rows' freshness current, and drops them once the server
 * no longer has the session. The run store follows the tab in front only, so
 * another session keeps the last run it was tracked with.
 */
function SessionWatcher({ sid }: { sid: string }) {
  // The run all preview, since a preview for the selection leaves the other steps untargeted.
  const preview = useRunAllPreviewFor(sid).data ?? null;
  const session = useSession(sid);
  const missing = session.error instanceof ApiError && session.error.status === 404;
  const followed = useRunStore((state) => state.sid === sid);
  const last = useRunStore((state) => (followed ? state.last : null));
  const start = useRunStore((state) => (followed && state.last ? state.previews[state.last.runId] ?? null : null));

  useEffect(() => {
    const store = useEchogramStore.getState();
    const kept = store.bySession[sid]?.computed ?? null;
    store.track(sid, preview, (followed ? computedSteps(last, start) : null) ?? kept);
  }, [sid, preview, followed, last, start]);

  useEffect(() => {
    if (missing) useEchogramStore.getState().removeSession(sid);
  }, [missing, sid]);

  return null;
}

/** Renders nothing: a watcher per session with a tab or a layer, the titles, and the pending run. */
export function EchogramWatcher() {
  const tabs = useTabs((state) => state.sids);
  const layers = useEchogramStore((state) => state.layers);
  const sids = useMemo(() => [...new Set([...tabs, ...layers.map((layer) => layer.sid)])], [tabs, layers]);
  const sessions = useSessions().data;
  const last = useRunStore((state) => state.last);
  const start = useRunStore((state) => (state.last ? state.previews[state.last.runId] ?? null : null));
  const pendingRun = useEchogramStore((state) => state.pendingRun?.runId ?? null);
  const followed = useRunStore((state) => [state.current, state.last].some((run) => run && run.runId === pendingRun));

  useEffect(() => {
    if (!sessions) return;
    useEchogramStore.getState().setTitles(Object.fromEntries(sessions.map((session) => [session.id, session.title])));
  }, [sessions]);

  useEffect(() => {
    if (!last || pendingRun !== last.runId) return;
    useEchogramStore.getState().runFinished(last, start);
  }, [last, start, pendingRun]);

  // A run lost without its run_finished, as when the server restarts, no longer blocks the pane.
  useEffect(() => {
    if (pendingRun && !followed) useEchogramStore.getState().cancelRun();
  }, [pendingRun, followed]);

  return (
    <>
      {sids.map((sid) => (
        <SessionWatcher key={sid} sid={sid} />
      ))}
    </>
  );
}
