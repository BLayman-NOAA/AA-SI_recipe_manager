import { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { ApiError } from '../api/client';
import { useCurrentSession, useRecoverWhenServerAnswers, useSessions } from '../api/queries';
import { DocsEnvContext, type DocsEnv } from '../env';
import { useRunStore } from '../runs/store';
import { RunControlsContext, useRunControls, useStartQueuedRun } from '../runs/useRunControls';
import { useFollowRuns } from '../runs/useRunEvents';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { useCommands } from './commands';
import { DockLayout } from './DockLayout';
import { useDock } from './dock';
import { useDocsUrl } from './links';
import { MenuBar } from './MenuBar';
import { buildMenus, type Commands } from './menus';
import { ShellDialogs } from './ShellDialogs';
import { installShortcuts } from './shortcuts';
import { StatusBar } from './StatusBar';
import { Toolbar } from './Toolbar';

/** Forget a remembered session the server no longer has. */
function useDropMissingSession() {
  const session = useCurrentSession();
  const setSid = useSessionStore((state) => state.setSid);
  const missing = session.error instanceof ApiError && session.error.status === 404;
  useEffect(() => {
    if (missing) setSid(null);
  }, [missing, setSid]);
}

/**
 * Clear the diagram scope (selection, expanded steps and boxes, drill, find) when the session
 * changes, before paint so the old scope never shows on the new recipe.
 */
function useResetScopeOnSessionChange() {
  const sid = useSessionStore((state) => state.sid);
  const previous = useRef(sid);
  useLayoutEffect(() => {
    if (previous.current !== sid) useView.getState().resetScope();
    previous.current = sid;
  }, [sid]);
}

/** Point the run store at the open session, dropping another session's runs. */
function useResetRunsOnSessionChange(sid: string | null) {
  useLayoutEffect(() => {
    useRunStore.getState().resetFor(sid);
  }, [sid]);
}

function useGlobalShortcuts(commands: Commands) {
  const { findStep, runToSelected, runSelectedOnly } = commands;
  useEffect(
    () =>
      installShortcuts({
        fit: () => useView.getState().requestFit(),
        find: findStep,
        escape: () => {
          const view = useView.getState();
          if (view.selected) view.select(null);
          else if (view.drill.length > 0) view.drillOut();
        },
        runToSelected,
        runSelectedOnly,
      }),
    [findStep, runToSelected, runSelectedOnly],
  );
}

/** The application window: menus, toolbar, panes and status bar. */
export function Shell() {
  // The one set of run controls: toolbar, menus, shortcuts and the diagram share its pending state.
  const run = useRunControls();
  const commands = useCommands(run);
  const sessions = useSessions();
  const sid = useSessionStore((state) => state.sid);
  const openPanes = useDock((state) => state.open);
  const connected = useView((state) => state.connected);
  const direction = useView((state) => state.direction);
  const theme = useView((state) => state.theme);
  const surveyRegion = useView((state) => state.surveyRegion);
  const textSize = useView((state) => state.textSize);

  const docsUrl = useDocsUrl();
  const docsEnv = useMemo<DocsEnv>(() => ({ docsUrl, linkTarget: '_blank', pages: {} }), [docsUrl]);

  useDropMissingSession();
  useRecoverWhenServerAnswers();
  useResetScopeOnSessionChange();
  useResetRunsOnSessionChange(sid);
  useFollowRuns(sid);
  useStartQueuedRun(run);
  useGlobalShortcuts(commands);

  const menus = buildMenus(
    {
      sessions: sessions.data ?? [],
      sid,
      openPanes: new Set(openPanes),
      connected,
      direction,
      theme,
      docsUrl,
      run: {
        canRun: run.canRun,
        canRunSelected: run.canRunSelected,
        isActive: run.isActive,
        isCancelling: run.isCancelling,
      },
      surveyRegion,
      textSize,
    },
    commands,
  );

  return (
    <DocsEnvContext.Provider value={docsEnv}>
      <RunControlsContext.Provider value={run}>
        <div className="rc-shell">
          <MenuBar menus={menus} />
          <Toolbar run={run} onFit={commands.fit} onToggleDirection={commands.toggleDirection} />
          <DockLayout />
          <StatusBar />
          <ShellDialogs />
        </div>
      </RunControlsContext.Provider>
    </DocsEnvContext.Provider>
  );
}
