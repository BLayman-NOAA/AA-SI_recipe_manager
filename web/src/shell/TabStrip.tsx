/** The session tabs above the toolbar: one per open session, the front one selected. */
import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import { ApiError } from '../api/client';
import { isActiveRun, type RunRecord } from '../api/models';
import { queryKeys, useRuns, useSession, useSessions } from '../api/queries';
import { useEchogramStore } from '../echogram/store';
import { useSessionStore } from '../state/session';
import { closeTab, openTab, useTabs } from '../state/tabs';

export type TabStatus = 'none' | 'active' | 'completed' | 'failed' | 'stopped';

const STATUS_TEXT: Record<Exclude<TabStatus, 'none'>, string> = {
  active: 'run going',
  completed: 'last run completed',
  failed: 'last run failed',
  stopped: 'last run stopped',
};

/** The close button's cross. */
const CLOSE_ICON = 'M19 6.41 17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z';

/** The status a tab's dot shows, from the session's newest run. */
export function tabStatus(runs: RunRecord[] | undefined): TabStatus {
  const newest = runs?.[0];
  if (!newest) return 'none';
  if (isActiveRun(newest)) return 'active';
  if (newest.status === 'completed' || newest.status === 'failed') return newest.status;
  return 'stopped';
}

/** Close the tab of a session the server no longer has, and drop its layers. */
function dropTab(sid: string) {
  closeTab(sid);
  useEchogramStore.getState().removeSession(sid);
}

/**
 * Close the tabs, and drop the echogram layers, of sessions the session list
 * no longer has. Nothing is dropped while the list loads or fails.
 */
function usePruneTabs() {
  const sessions = useSessions().data;
  useEffect(() => {
    if (!sessions) return;
    const listed = new Set(sessions.map((session) => session.id));
    const layered = useEchogramStore.getState().layers.map((layer) => layer.sid);
    for (const sid of new Set([...useTabs.getState().sids, ...layered])) {
      if (!listed.has(sid)) dropTab(sid);
    }
  }, [sessions]);
}

interface TabProps {
  sid: string;
  title: string | undefined;
  front: boolean;
}

function Tab({ sid, title, front }: TabProps) {
  const client = useQueryClient();
  const session = useSession(sid);
  const status = tabStatus(useRuns(sid).data);
  const missing = session.error instanceof ApiError && session.error.status === 404;
  const name = title ?? session.data?.title ?? sid;
  const dirty = (session.data?.dirty.length ?? 0) > 0;

  useEffect(() => {
    if (missing) dropTab(sid);
  }, [missing, sid]);

  // A run that ended in a tab the run store does not follow leaves its previews stale.
  const previous = useRef(status);
  useEffect(() => {
    if (previous.current === 'active' && status !== 'active') {
      void client.invalidateQueries({ queryKey: queryKeys.previews(sid) });
    }
    previous.current = status;
  }, [client, sid, status]);

  return (
    <div className={`rc-session-tab${front ? ' rc-session-tab-front' : ''}`} data-sid={sid}>
      <button
        type="button"
        role="tab"
        aria-selected={front}
        className="rc-session-tab-title"
        title={session.data?.origin}
        onClick={() => openTab(sid)}
      >
        {name}
        {dirty && (
          <span className="rc-session-tab-dirty" title="Unsaved edits">
            *
          </span>
        )}
      </button>
      {status !== 'none' && (
        <span className={`rc-session-tab-dot rc-session-tab-dot-${status}`} title={STATUS_TEXT[status]} />
      )}
      <button
        type="button"
        className="rc-session-tab-close"
        aria-label={`Close ${name}`}
        title="Close the tab; the session stays in Recent Sessions"
        onClick={() => closeTab(sid)}
      >
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <path d={CLOSE_ICON} />
        </svg>
      </button>
    </div>
  );
}

/** One tab per open session, above the toolbar. */
export function TabStrip() {
  const sids = useTabs((state) => state.sids);
  const front = useSessionStore((state) => state.sid);
  const sessions = useSessions().data;
  usePruneTabs();
  if (sids.length === 0) return null;
  const titles = new Map((sessions ?? []).map((session) => [session.id, session.title]));
  return (
    <div className="rc-session-tabs" role="tablist" aria-label="Sessions">
      {sids.map((sid) => (
        <Tab key={sid} sid={sid} title={titles.get(sid)} front={sid === front} />
      ))}
    </div>
  );
}
