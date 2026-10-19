/** The client's commands, shared by the menu bar, the toolbar and the shortcuts. */
import { useQueryClient } from '@tanstack/react-query';
import { useMemo } from 'react';
import { queryKeys, useCloseSession } from '../api/queries';
import type { RunControls } from '../runs/useRunControls';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { resetLayout, showPane, togglePane } from './dock';
import { useDialogs } from './dialogs';
import type { Commands } from './menus';

/** The commands, with the run actions from the Shell's one set of run controls. */
export function useCommands(run: RunControls): Commands {
  const { mutate: closeSession } = useCloseSession();
  const client = useQueryClient();

  return useMemo<Commands>(() => {
    const view = () => useView.getState();
    return {
      openRecipe: () => showPane('recipes'),
      openSession: (sid) => useSessionStore.getState().setSid(sid),
      closeSession: () => {
        const sid = useSessionStore.getState().sid;
        const message = 'Delete this session? Its saved inputs and settings are removed.';
        if (sid && window.confirm(message)) closeSession(sid);
      },
      findStep: () => {
        showPane('diagram');
        view().requestFind();
      },
      showPane,
      togglePane,
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
    };
  }, [closeSession, client, run]);
}
