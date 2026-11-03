/** The client's commands, shared by the menu bar, the toolbar and the shortcuts. */
import { useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { findStep } from '../api/graph';
import type { SessionDocument } from '../api/models';
import { queryKeys, useCloseSession, useRedo, useRevert, useUndo } from '../api/queries';
import { showEchogram } from '../echogram/show';
import type { RunControls } from '../runs/useRunControls';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { afterFlush } from '../yaml/editors';
import { popoutPane, resetLayout, returnPane, showPane, togglePane } from './dock';
import { useDialogs } from './dialogs';
import type { Commands } from './menus';

/** The commands, with the run actions from the Shell's one set of run controls. */
export function useCommands(run: RunControls): Commands {
  const { mutate: closeSession } = useCloseSession();
  const client = useQueryClient();
  const sid = useSessionStore((state) => state.sid);
  const { mutate: undo } = useUndo(sid);
  const { mutate: redo } = useRedo(sid);
  const { mutate: revert } = useRevert(sid);

  return useMemo<Commands>(() => {
    const view = () => useView.getState();
    /** The open session as the client holds it. */
    const session = () => {
      const sid = useSessionStore.getState().sid;
      return sid ? client.getQueryData<SessionDocument>(queryKeys.session(sid)) : undefined;
    };
    return {
      openRecipe: () => showPane('recipes'),
      openSession: (sid) => useSessionStore.getState().setSid(sid),
      closeSession: () => {
        const sid = useSessionStore.getState().sid;
        const message = 'Delete this session? Its saved inputs and settings are removed.';
        if (sid && window.confirm(message)) closeSession(sid);
      },
      // Save and Save As first send the YAML typing still waiting, so they write it.
      save: () =>
        afterFlush(client, () => {
          if (session()?.dirty.length) useDialogs.getState().show('save');
        }),
      saveAs: () =>
        afterFlush(client, () => {
          if (session()) useDialogs.getState().show('saveAs');
        }),
      revert: () => {
        const message =
          'Discard every unsaved change? The recipe files go back to what is saved on disk, and the undo history is cleared.';
        if (session()?.dirty.length && window.confirm(message)) revert();
      },
      undo: () => {
        if (session()?.history.can_undo) undo();
      },
      redo: () => {
        if (session()?.history.can_redo) redo();
      },
      findStep: () => {
        showPane('diagram');
        view().requestFind();
      },
      showPane,
      togglePane,
      popoutPane,
      returnPane,
      toggleConnected: () => view().setConnected(!view().connected),
      expandAll: () => {
        showPane('diagram');
        view().requestExpandAll(true);
      },
      collapseAll: () => {
        showPane('diagram');
        view().requestExpandAll(false);
      },
      toggleDirection: () => view().setDirection(view().direction === 'RIGHT' ? 'DOWN' : 'RIGHT'),
      fit: () => view().requestFit(),
      resetLayout,
      toggleTheme: () => view().setTheme(view().theme === 'dark' ? 'light' : 'dark'),
      setTextSize: (size) => view().setTextSize(size),
      openUrl: (url) => {
        window.open(url, '_blank', 'noopener');
      },
      showShortcuts: () => useDialogs.getState().show('shortcuts'),
      showAbout: () => useDialogs.getState().show('about'),
      dryRun: run.dryRun,
      runAll: run.runAll,
      runToSelected: run.runToSelected,
      runSelectedOnly: run.runSelectedOnly,
      forceRerunSelected: run.forceSelected,
      forceRerunAll: run.forceAll,
      cancelRun: run.cancel,
      stopRun: run.stopNow,
      showRunHistory: () => showPane('runs'),
      refreshPreview: () => {
        const sid = useSessionStore.getState().sid;
        if (sid) void client.invalidateQueries({ queryKey: queryKeys.previews(sid) });
      },
      toggleSurveyRegion: () => view().setSurveyRegion(!view().surveyRegion),
      showEchogramForSelected: () => {
        const sid = useSessionStore.getState().sid;
        const selected = view().selected;
        if (!sid || selected?.kind !== 'step') return;
        const graph = client.getQueryData<SessionDocument>(queryKeys.session(sid))?.graph;
        const step = graph && findStep(graph, selected.id);
        if (step) showEchogram(sid, step);
      },
    };
  }, [closeSession, client, run, undo, redo, revert]);
}
