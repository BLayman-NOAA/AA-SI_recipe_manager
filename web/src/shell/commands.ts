/** The client's commands, shared by the menu bar, the toolbar and the shortcuts. */
import { useMemo } from 'react';
import { useCloseSession } from '../api/queries';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { resetLayout, showPane, togglePane } from './dock';
import { useDialogs } from './dialogs';
import type { Commands } from './menus';

export function useCommands(): Commands {
  const { mutate: closeSession } = useCloseSession();

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
      openUrl: (url) => {
        window.open(url, '_blank', 'noopener');
      },
      showShortcuts: () => useDialogs.getState().show('shortcuts'),
      showAbout: () => useDialogs.getState().show('about'),
    };
  }, [closeSession]);
}
