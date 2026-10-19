/** Follow a run's server sent events into the run store. */
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { queryKeys, useRuns } from '../api/queries';
import { parseRunEvent, type RunEvent } from './events';
import { heldRun, useRunStore } from './store';

/** How long events are gathered before the store folds them, so a replay renders once per batch. */
export const EVENT_BATCH_MS = 50;

export function eventsUrl(runId: string, after: number): string {
  return `/api/runs/${encodeURIComponent(runId)}/events?after=${after}`;
}

/**
 * Subscribe to a run's events from the last seq the store holds.
 *
 * The stream ends after run_finished; the source is closed then, since
 * EventSource would otherwise reconnect forever, and the previews and the
 * runs list are refetched.
 */
export function useRunEvents(runId: string | null) {
  const client = useQueryClient();
  useEffect(() => {
    if (!runId) return;
    const store = useRunStore.getState();
    const after = heldRun(store, runId)?.lastSeq ?? 0;
    const source = new EventSource(eventsUrl(runId, after));
    let buffer: RunEvent[] = [];
    let timer: number | undefined;
    let ended = false;

    const flush = () => {
      window.clearTimeout(timer);
      timer = undefined;
      if (buffer.length === 0) return;
      const events = buffer;
      buffer = [];
      useRunStore.getState().apply(runId, events);
    };

    const end = () => {
      ended = true;
      source.close();
      flush();
      // A replayed older run changes nothing the previews or the runs list show.
      const store = useRunStore.getState();
      if (store.current?.runId !== runId) return;
      store.finish(runId);
      const sid = store.sid;
      if (sid) {
        void client.invalidateQueries({ queryKey: queryKeys.previews(sid) });
        void client.invalidateQueries({ queryKey: queryKeys.runs(sid) });
      }
    };

    source.onmessage = (message: MessageEvent<string>) => {
      let event: RunEvent;
      try {
        event = parseRunEvent(message.data);
      } catch {
        return;
      }
      buffer.push(event);
      if (event.kind === 'run_finished') end();
      else if (timer === undefined) timer = window.setTimeout(flush, EVENT_BATCH_MS);
    };
    source.onerror = () => {
      // The browser reconnects with Last-Event-ID; a finished run has nothing more to send.
      if (ended || heldRun(useRunStore.getState(), runId)?.finished) source.close();
    };

    return () => {
      source.close();
      if (!ended) flush();
    };
  }, [runId, client]);
}

/** After a session opens (or the page reloads), follow its newest run, replaying it from the start. */
export function useResumeRun(sid: string | null) {
  const runs = useRuns(sid);
  const resumed = useRef<string | null>(null);
  const newest = runs.data?.[0]?.id ?? null;
  useEffect(() => {
    if (!sid || !newest || resumed.current === sid) return;
    const store = useRunStore.getState();
    if (store.sid !== sid) return;
    resumed.current = sid;
    if (!store.current && !store.last) store.begin(newest);
  }, [sid, newest]);
}

/** Keep the store's runs followed: the current run, and a run replayed to show on the diagram. */
export function useFollowRuns(sid: string | null) {
  useResumeRun(sid);
  const following = useRunStore((state) => (state.current && !state.current.finished ? state.current.runId : null));
  const replaying = useRunStore((state) => (state.replay && !state.replay.finished ? state.replay.runId : null));
  useRunEvents(following);
  useRunEvents(replaying);
}
