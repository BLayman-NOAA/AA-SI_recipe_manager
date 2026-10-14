import { useEffect, useLayoutEffect, useRef } from 'react';
import { ApiError } from '../api/client';
import { useCurrentSession, useRecoverWhenServerAnswers, useSessions } from '../api/queries';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { useCommands } from './commands';
import { DockLayout } from './DockLayout';
import { useDocsUrl } from './links';
import { useDock } from './dock';
import { MenuBar } from './MenuBar';
import { buildMenus } from './menus';
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

function useGlobalShortcuts(findStep: () => void) {
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
      }),
    [findStep],
  );
}

/** The application window: menus, toolbar, panes and status bar. */
export function Shell() {
  const commands = useCommands();
  const sessions = useSessions();
  const sid = useSessionStore((state) => state.sid);
  const openPanes = useDock((state) => state.open);
  const connected = useView((state) => state.connected);
  const direction = useView((state) => state.direction);
  const theme = useView((state) => state.theme);

  const docsUrl = useDocsUrl();

  useDropMissingSession();
  useRecoverWhenServerAnswers();
  useResetScopeOnSessionChange();
  useGlobalShortcuts(commands.findStep);

  const menus = buildMenus(
    {
      sessions: sessions.data ?? [],
      sid,
      openPanes: new Set(openPanes),
      connected,
      direction,
      theme,
      docsUrl,
    },
    commands,
  );

  return (
    <div className="rc-shell">
      <MenuBar menus={menus} />
      <Toolbar onFit={commands.fit} onToggleDirection={commands.toggleDirection} />
      <DockLayout />
      <StatusBar />
      <ShellDialogs />
    </div>
  );
}
