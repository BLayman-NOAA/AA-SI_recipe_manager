import { useQueryClient } from '@tanstack/react-query';
import { useEffect, useLayoutEffect, useMemo, useRef } from 'react';
import { ApiError } from '../api/client';
import { echogramCandidate, findStep } from '../api/graph';
import { useCurrentSession, useRecoverWhenServerAnswers, useSessions } from '../api/queries';
import { useEchogramStore } from '../echogram/store';
import { EchogramWatcher } from '../echogram/useEchogramStaleness';
import { useEditingStore } from '../editing/store';
import { useDiskSync } from '../editing/useDiskSync';
import { DocsEnvContext, type DocsEnv } from '../env';
import { useRunStore } from '../runs/store';
import { RunControlsContext, useRunControls, useStartQueuedRun } from '../runs/useRunControls';
import { useFollowRuns } from '../runs/useRunEvents';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { dueFiles, resetEditors, useYamlEditors, useYamlSender } from '../yaml/editors';
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

/** The page title, as index.html sets it. */
const APP_TITLE = 'AA-SI Recipe Manager';

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

/** Point the echogram layers at the open session, restoring the ones it had. */
function useResetEchogramOnSessionChange(sid: string | null) {
  useLayoutEffect(() => {
    useEchogramStore.getState().resetFor(sid);
  }, [sid]);
}

/**
 * Drop the editing notices, errors and kept files of another session, and its
 * YAML editors, sending the typing still waiting there to it first.
 */
function useResetEditingOnSessionChange(sid: string | null) {
  const client = useQueryClient();
  const previous = useRef(sid);
  useLayoutEffect(() => {
    useEditingStore.getState().reset();
    resetEditors(client, previous.current === sid ? null : previous.current);
    previous.current = sid;
  }, [client, sid]);
}

/** The page title, with a leading `*` while the session has unsaved edits. */
function useDirtyTitle(dirty: boolean) {
  useEffect(() => {
    document.title = `${dirty ? '*' : ''}${APP_TITLE}`;
  }, [dirty]);
}

function useGlobalShortcuts(commands: Commands) {
  const { findStep, runToSelected, runSelectedOnly, undo, redo, save, saveAs } = commands;
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
        undo,
        redo,
        save,
        saveAs,
      }),
    [findStep, runToSelected, runSelectedOnly, undo, redo, save, saveAs],
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
  const selected = useView((state) => state.selected);
  const session = useCurrentSession().data;
  const graph = session?.graph;
  const history = session?.history;
  const dirty = (session?.dirty.length ?? 0) > 0;
  const typing = useYamlEditors((state) => dueFiles(state.files).length > 0);
  const selectedStep = selected?.kind === 'step' && graph ? findStep(graph, selected.id) : undefined;
  const echogramSelected = Boolean(selectedStep && echogramCandidate(selectedStep));

  const docsUrl = useDocsUrl();
  const docsEnv = useMemo<DocsEnv>(() => ({ docsUrl, linkTarget: '_blank', pages: {} }), [docsUrl]);

  useDropMissingSession();
  useRecoverWhenServerAnswers();
  useResetScopeOnSessionChange();
  useResetRunsOnSessionChange(sid);
  useResetEchogramOnSessionChange(sid);
  useFollowRuns(sid);
  useStartQueuedRun(run);
  useResetEditingOnSessionChange(sid);
  useDiskSync(sid);
  useYamlSender();
  useDirtyTitle(dirty);
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
        canRunStep: run.canRunStep,
        isActive: run.isActive,
        isCancelling: run.isCancelling,
      },
      edit: {
        dirty,
        typing,
        canUndo: history?.can_undo ?? false,
        canRedo: history?.can_redo ?? false,
        undoKind: history?.undo_kind ?? null,
        redoKind: history?.redo_kind ?? null,
        note: history?.note ?? null,
      },
      surveyRegion,
      textSize,
      echogramSelected,
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
          <EchogramWatcher />
        </div>
      </RunControlsContext.Provider>
    </DocsEnvContext.Provider>
  );
}
