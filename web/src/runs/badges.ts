/**
 * Status marks per step: the preview's prediction joined with a run's actual
 * status by step id (front end doc 3.3).
 *
 * Pure. diagram/buildView.ts puts the result into node data.
 */
import { stepNodes, type Graph, type GraphInclude } from '../api/graph';
import type { Preview, StepPreview } from '../api/models';
import { type RunState, type StepRunState, type StepStatus } from './reducer';

export type Predicted = 'survey' | 'user' | 'marker' | 'compute' | 'skipped' | 'pruned' | 'untargeted' | 'unknown';

export interface StepBadge {
  /** Outline, from the preview, or from the run's plan while it runs. */
  predicted: Predicted | null;
  /** Fill, from the run shown. */
  actual: StepStatus | null;
  /** Instances done of a mapped step. */
  progress: { done: number; count: number | null } | null;
  /** The tier holding a result for the current hash (first in read order). */
  saved: 'S' | 'U' | null;
  /** A result is written by the run previewed: a pinned target, or a step the recipe checkpoints. */
  willSave: boolean;
  /** The recipe checkpoints the step (`checkpoint: always`). */
  checkpointed: boolean;
  /** The shown result's hash differs from the preview's (FR-54). */
  stale: boolean;
  /** The survey tier holds the step (FR-52). */
  surveyBacked: boolean;
  note: string | null;
}

/** Map a planned disposition of either vocabulary (the preview's or the plan event's) to a mark. */
export function predictedOf(planned: string, tier: string | null): Predicted {
  switch (planned) {
    case 'load-survey':
      return 'survey';
    case 'load-user':
      return 'user';
    case 'load':
      return tier === 'survey' ? 'survey' : 'user';
    case 'compute':
    case 'marker':
    case 'skipped':
    case 'pruned':
    case 'untargeted':
      return planned;
    default:
      return 'unknown';
  }
}

/** Steps the survey tier holds. */
export function surveyBackedSteps(preview: Preview | null): Set<string> {
  return new Set(
    (preview?.steps ?? []).filter((step) => step.held_by.includes('survey')).map((step) => step.step_id),
  );
}

/** Results a step's hash can be compared with: statuses that read or wrote one. */
const RESULT_STATUSES: ReadonlySet<StepStatus> = new Set(['computed', 'hit-user-cache', 'hit-survey-cache', 'marker']);

/** The run's result for a step was made with another hash than the preview's (FR-54). */
export function staleResult(ahead: StepPreview | undefined, actual: StepRunState | undefined): boolean {
  return Boolean(ahead && actual?.hash && RESULT_STATUSES.has(actual.status) && actual.hash !== ahead.step_hash);
}

function savedLetter(step: StepPreview | undefined): 'S' | 'U' | null {
  const tier = step?.held_by[0];
  if (tier === 'survey') return 'S';
  if (tier === 'user') return 'U';
  return null;
}

/**
 * What the marks read from a run: its step states, and whether it is still
 * going. Logs and warnings are left out, so log traffic does not change it.
 */
export interface RunMarks {
  steps: Record<string, StepRunState>;
  /** The run has no run_finished yet; its plan then gives the outline. */
  live: boolean;
}

export function runMarks(run: RunState | null): RunMarks | null {
  return run && { steps: run.steps, live: run.finished === null };
}

/**
 * The marks of every step of a graph.
 *
 * Args:
 *   graph: The session graph.
 *   preview: The plan preview, or null before it loads.
 *   run: The marks of the run shown (runMarks), or null.
 */
export function stepBadges(graph: Graph, preview: Preview | null, run: RunMarks | null): Record<string, StepBadge> {
  const previews = new Map((preview?.steps ?? []).map((step) => [step.step_id, step]));
  const live = run?.live ?? false;
  const badges: Record<string, StepBadge> = {};
  for (const node of stepNodes(graph)) {
    const ahead = previews.get(node.id);
    const actual = run?.steps[node.id];
    let predicted: Predicted | null = ahead ? predictedOf(ahead.planned, ahead.tier) : null;
    if (live && actual?.planned) predicted = predictedOf(actual.planned, actual.tier);
    const saved = savedLetter(ahead);
    const counts = actual?.instances;
    badges[node.id] = {
      predicted,
      actual: actual ? actual.status : null,
      progress: counts && counts.count !== null ? { done: counts.done, count: counts.count } : null,
      saved,
      willSave: saved === null && Boolean(ahead && (ahead.pinned || ahead.checkpoint === 'will-save')),
      checkpointed: node.checkpoint === 'always',
      stale: staleResult(ahead, actual),
      surveyBacked: Boolean(ahead?.held_by.includes('survey')),
      note: ahead?.note ?? null,
    };
  }
  return badges;
}

const SUMMARY: [Predicted, (count: number) => string][] = [
  ['survey', (count) => `${count} survey`],
  ['user', (count) => `${count} user`],
  ['marker', (count) => `${count} done`],
  ['compute', (count) => `${count} to compute`],
  ['unknown', (count) => `${count} not yet known`],
];

/** The summary of a sub-recipe none of whose steps the run uses (all skipped, pruned or untargeted). */
export const NOT_IN_RUN = 'not in this run';

/**
 * A sub-recipe's predicted marks counted, such as "9 survey, 3 user, 2 to
 * compute"; NOT_IN_RUN when the run uses none of its steps; '' before any
 * prediction.
 */
export function includeSummary(include: GraphInclude, badges: Record<string, StepBadge>): string {
  const counts = new Map<Predicted, number>();
  for (const id of include.steps) {
    const predicted = badges[id]?.predicted;
    if (predicted) counts.set(predicted, (counts.get(predicted) ?? 0) + 1);
  }
  if (counts.size === 0) return '';
  const parts = SUMMARY.filter(([kind]) => counts.get(kind)).map(([kind, text]) => text(counts.get(kind)!));
  return parts.length > 0 ? parts.join(', ') : NOT_IN_RUN;
}

/** The letter a status or prediction shows: S survey, U user, M marker. */
export function tierLetter(badge: StepBadge): 'S' | 'U' | 'M' | null {
  const status = badge.actual && badge.actual !== 'queued' && badge.actual !== 'running' ? badge.actual : null;
  if (status === 'hit-survey-cache') return 'S';
  if (status === 'hit-user-cache') return 'U';
  if (status === 'marker') return 'M';
  if (status) return null;
  if (badge.predicted === 'survey') return 'S';
  if (badge.predicted === 'user') return 'U';
  if (badge.predicted === 'marker') return 'M';
  return null;
}
