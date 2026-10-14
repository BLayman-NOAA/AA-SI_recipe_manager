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
