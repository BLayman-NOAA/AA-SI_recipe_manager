/** Opening a recipe: reuse its newest session, or start one, once per request. */
import { useRef } from 'react';
import {
  useCurrentSession,
  useMutationError,
  useOpeningPath,
  useOpenSession,
  useSessions,
} from '../api/queries';
import { useSessionStore } from '../state/session';
import { latestSessionFor } from './recipeList';

export interface OpenRecipe {
  /** Open a recipe; `fresh` starts a new session even when one exists. */
  open: (path: string, fresh?: boolean) => void;
  /** An open is in flight anywhere in the client. */
  pending: boolean;
  error: string | null;
}

export function useOpenRecipe(): OpenRecipe {
  const openSession = useOpenSession();
  const sessions = useSessions();
  const current = useCurrentSession().data;
  const setSid = useSessionStore((state) => state.setSid);
  const opening = useOpeningPath();
  const busy = useRef(false);
  const error = useMutationError(openSession);
  const pending = opening !== null || openSession.isPending;

  const open = (path: string, fresh = false) => {
    // A double click sends two clicks and a dblclick; only the first counts.
    if (busy.current || pending) return;
    if (!fresh && current?.origin === path) return;
    const existing = fresh ? undefined : latestSessionFor(sessions.data ?? [], path);
    if (existing) {
      setSid(existing.id);
      return;
    }
    busy.current = true;
    openSession.mutate(path, {
      onSettled: () => {
        busy.current = false;
      },
    });
  };

  return { open, pending, error };
}
