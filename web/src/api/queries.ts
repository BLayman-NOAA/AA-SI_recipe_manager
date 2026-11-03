/** TanStack Query hooks for every server endpoint the client uses. */
import {
  MutationObserver,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
  type MutationObserverOptions,
  type QueryClient,
} from '@tanstack/react-query';
import { useEffect } from 'react';
import type { Operation } from '../editing/operations';
import { noticeReloaded, useEditingStore } from '../editing/store';
import { surveyHitsLost, surveyTierLost } from '../editing/survey';
import { useSessionStore } from '../state/session';
import { ApiError, apiGet, apiSend, isApiError, isClientError, isNetworkError } from './client';
import { previewQuery, RUN_ALL, toStartRequest, type RunIntent } from '../runs/intent';
import { isActiveRun } from './models';
import type {
  EditResponse,
  FileText,
  Health,
  OpCatalog,
  Preview,
  RecipesResponse,
  RunLog,
  RunRecord,
  SaveAsRequest,
  SaveAsResponse,
  SaveConfigRequest,
  SaveConfigResponse,
  SaveResponse,
  SessionDocument,
  SessionSummary,
  SetRunConfigRequest,
  StartRunResponse,
  StepArtifacts,
  SyncResponse,
} from './models';

export const queryKeys = {
  health: ['health'] as const,
  recipes: ['recipes'] as const,
  ops: ['ops'] as const,
  sessions: ['sessions'] as const,
  session: (sid: string) => ['session', sid] as const,
  /** Every file text of a session, for invalidation. */
  files: (sid: string) => ['session', sid, 'file'] as const,
  file: (sid: string, path: string) => ['session', sid, 'file', path] as const,
  /** Every step's artifact list of a session, for invalidation. */
  allArtifacts: (sid: string) => ['session', sid, 'artifacts'] as const,
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
 * Fetch a session and one of its file texts from the server into the cache.
 *
 * Returns:
 *   The file's text.
 */
export async function refetchSessionFile(client: QueryClient, sid: string, path: string): Promise<string> {
  await client.fetchQuery({
    queryKey: queryKeys.session(sid),
    queryFn: () => apiGet<SessionDocument>(sessionPath(sid)),
    staleTime: 0,
  });
  const file = await client.fetchQuery({
    queryKey: queryKeys.file(sid, path),
    queryFn: () => apiGet<FileText>(fileUrl(sid, path)),
    staleTime: 0,
  });
  return file.text;
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

/** Key prefix of the mutations that change the recipe's text or files; the disk sync waits for them. */
export const EDITING_KEY = ['editing'] as const;

export const STALE_VERSION_MESSAGE = 'The recipe changed in another tab; the edit was not applied.';

/** The version of the session the client holds, sent so the server refuses a stale change. */
function heldVersion(client: QueryClient, sid: string): number | undefined {
  return client.getQueryData<SessionDocument>(queryKeys.session(sid))?.version;
}

/**
 * Cache a session the server answered after a change, and refetch what
 * follows from it; an answer older than the session held is dropped.
 */
function takeSession(client: QueryClient, session: SessionDocument) {
  const held = heldVersion(client, session.id);
  if (held !== undefined && session.version < held) return;
  client.setQueryData(queryKeys.session(session.id), session);
  void client.invalidateQueries({ queryKey: queryKeys.previews(session.id) });
  void client.invalidateQueries({ queryKey: queryKeys.files(session.id) });
  void client.invalidateQueries({ queryKey: queryKeys.allArtifacts(session.id) });
}

/** Cache the session of an edit, undo, redo or reload response. */
function takeEditSession(client: QueryClient, response: EditResponse) {
  if (response.changed) takeSession(client, response.session);
  else client.setQueryData(queryKeys.session(response.session.id), response.session);
}

/** Take an edit, undo, redo or reload response; a successful one clears the editing error. */
function takeEdit(client: QueryClient, response: EditResponse) {
  takeEditSession(client, response);
  useEditingStore.getState().setError(null);
}

/**
 * On a 409 StaleVersion, refetch the session and say the change was not
 * applied. Returns whether the error was one.
 */
function staleVersion(client: QueryClient, sid: string, error: unknown): boolean {
  if (!isApiError(error, 'StaleVersion')) return false;
  void client.invalidateQueries({ queryKey: queryKeys.session(sid) });
  useEditingStore.getState().setError(STALE_VERSION_MESSAGE);
  return true;
}

/** Show an editing failure in the status bar, for changes made from a menu or a button. */
function reportEditError(client: QueryClient, sid: string, error: Error) {
  if (!staleVersion(client, sid, error)) useEditingStore.getState().setError(error.message);
}

interface EditContext {
  /** The preview held before the edit, for the survey rule. */
  preview: Preview | null;
}

function editOptions(
  client: QueryClient,
  sid: string | null,
): MutationObserverOptions<EditResponse, Error, Operation[], EditContext> {
  const id = sid ?? '';
  return {
    mutationKey: [...EDITING_KEY, 'edits'],
    mutationFn: (operations) =>
      apiSend<EditResponse>('POST', `${sessionPath(id)}/edits`, { operations, version: heldVersion(client, id) }),
    onMutate: () => ({ preview: heldPreview(client, id) }),
    onSuccess: (response, _operations, context) => {
      // Typing sent to a session left is taken without touching the open one's warnings.
      if (response.session.id !== useSessionStore.getState().sid) {
        takeEditSession(client, response);
        return;
      }
      takeEdit(client, response);
      const lost = surveyHitsLost(response.changed_hashes, context?.preview ?? null);
      if (lost.length > 0) {
        useEditingStore.getState().showSurveyWarning({ changedHashes: response.changed_hashes ?? [], lost });
      }
    },
    onError: (error, operations) => {
      // A batch of several that fails part way gives the session a new version, so it is refetched.
      if (staleVersion(client, id, error) || operations.length === 1) return;
      void client.invalidateQueries({ queryKey: queryKeys.session(id), exact: true });
    },
  };
}

/**
 * Apply edit operations in one request. The caller shows other errors
 * (useMutationError); a stale version is reported in the status bar.
 */
export function useEdit(sid: string | null) {
  const client = useQueryClient();
  return useMutation(editOptions(client, sid));
}

/** Apply edit operations outside a component, as useEdit does; the caller handles every error. */
export function postEdit(client: QueryClient, sid: string, operations: Operation[]): Promise<EditResponse> {
  return new MutationObserver(client, editOptions(client, sid)).mutate(operations);
}

/**
 * Apply edit operations from a one shot caller (the run hint, the checkpoint
 * toggle); any failure is shown in the status bar.
 *
 * Returns:
 *   The edit response, or null when the edit failed.
 */
export async function applyEdit(
  client: QueryClient,
  sid: string,
  operations: Operation[],
): Promise<EditResponse | null> {
  try {
    return await postEdit(client, sid, operations);
  } catch (error) {
    if (!isApiError(error, 'StaleVersion')) {
      useEditingStore.getState().setError(error instanceof Error ? error.message : String(error));
    }
    return null;
  }
}

function useHistoryAction(sid: string | null, action: 'undo' | 'redo') {
  const client = useQueryClient();
  const id = sid ?? '';
  return useMutation({
    mutationKey: [...EDITING_KEY, action],
    mutationFn: () => apiSend<EditResponse>('POST', `${sessionPath(id)}/${action}`, { version: heldVersion(client, id) }),
    onSuccess: (response) => takeEdit(client, response),
    onError: (error) => reportEditError(client, id, error),
  });
}

/** Undo the session's newest edit; failures show in the status bar. */
export function useUndo(sid: string | null) {
  return useHistoryAction(sid, 'undo');
}

/** Redo the edit undone last; failures show in the status bar. */
export function useRedo(sid: string | null) {
  return useHistoryAction(sid, 'redo');
}

/** Discard every unsaved change and the history; failures show in the status bar. */
export function useRevert(sid: string | null) {
  const client = useQueryClient();
  const id = sid ?? '';
  return useMutation({
    mutationKey: [...EDITING_KEY, 'revert'],
    mutationFn: () => apiSend<SessionDocument>('POST', `${sessionPath(id)}/revert`, { version: heldVersion(client, id) }),
    onSuccess: (session) => {
      takeSession(client, session);
      useEditingStore.getState().setError(null);
    },
    onError: (error) => reportEditError(client, id, error),
  });
}

/**
 * Take files' text from disk, replacing the session's edits to them as an
 * undoable change; null reloads every file that differs. Failures show in the
 * status bar.
 */
export function useReload(sid: string | null) {
  const client = useQueryClient();
  const id = sid ?? '';
  return useMutation({
    mutationKey: [...EDITING_KEY, 'reload'],
    mutationFn: (files: string[] | null) =>
      apiSend<EditResponse>('POST', `${sessionPath(id)}/reload`, { files, version: heldVersion(client, id) }),
    onSuccess: (response) => takeEdit(client, response),
    onError: (error) => reportEditError(client, id, error),
  });
}

/**
 * Reload the files changed on disk that have no unsaved edits here, and list
 * the others as conflicts. A new version without reloads means the server
 * reloaded files itself (before a run), so the texts are refetched then too.
 */
export function useSync(sid: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: [...EDITING_KEY, 'sync'],
    mutationFn: () => apiSend<SyncResponse>('POST', `${sessionPath(sid ?? '')}/sync`),
    onSuccess: (response) => {
      const session = response.session;
      if (response.reloaded.length > 0 || heldVersion(client, session.id) !== session.version) {
        takeSession(client, session);
      } else {
        client.setQueryData(queryKeys.session(session.id), session);
      }
    },
  });
}

/**
 * Write the edited files over their originals; `true` writes files changed
 * on disk too. The response lists the files the server reloaded first.
 */
export function useSave(sid: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: [...EDITING_KEY, 'save'],
    mutationFn: (overwrite: boolean) => apiSend<SaveResponse>('POST', `${sessionPath(sid ?? '')}/save`, { overwrite }),
    onSuccess: (response) => takeSession(client, response.session),
  });
}

/** Save the recipe under another path, or with `dry_run` report what that would write. */
export function useSaveAs(sid: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationKey: [...EDITING_KEY, 'save-as'],
    mutationFn: (request: SaveAsRequest) =>
      apiSend<SaveAsResponse>('POST', `${sessionPath(sid ?? '')}/save-as`, request),
    onSuccess: (response) => {
      if (response.result.dry_run) {
        client.setQueryData(queryKeys.session(response.session.id), response.session);
        return;
      }
      takeSession(client, response.session);
      void client.invalidateQueries({ queryKey: queryKeys.sessions });
      void client.invalidateQueries({ queryKey: queryKeys.recipes });
    },
  });
}

/** Cache a session answered after a run config file change and refetch its previews. */
function takeConfigSession(client: QueryClient, session: SessionDocument) {
  client.setQueryData(queryKeys.session(session.id), session);
  void client.invalidateQueries({ queryKey: queryKeys.previews(session.id) });
}

/**
 * Use a run config file for the session, as `--config` does; a relative path
 * is from the recipe's folder, and null finds the file as the CLI does.
 */
export function useChooseConfig(sid: string | null) {
  const client = useQueryClient();
  const id = sid ?? '';
  return useMutation({
    mutationKey: [...EDITING_KEY, 'config-file'],
    mutationFn: (path: string | null) =>
      apiSend<SessionDocument>('PUT', `${sessionPath(id)}/config-file`, { path, version: heldVersion(client, id) }),
    onSuccess: (session) => takeConfigSession(client, session),
    onError: (error) => {
      staleVersion(client, id, error);
    },
  });
}

/** Write `<recipe>.config.yaml` beside the recipe from the template, and use it. */
export function useCreateConfig(sid: string | null) {
  const client = useQueryClient();
  const id = sid ?? '';
  return useMutation({
    mutationKey: [...EDITING_KEY, 'config-file', 'create'],
    mutationFn: () =>
      apiSend<SessionDocument>('POST', `${sessionPath(id)}/config-file`, { version: heldVersion(client, id) }),
    onSuccess: (session) => takeConfigSession(client, session),
    onError: (error) => {
      staleVersion(client, id, error);
    },
  });
}

/**
 * Write edits to the run config file in use, straight to disk: they are not
 * part of the session's edits and Undo does not reach them.
 *
 * Survey backed steps the save takes out of the survey cache open the survey
 * warning: steps whose hash changed, and, when a cache setting changed,
 * steps a fresh preview no longer shows in the survey tier.
 */
export function useSaveConfig(sid: string | null) {
  const client = useQueryClient();
  const id = sid ?? '';
  return useMutation({
    mutationKey: [...EDITING_KEY, 'config-file', 'save'],
    mutationFn: (request: Omit<SaveConfigRequest, 'version'>) =>
      apiSend<SaveConfigResponse>('POST', `${sessionPath(id)}/config-file/save`, {
        ...request,
        version: heldVersion(client, id),
      }),
    onMutate: () => ({ preview: heldPreview(client, id) }),
    onSuccess: (response, _request, context) => {
      if (!response.changed) {
        client.setQueryData(queryKeys.session(response.session.id), response.session);
        return;
      }
      takeConfigSession(client, response.session);
      const held = context?.preview ?? null;
      if (!held) return;
      // The save is done; the warning follows once a fresh preview shows the cache tiers.
      void (async () => {
        const lost = new Set(surveyHitsLost(response.changed_hashes, held));
        if (response.cache_changed) {
          const fresh = await fetchPreview(client, id, RUN_ALL).catch(() => null);
          for (const step of surveyTierLost(held, fresh)) lost.add(step);
        }
        if (lost.size === 0) return;
        useEditingStore.getState().showSurveyWarning({
          changedHashes: response.changed_hashes ?? [],
          lost: held.steps.map((step) => step.step_id).filter((step) => lost.has(step)),
          config: true,
        });
      })();
    },
    onError: (error) => {
      staleVersion(client, id, error);
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

/** A preview of the session the client holds and has not invalidated: run all's, else the newest. */
export function heldPreview(client: QueryClient, sid: string): Preview | null {
  const all = cachedPreview(client, sid, RUN_ALL);
  if (all) return all;
  const fresh = client
    .getQueryCache()
    .findAll({ queryKey: queryKeys.previews(sid) })
    .filter((query) => query.state.data && !query.state.isInvalidated)
    .sort((a, b) => b.state.dataUpdatedAt - a.state.dataUpdatedAt);
  return (fresh[0]?.state.data as Preview | undefined) ?? null;
}

function previewUrl(sid: string, intent: RunIntent): string {
  return `${sessionPath(sid)}/preview${previewQuery(intent)}`;
}

/** Fetch the preview of an intent from the server into the cache. */
function fetchPreview(client: QueryClient, sid: string, intent: RunIntent): Promise<Preview> {
  return client.fetchQuery({
    queryKey: previewKey(sid, intent),
    queryFn: () => apiGet<Preview>(previewUrl(sid, intent)),
    staleTime: 0,
  });
}

/** What a run with an intent would do, step by step. */
export function usePreview(sid: string | null, intent: RunIntent | null, enabled = true) {
  const args = intent ?? { mode: 'all', targets: [], force: false, forceSteps: [] };
  return useQuery({
    queryKey: previewKey(sid ?? '', args),
    queryFn: () => apiGet<Preview>(previewUrl(sid ?? '', args)),
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

/**
 * Start a run; the run controls follow it (runs/useRunControls.ts). Files the
 * server reloaded from disk before the run get a notice, and the session and
 * its texts are refetched.
 */
export function useStartRun(sid: string | null) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (intent: RunIntent) =>
      apiSend<StartRunResponse>('POST', `${sessionPath(sid ?? '')}/runs`, toStartRequest(intent)),
    onSuccess: (run) => {
      void client.invalidateQueries({ queryKey: queryKeys.runs(run.session) });
      if (run.reloaded.length === 0) return;
      noticeReloaded(run.reloaded);
      void client.invalidateQueries({ queryKey: queryKeys.session(run.session) });
      void client.invalidateQueries({ queryKey: queryKeys.files(run.session) });
      void client.invalidateQueries({ queryKey: queryKeys.previews(run.session) });
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

/** A change's error message, as useMutationError gives it; a stale version is left to the status bar. */
export function useChangeError(mutation: { error: Error | null; submittedAt: number }): string | null {
  const message = useMutationError(mutation);
  return isApiError(mutation.error, 'StaleVersion') ? null : message;
}
