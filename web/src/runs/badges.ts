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
  /**
   * Outline, from the preview; while a run goes, from the preview it was
   * started with, else from its plan event.
   */
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
  /** A result for the current hash is on disk (savedOnDisk), so the echogram pane can draw it. */
  onDisk: boolean;
  /** Some instances of a fanned out step are held and not all, as "2 of 3". */
  partly: { count: number; of: number | null } | null;
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

/** How the client names the pruned and skipped statuses. */
export const NOT_NEEDED = 'not needed';

/** The tooltip of a step the run does not need. */
export const NOT_NEEDED_TITLE = "Not needed: a later step's result is cached";

/** A planned disposition or step status as the client words it. */
export function statusText(status: string): string {
  return status === 'pruned' || status === 'skipped' ? NOT_NEEDED : status;
}

/** The step is not needed by the run, predicted or actual. */
export function notNeeded(badge: StepBadge): boolean {
  return [badge.predicted, badge.actual].some((status) => status === 'pruned' || status === 'skipped');
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

/** Instances of a fanned out step some tier holds, when no tier holds them all. */
export function partlyHeld(ahead: StepPreview | undefined): { count: number; of: number | null } | null {
  if (!ahead || ahead.held_by.length > 0) return null;
  const count = Math.max(0, ...Object.values(ahead.instances_held));
  return count > 0 ? { count, of: ahead.instances } : null;
}

/**
 * Whether a step's result for the current hash is on disk, so the echogram
 * pane can draw it.
 *
 * A tier holds it (the current preview's held_by), or some of its instances
 * (partlyHeld), or the run shown read it from one, or computed it and wrote
 * it. Whether the run wrote it comes from the preview it was started with
 * (pinned, will-save or saved); only without one is a target taken as
 * saved, since a run pins its target unless the recipe says checkpoint:
 * never. A result computed in memory only does not count, and nothing from a
 * run whose hash differs from the preview's does.
 *
 * Args:
 *   ahead: The step in the current preview.
 *   actual: The step in the run shown.
 *   start: The step in the preview the run was started with.
 *   targeted: The run was asked to run to the step.
 */
export function savedOnDisk(
  ahead: StepPreview | undefined,
  actual: StepRunState | undefined,
  start?: StepPreview,
  targeted = false,
): boolean {
  if (ahead && (ahead.held_by.length > 0 || partlyHeld(ahead))) return true;
  if (!actual || staleResult(ahead, actual)) return false;
  if (actual.status === 'hit-user-cache' || actual.status === 'hit-survey-cache') return true;
  const written = start ? start.pinned || start.checkpoint !== 'memory-only' : targeted;
  return actual.status === 'computed' && written;
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
  /** The run has no run_finished yet; its start preview, else its plan, then gives the outline. */
  live: boolean;
  /**
   * The preview the client started the run with. It resolves mapped steps
   * per instance, which the plan event cannot, so its outline wins.
   */
  preview: Preview | null;
  /** The steps the run was asked to run to, which it pins; absent when not known. */
  targets?: string[];
}

export function runMarks(run: RunState | null, preview: Preview | null = null): RunMarks | null {
  return run && { steps: run.steps, live: run.finished === null, preview, targets: run.started?.targets ?? [] };
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
  const startPreview = new Map((run?.preview?.steps ?? []).map((step) => [step.step_id, step]));
  const started = live ? startPreview : new Map<string, StepPreview>();
  const badges: Record<string, StepBadge> = {};
  for (const node of stepNodes(graph)) {
    const ahead = previews.get(node.id);
    const actual = run?.steps[node.id];
    const atStart = started.get(node.id);
    let predicted: Predicted | null = ahead ? predictedOf(ahead.planned, ahead.tier) : null;
    if (atStart) predicted = predictedOf(atStart.planned, atStart.tier);
    else if (live && actual?.planned) predicted = predictedOf(actual.planned, actual.tier);
    const planned = atStart ?? ahead;
    const saved = savedLetter(ahead);
    const counts = actual?.instances;
    badges[node.id] = {
      predicted,
      actual: actual ? actual.status : null,
      progress: counts && counts.count !== null ? { done: counts.done, count: counts.count } : null,
      saved,
      willSave: saved === null && Boolean(planned && (planned.pinned || planned.checkpoint === 'will-save')),
      checkpointed: node.checkpoint === 'always',
      stale: staleResult(ahead, actual),
      surveyBacked: Boolean(ahead?.held_by.includes('survey')),
      onDisk: savedOnDisk(ahead, actual, startPreview.get(node.id), run?.targets?.includes(node.id) ?? false),
      partly: partlyHeld(ahead),
      note: planned?.note ?? null,
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

/** Statuses of a step the run does not use. */
const OUT_OF_RUN: ReadonlySet<StepStatus> = new Set(['untargeted', 'pruned']);

/** Statuses of a step the run has done: computed, loaded, or skipped at run time. */
const DONE: ReadonlySet<StepStatus> = new Set(['computed', 'hit-user-cache', 'hit-survey-cache', 'marker', 'skipped']);

/**
 * A sub-recipe's progress in a run: "5 of 13 done", "complete" once every
 * member the run uses is done, or "failed"; null when the run uses none.
 * While mapped members run, their items are added, as "2 of 8 done, 150 of
 * 300 items".
 *
 * Args:
 *   include: The sub-recipe.
 *   steps: The run's step states by id.
 */
export function includeProgress(include: GraphInclude, steps: Record<string, StepRunState>): string | null {
  const members = include.steps.map((id) => steps[id]).filter((step) => step && !OUT_OF_RUN.has(step.status));
  if (members.length === 0) return null;
  if (members.some((step) => step.status === 'failed')) return 'failed';
  const done = members.filter((step) => DONE.has(step.status)).length;
  if (done === members.length) return 'complete';
  const text = `${done} of ${members.length} done`;
  const mapped = members.filter((step) => step.status === 'running' && step.instances.count !== null);
  if (mapped.length === 0) return text;
  const items = mapped.reduce((sum, step) => sum + step.instances.done, 0);
  const count = mapped.reduce((sum, step) => sum + step.instances.count!, 0);
  return `${text}, ${items} of ${count} items`;
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
