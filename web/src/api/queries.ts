/** TanStack Query hooks for every server endpoint the client uses. */
import { useMutation, useMutationState, useQuery, useQueryClient, type QueryClient } from '@tanstack/react-query';
import { useEffect } from 'react';
import { useSessionStore } from '../state/session';
import { ApiError, apiGet, apiSend, isClientError, isNetworkError } from './client';
import { previewQuery, toStartRequest, type RunIntent } from '../runs/intent';
import { isActiveRun } from './models';
import type {
  FileText,
  Health,
  OpCatalog,
  Preview,
  RecipesResponse,
  RunLog,
  RunRecord,
  SessionDocument,
  SessionSummary,
  SetRunConfigRequest,
  StepArtifacts,
} from './models';

export const queryKeys = {
  health: ['health'] as const,
  recipes: ['recipes'] as const,
  ops: ['ops'] as const,
  sessions: ['sessions'] as const,
  session: (sid: string) => ['session', sid] as const,
  file: (sid: string, path: string) => ['session', sid, 'file', path] as const,
  /** Every preview of a session, for invalidation. */
  previews: (sid: string) => ['preview', sid] as const,
  preview: (sid: string, targets: string[], only: boolean, force: boolean, forceSteps: string[]) =>
    ['preview', sid, { targets, only, force, forceSteps }] as const,
  runs: (sid: string) => ['runs', sid] as const,
  run: (rid: string) => ['run', rid] as const,
  runLog: (rid: string, step: string | null) => ['run', rid, 'log', step] as const,
  artifacts: (sid: string, step: string) => ['session', sid, 'artifacts', step] as const,
};

/** How long a preview is fresh; runs and edits refetch it sooner. */
const PREVIEW_STALE_MS = 10000;
const ACTIVE_RUNS_POLL_MS = 5000;

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

/**
 * Which output and variable of a step to draw; absent fields take the
 * server's default. `resolve: false` only lists the artifacts, reading and
 * mounting nothing.
 */
export interface ArtifactChoice {
  output?: string | null;
  var?: string | null;
  resolve?: boolean;
}

/** The URL of a step's artifacts, resolving an output and variable for the echogram. */
export function artifactsUrl(sid: string, step: string, choice: ArtifactChoice = {}): string {
  const query = new URLSearchParams();
  if (choice.output) query.set('output', choice.output);
  if (choice.var) query.set('var', choice.var);
  if (choice.resolve === false) query.set('resolve', 'false');
  const text = query.toString();
  return `${sessionPath(sid)}/steps/${encodeURIComponent(step)}/artifacts${text ? `?${text}` : ''}`;
}

/** Where a step's saved outputs are, and what the echogram pane can draw of them. */
export function fetchStepArtifacts(sid: string, step: string, choice: ArtifactChoice = {}): Promise<StepArtifacts> {
  return apiGet<StepArtifacts>(artifactsUrl(sid, step, choice));
}

/** A step's saved outputs, listed without resolving them, for the inspector; fetched only while enabled. */
export function useStepArtifacts(sid: string | null, step: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.artifacts(sid ?? '', step ?? ''),
    queryFn: () => fetchStepArtifacts(sid ?? '', step ?? '', { resolve: false }),
    enabled: sid !== null && step !== null && enabled,
    staleTime: 0,
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
      client.invalidateQueries({ queryKey: queryKeys.previews(session.id) });
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
      client.invalidateQueries({ queryKey: queryKeys.previews(session.id) });
    },
  });
}

function previewKey(sid: string, intent: RunIntent) {
  return queryKeys.preview(sid, intent.targets, intent.mode === 'only', intent.force, intent.forceSteps);
}

/** The preview of an intent the client holds, or null when it has none or it was invalidated since. */
export function cachedPreview(client: QueryClient, sid: string, intent: RunIntent): Preview | null {
  const state = client.getQueryState<Preview>(previewKey(sid, intent));
  return state?.data && !state.isInvalidated ? state.data : null;
}

/** What a run with an intent would do, step by step. */
export function usePreview(sid: string | null, intent: RunIntent | null, enabled = true) {
  const args = intent ?? { mode: 'all', targets: [], force: false, forceSteps: [] };
  return useQuery({
    queryKey: previewKey(sid ?? '', args),
    queryFn: () => apiGet<Preview>(`${sessionPath(sid ?? '')}/preview${previewQuery(args)}`),
    enabled: sid !== null && intent !== null && enabled,
    staleTime: PREVIEW_STALE_MS,
    // Keep the last preview of the same session on screen while the next one loads.
    placeholderData: (previous, previousQuery) => (previousQuery?.queryKey[1] === sid ? previous : undefined),
  });
}

/** The session's runs, newest first; polled while one is active. */
export function useRuns(sid: string | null) {
  return useQuery({
    queryKey: queryKeys.runs(sid ?? ''),
    queryFn: () => apiGet<RunRecord[]>(`${sessionPath(sid ?? '')}/runs`),
    enabled: sid !== null,
    refetchInterval: (query) => (query.state.data?.some(isActiveRun) ? ACTIVE_RUNS_POLL_MS : false),
  });
}

export function useRun(rid: string | null) {
  return useQuery({
    queryKey: queryKeys.run(rid ?? ''),
    queryFn: () => apiGet<RunRecord>(`/api/runs/${encodeURIComponent(rid ?? '')}`),
    enabled: rid !== null,
  });
}

/** A run's log from the server, for runs the client does not hold. */
export function useRunLog(rid: string | null, step: string | null, enabled = true) {
  return useQuery({
    queryKey: queryKeys.runLog(rid ?? '', step),
    queryFn: () => {
      const query = step ? `?step=${encodeURIComponent(step)}` : '';
      return apiGet<RunLog>(`/api/runs/${encodeURIComponent(rid ?? '')}/log${query}`);
    },
    enabled: rid !== null && enabled,
  });
}

/** Start a run; the run controls follow it (runs/useRunControls.ts). */
export function useStartRun(sid: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: RunIntent) =>
      apiSend<RunRecord>('POST', `${sessionPath(sid ?? '')}/runs`, toStartRequest(intent)),
    onSuccess: (run) => {
      client.invalidateQueries({ queryKey: queryKeys.runs(run.session) });
    },
  });
}

/**
 * Ask a run to stop after its running steps (`cancel`), or stop it now,
 * killing its process (`stop`).
 */
function useRunAction(action: 'cancel' | 'stop') {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (rid: string) => apiSend<RunRecord>('POST', `/api/runs/${encodeURIComponent(rid)}/${action}`),
    onSuccess: (run) => {
      client.setQueryData(queryKeys.run(run.id), run);
      client.invalidateQueries({ queryKey: queryKeys.runs(run.session) });
    },
  });
}

/** Ask a run to stop once the steps running now finish. */
export function useCancelRun() {
  return useRunAction('cancel');
}

/** Stop a run now; the step running is not saved. */
export function useStopRun() {
  return useRunAction('stop');
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
