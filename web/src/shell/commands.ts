/** The client's commands, shared by the menu bar, the toolbar and the shortcuts. */
import { useQueryClient } from '@tanstack/react-query';
import { useCallback, useMemo, useState } from 'react';
import { isApiError } from '../api/client';
import { findStep } from '../api/graph';
import type { SessionDocument, SessionSummary } from '../api/models';
import { queryKeys, useCloseSession, useMutationError, useRedo, useRevert, useUndo } from '../api/queries';
import { showEchogram } from '../echogram/show';
import { useEchogramStore } from '../echogram/store';
import { useEditingStore } from '../editing/store';
import type { RunControls } from '../runs/useRunControls';
import { useSessionStore } from '../state/session';
import { openTab } from '../state/tabs';
import { useView } from '../state/view';
import { plural } from '../text';
import { afterFlush } from '../yaml/editors';
import { popoutPane, resetLayout, returnPane, showPane, togglePane } from './dock';
import { useDialogs } from './dialogs';
import type { Commands } from './menus';

/** The confirm of Delete Session, naming the echogram layers that go with the session. */
export function deleteSessionMessage(title: string, layers: number): string {
  const drawn = layers > 0 ? `, and ${plural(layers, 'echogram layer')} with them` : '';
  return `Delete the session "${title}"? Its saved inputs and settings are removed${drawn}.`;
}

/** Why the delete of the session `title` failed; a run going in it is said in words, not ids. */
export function deleteFailureText(title: string, failure: Error): string {
  const why = isApiError(failure, 'ActiveRunExists')
    ? 'a run is going in it. Cancel the run or wait for it to finish.'
    : failure.message;
  return `Could not delete ${title}: ${why}`;
}

export interface DeleteSession {
  /** Delete a session after a confirm that names its echogram layers. */
  remove: (sid: string, title: string) => void;
  /** Why the last delete failed, as deleteFailureText says it, while useMutationError keeps it. */
  error: string | null;
}

/**
 * Delete a session after a confirm that names its echogram layers; its tab
 * closes and its layers go once the server has deleted it. A failure, as for
 * a session with a run going, keeps both; its message is `error`, and is
 * also given to `onError`.
 */
export function useDeleteSession(onError?: (message: string) => void): DeleteSession {
  const mutation = useCloseSession();
  const [deleting, setDeleting] = useState('');
  const error = useMutationError(mutation) === null ? null : deleteFailureText(deleting, mutation.error!);
  const { mutateAsync } = mutation;
  const remove = useCallback(
    (sid: string, title: string) => {
      const layers = useEchogramStore.getState().layers.filter((layer) => layer.sid === sid).length;
      if (!window.confirm(deleteSessionMessage(title, layers))) return;
      setDeleting(title);
      mutateAsync(sid).then(
        () => useEchogramStore.getState().removeSession(sid),
        (failure: Error) => onError?.(deleteFailureText(title, failure)),
      );
    },
    [mutateAsync, onError],
  );
  return { remove, error };
}

/** Show a command's failure in the status bar. */
function showInStatusBar(message: string) {
  useEditingStore.getState().setError(message);
}

/** The commands, with the run actions from the Shell's one set of run controls. */
export function useCommands(run: RunControls): Commands {
  const deleteSession = useDeleteSession(showInStatusBar).remove;
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
      openSession: (sid) => openTab(sid),
      closeSession: () => {
        const sid = useSessionStore.getState().sid;
        if (!sid) return;
        const sessions = client.getQueryData<SessionSummary[]>(queryKeys.sessions) ?? [];
        const title = sessions.find((entry) => entry.id === sid)?.title ?? session()?.title ?? sid;
        deleteSession(sid, title);
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
  }, [deleteSession, client, run, undo, redo, revert]);
}
