/** The survey cache rule of an edit (FR-53): which survey backed steps lose their hits. */
import { findInclude, findStep, type Graph, type GraphInclude } from '../api/graph';
import type { Preview } from '../api/models';
import { surveyBackedSteps } from '../runs/badges';

/**
 * The steps an edit takes out of the survey cache: those whose hash changed
 * that the survey tier held in the preview from before the edit.
 *
 * Args:
 *   changedHashes: The edit response's `changed_hashes`, in run order; null
 *     when the server could not compute them.
 *   preview: The preview the client held before the edit, or null.
 */
export function surveyHitsLost(changedHashes: string[] | null, preview: Preview | null): string[] {
  if (!changedHashes || !preview) return [];
  const survey = surveyBackedSteps(preview);
  return changedHashes.filter((id) => survey.has(id));
}

/**
 * The steps a change of cache settings takes out of the survey cache with no
 * change of hash: survey backed in the preview from before, not in the one
 * from after.
 */
export function surveyTierLost(before: Preview | null, after: Preview | null): string[] {
  if (!before || !after) return [];
  const now = surveyBackedSteps(after);
  return [...surveyBackedSteps(before)].filter((id) => !now.has(id));
}

/** Steps under the sub-recipe that holds them; `include` is null for the root recipe. */
export interface IncludeGroup {
  include: GraphInclude | null;
  steps: string[];
}

/**
 * Group step ids by their innermost include: the root recipe's first, then the
 * includes in graph order. Ids the graph does not have go with the root.
 */
export function groupByInclude(graph: Graph, ids: string[]): IncludeGroup[] {
  const byInclude = new Map<string | null, string[]>();
  for (const id of ids) {
    const include = findStep(graph, id)?.include ?? null;
    byInclude.set(include, [...(byInclude.get(include) ?? []), id]);
  }
  const order = [null, ...graph.includes.map((include) => include.id)];
  return order
    .filter((id) => byInclude.has(id))
    .map((id) => ({ include: id === null ? null : findInclude(graph, id) ?? null, steps: byInclude.get(id)! }));
}
