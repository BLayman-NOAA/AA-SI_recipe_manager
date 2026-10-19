/** Filtering the run log for the Logs pane. Pure. */
import type { LogEntry } from '../api/models';

export const LOG_STREAMS = ['progress', 'stdout', 'stderr', 'logging', 'traceback'] as const;

/** How many filtered entries the pane lists, the newest kept. */
export const LOG_LIMIT = 5000;

export interface LogFilter {
  /** A step id, or '' for every step. */
  step: string;
  /** A stream, or '' for every stream. */
  stream: string;
  /** Text the entry must contain, case insensitive. */
  text: string;
}

export const NO_FILTER: LogFilter = { step: '', stream: '', text: '' };

export function filterLogs(entries: LogEntry[], filter: LogFilter): LogEntry[] {
  const text = filter.text.trim().toLowerCase();
  return entries.filter(
    (entry) =>
      (!filter.step || entry.step === filter.step) &&
      (!filter.stream || entry.stream === filter.stream) &&
      (!text || entry.text.toLowerCase().includes(text)),
  );
}

/** Step ids with entries, in first seen order. */
export function stepsOf(entries: LogEntry[]): string[] {
  const steps = new Set<string>();
  for (const entry of entries) {
    if (entry.step) steps.add(entry.step);
  }
  return [...steps];
}

/** The last `limit` entries, and how many earlier ones are hidden. */
export function lastEntries(entries: LogEntry[], limit = LOG_LIMIT): { shown: LogEntry[]; hidden: number } {
  const hidden = Math.max(0, entries.length - limit);
  return { shown: hidden ? entries.slice(hidden) : entries, hidden };
}

/** An instance as text for an entry's prefix, or ''. */
export function instanceText(instance: unknown): string {
  if (instance === null || instance === undefined) return '';
  return typeof instance === 'string' ? instance : JSON.stringify(instance);
}
