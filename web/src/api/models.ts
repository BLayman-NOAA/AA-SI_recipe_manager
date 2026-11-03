/** Names for the generated API schema types used across the client. */
import type { Graph } from './graph';
import type { components } from './types';

type Schemas = components['schemas'];

export type Health = Schemas['HealthResponse'];
export type RecipeEntry = Schemas['RecipeEntryModel'];
export type RecipesResponse = Schemas['RecipesResponse'];
export type SessionSummary = Schemas['SessionSummary'];
export type FileRef = Schemas['FileRef'];
export type InputField = Schemas['InputField'];
export type InputSource = InputField['source'];
export type RunConfigBlock = Schemas['RunConfigBlock'];
export type Problem = Schemas['Problem'];
export type Validation = Schemas['ValidationBlock'];
export type FileText = Schemas['FileTextResponse'];
export type SetRunConfigRequest = Schemas['SetRunConfigRequest'];
export type ErrorBody = Schemas['ErrorResponse'];

/** A session document; `graph` is typed by hand in graph.ts. */
export type SessionDocument = Omit<Schemas['SessionResponse'], 'graph'> & {
  graph: Graph;
};

/** A response type with its `session` read as a SessionDocument. */
type WithSession<T> = Omit<T, 'session'> & { session: SessionDocument };

export type HistoryEntry = Schemas['HistoryEntryModel'];
export type HistoryBlock = Schemas['HistoryBlock'];
export type DiskConflict = Schemas['DiskConflict'];
export type EditResponse = WithSession<Schemas['EditResponse']>;
export type SyncResponse = WithSession<Schemas['SyncResponse']>;
export type SaveResponse = WithSession<Schemas['SaveResponse']>;
export type SaveAsRequest = Schemas['SaveAsRequest'];
export type SaveAsResult = Schemas['SaveAsResultModel'];
export type Rewrite = Schemas['RewriteModel'];
export type SaveAsResponse = WithSession<Schemas['SaveAsResponse']>;
export type SaveConfigRequest = Schemas['SaveConfigRequest'];
export type SaveConfigResponse = WithSession<Schemas['SaveConfigResponse']>;

/** One op in the catalog served by GET /api/ops (docs/data/ops.json shape). */
export interface OpEntry {
  op: string;
  description: string | null;
  category: string | null;
  experimental: boolean;
  sink: boolean;
  [key: string]: unknown;
}

/** The op catalog served by GET /api/ops. */
export interface OpCatalog {
  schema: number;
  counts: { ops: number; implementations: number; sources_resolved: number };
  categories: string[];
  ops: OpEntry[];
}

/** The server reports run config problems with this prefix and no step. */
export const RUN_CONFIG_PREFIX = 'Run config: ';

export function isRunConfigProblem(problem: Problem): boolean {
  return !problem.step && problem.message.startsWith(RUN_CONFIG_PREFIX);
}

export type RunRecord = Schemas['RunResponse'];
/** The run a start answers, with the files the server reloaded from disk before it. */
export type StartRunResponse = Schemas['StartRunResponse'];
export type RunStatus = RunRecord['status'];
export type RunSummary = Schemas['RunSummary'];
export type StartRunRequest = Schemas['StartRunRequest'];
export type Preview = Schemas['PreviewResponse'];
export type StepPreview = Schemas['StepPreviewModel'];
export type RunLog = Schemas['LogResponse'];
export type LogEntry = Schemas['LogEntry'];

/** Run statuses of a run that has not ended. */
export const ACTIVE_RUN_STATUSES: ReadonlySet<RunStatus> = new Set(['queued', 'starting', 'running']);

export function isActiveRun(run: RunRecord): boolean {
  return ACTIVE_RUN_STATUSES.has(run.status);
}

/** Whether the session may edit a file: an editable session and one of its editable files. */
export function fileEditable(session: Pick<SessionDocument, 'read_only' | 'files'>, path: string): boolean {
  return !session.read_only && session.files.some((file) => file.path === path && file.editable);
}

export type StepArtifacts = Schemas['StepArtifactsResponse'];
export type Artifact = Schemas['ArtifactModel'];
