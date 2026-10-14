/** TanStack Query hooks for every server endpoint the client uses. */
import { useMutation, useMutationState, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useSessionStore } from '../state/session';
import { ApiError, apiGet, apiSend, isClientError, isNetworkError } from './client';
import type {
  FileText,
  Health,
  OpCatalog,
  RecipesResponse,
  SessionDocument,
  SessionSummary,
  SetRunConfigRequest,
} from './models';

export const queryKeys = {
  health: ['health'] as const,
  recipes: ['recipes'] as const,
  ops: ['ops'] as const,
  sessions: ['sessions'] as const,
  session: (sid: string) => ['session', sid] as const,
  file: (sid: string, path: string) => ['session', sid, 'file', path] as const,
};

const OPEN_SESSION_KEY = ['open-session'] as const;

function sessionPath(sid: string): string {
  return `/api/sessions/${encodeURIComponent(sid)}`;
}

/**
 * The URL of a session file, the whole relative path encoded as one component.
 *
 * Slashes become %2F, so a path leaving the recipe folder (`../shared/x.yaml`)
 * has no `..` segment for URL parsing to collapse; `%2E%2E` would not help,
 * since URL parsing treats it as `..` too. The server decodes the path.
 */
export function fileUrl(sid: string, path: string): string {
  return `${sessionPath(sid)}/files/${encodeURIComponent(path)}`;
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => apiGet<Health>('/api/health'),
    refetchInterval: 15000,
    retry: false,
  });
}

export function useRecipes() {
  return useQuery({
    queryKey: queryKeys.recipes,
    queryFn: () => apiGet<RecipesResponse>('/api/recipes'),
  });
}

export function useOps() {
  return useQuery({
    queryKey: queryKeys.ops,
    queryFn: () => apiGet<OpCatalog>('/api/ops'),
    staleTime: Infinity,
  });
}

export function useSessions() {
  return useQuery({
    queryKey: queryKeys.sessions,
    queryFn: () => apiGet<SessionSummary[]>('/api/sessions'),
  });
}

export function useSession(sid: string | null) {
  return useQuery({
    queryKey: queryKeys.session(sid ?? ''),
    queryFn: () => apiGet<SessionDocument>(sessionPath(sid ?? '')),
    enabled: sid !== null,
    // A server error is retried twice; a missing session (4xx) is final; a
    // network failure waits for the server to answer (useRecoverWhenServerAnswers).
    retry: (failures, error) => error instanceof ApiError && error.status >= 500 && failures < 2,
  });
}

/** The session the client has open, if any. */
export function useCurrentSession() {
  const sid = useSessionStore((state) => state.sid);
  return useSession(sid);
}

export function useFileText(sid: string | null, path: string | null) {
  return useQuery({
    queryKey: queryKeys.file(sid ?? '', path ?? ''),
    queryFn: () => apiGet<FileText>(fileUrl(sid ?? '', path ?? '')),
    enabled: sid !== null && path !== null,
  });
}

/** Open a recipe as a session and make it the current one. */
export function useOpenSession() {
  const client = useQueryClient();
  const setSid = useSessionStore((state) => state.setSid);
  return useMutation({
    mutationKey: OPEN_SESSION_KEY,
    mutationFn: (path: string) => apiSend<SessionDocument>('POST', '/api/sessions', { path }),
    onSuccess: (session) => {
      client.setQueryData(queryKeys.session(session.id), session);
      client.invalidateQueries({ queryKey: queryKeys.sessions });
      setSid(session.id);
    },
  });
}

/** Delete a session on the server; clears it when it is the current one. */
export function useCloseSession() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (sid: string) => apiSend<null>('DELETE', sessionPath(sid)),
    onSuccess: (_data, sid) => {
      const store = useSessionStore.getState();
      if (store.sid === sid) store.setSid(null);
      client.removeQueries({ queryKey: queryKeys.session(sid) });
      client.invalidateQueries({ queryKey: queryKeys.sessions });
    },
  });
}

export function useSetInputs(sid: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (values: Record<string, unknown>) =>
      apiSend<SessionDocument>('PUT', `${sessionPath(sid ?? '')}/inputs`, { values }),
    onSuccess: (session) => {
      client.setQueryData(queryKeys.session(session.id), session);
    },
  });
}

export function useSetRunConfig(sid: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (overrides: SetRunConfigRequest) =>
      apiSend<SessionDocument>('PUT', `${sessionPath(sid ?? '')}/config`, overrides),
    onSuccess: (session) => {
      client.setQueryData(queryKeys.session(session.id), session);
    },
  });
}

/** The path of the recipe being opened as a session, or null. */
export function useOpeningPath(): string | null {
  const pending = useMutationState({
    filters: { mutationKey: OPEN_SESSION_KEY, status: 'pending' },
    select: (mutation) => mutation.state.variables as string,
  });
  return pending[0] ?? null;
}

/** The name of the recipe being opened, from the recipe list when it is loaded. */
export function useOpeningRecipe(): string | null {
  const client = useQueryClient();
  const path = useOpeningPath();
  if (!path) return null;
  const recipes = client.getQueryData<RecipesResponse>(queryKeys.recipes)?.recipes ?? [];
  return recipes.find((recipe) => recipe.path === path)?.name ?? path.split('/').pop() ?? path;
}

/**
 * Refetch the queries that failed for a reason worth retrying (not a 4xx)
 * each time the server answers a health check, so a server restart heals
 * the panes.
 */
export function useRecoverWhenServerAnswers() {
  const client = useQueryClient();
  const healthAt = useHealth().dataUpdatedAt;
  useEffect(() => {
    if (healthAt === 0) return;
    void client.refetchQueries({
      type: 'active',
      predicate: (query) =>
        query.state.status === 'error' &&
        query.queryKey[0] !== queryKeys.health[0] &&
        !isClientError(query.state.error),
    });
  }, [client, healthAt]);
}

/**
 * A mutation's error message, or null; a network failure stops showing once
 * the server has answered since the attempt.
 */
export function useMutationError(mutation: { error: Error | null; submittedAt: number }): string | null {
  const healthAt = useHealth().dataUpdatedAt;
  if (!mutation.error) return null;
  if (isNetworkError(mutation.error) && healthAt > mutation.submittedAt) return null;
  return mutation.error.message;
}
