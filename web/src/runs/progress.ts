/**
 * Progress lines for the run log, worded like the CLI's: "[3/9] seed ... start"
 * and "[3/9] seed ... computed (1.20s)".
 *
 * Pure. The server's tail.log_entries writes the same text for runs the
 * client does not hold; test/fixtures/progress.expected.json keeps the two
 * in step.
 */
import type { RunEvent } from './events';
import { LOADED_SURVEY, LOADED_USER, NOT_NEEDED, SKIPPED_CACHED } from './wording';

/** The Logs pane stream of progress lines. */
export const PROGRESS_STREAM = 'progress';

const FINISHED_LABEL: Record<string, string> = {
  computed: 'computed',
  'hit-user-cache': LOADED_USER,
  'hit-survey-cache': LOADED_SURVEY,
  marker: 'already done',
  skipped: SKIPPED_CACHED,
  pruned: SKIPPED_CACHED,
  untargeted: NOT_NEEDED,
};

/** Dispositions whose time is worth showing. */
const TIMED: ReadonlySet<string> = new Set(['computed', 'hit-user-cache', 'hit-survey-cache', 'marker']);

type Data = Record<string, unknown>;

function seconds(value: number): string {
  return `${value.toFixed(2)}s`;
}

function count(value: number, noun: string): string {
  return `${value} ${noun}${value === 1 ? '' : 's'}`;
}

/** The spread of a mapped step's instance times: "sum 6.00s, avg 2.00s, min 1.00s, max 3.00s". */
function spread(times: number[]): string {
  const sum = times.reduce((total, time) => total + time, 0);
  return (
    `sum ${seconds(sum)}, avg ${seconds(sum / times.length)}, ` +
    `min ${seconds(Math.min(...times))}, max ${seconds(Math.max(...times))}`
  );
}

/** A step's time, with the spread when it fanned out; the CLI's _format_step_time. */
export function formatStepTime(elapsed: number, instanceSeconds: number[] = []): string {
  if (instanceSeconds.length < 2) return seconds(elapsed);
  return `${instanceSeconds.length} instances: ${spread(instanceSeconds)}`;
}

function prefix(data: Data): string {
  return `[${String(data.index ?? '?')}/${String(data.total ?? '?')}] ${String(data.step ?? '?')} ...`;
}

function runStarted(data: Data): string {
  const targets = ((data.targets as string[] | undefined) ?? []).join(', ');
  const parts = [data.only ? `only ${targets}` : targets ? `to ${targets}` : 'all'];
  if (data.force) parts.push('force');
  const forced = (data.force_steps as string[] | undefined) ?? [];
  if (forced.length) parts.push(`force ${forced.join(', ')}`);
  if (typeof data.step_count === 'number') parts.push(count(data.step_count, 'step'));
  return `run ... start (${parts.join(', ')})`;
}

function stepStarted(data: Data): string {
  const instances = data.instances;
  return `${prefix(data)} start${typeof instances === 'number' ? ` (${count(instances, 'instance')})` : ''}`;
}

function stepFinished(data: Data): string {
  const disposition = String(data.disposition ?? '');
  const parts: string[] = [];
  const instances = data.instances as { count: number; computed: number; cached: number; skipped: number } | null;
  const times = (data.instance_seconds as number[] | undefined) ?? [];
  const elapsed = typeof data.seconds === 'number' ? data.seconds : null;
  if (instances) {
    const counts = [
      instances.computed && `${instances.computed} computed`,
      instances.cached && `${instances.cached} loaded`,
      instances.skipped && `${instances.skipped} skipped`,
    ].filter(Boolean);
    const many = count(instances.count, 'instance');
    parts.push(counts.length ? `${many}: ${counts.join(', ')}` : many);
  }
  if (TIMED.has(disposition) && elapsed !== null) {
    parts.push(instances && times.length >= 2 ? spread(times) : formatStepTime(elapsed, times));
  }
  const label = FINISHED_LABEL[disposition] ?? disposition;
  return `${prefix(data)} ${label}${parts.length ? ` (${parts.join('; ')})` : ''}`;
}

function stepFailed(data: Data): string {
  const elapsed = typeof data.seconds === 'number' ? ` (${formatStepTime(data.seconds)})` : '';
  return `${prefix(data)} failed${elapsed}`;
}

function runFinished(data: Data): string {
  const elapsed = typeof data.seconds === 'number' ? ` (${seconds(data.seconds)})` : '';
  const message = data.message ? `: ${String(data.message)}` : '';
  return `run ... ${String(data.status || 'finished')}${elapsed}${message}`;
}

/** The progress line of an event, or null for kinds without one. */
export function progressText(event: Pick<RunEvent, 'kind' | 'data'>): string | null {
  const data = event.data ?? {};
  switch (event.kind) {
    case 'run_started':
      return runStarted(data);
    case 'step_started':
      return stepStarted(data);
    case 'step_finished':
      return stepFinished(data);
    case 'step_failed':
      return stepFailed(data);
    case 'run_finished':
      return runFinished(data);
    default:
      return null;
  }
}
