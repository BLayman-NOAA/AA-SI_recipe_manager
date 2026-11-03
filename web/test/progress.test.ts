// The progress lines the reducer adds to a run's log. The server's
// tail.log_entries is held to the same expectation file
// (tests/server/test_sse.py), so a run reads the same live or fetched.
import { describe, expect, it } from 'vitest';
import { filterLogs, NO_FILTER, stepsOf } from '../src/logs/logList';
import { parseRunEvents } from '../src/runs/events';
import { formatStepTime, progressText, PROGRESS_STREAM } from '../src/runs/progress';
import { initialRunState, reduceEvents } from '../src/runs/reducer';
import cachedText from './fixtures/cached.events.jsonl?raw';
import failingText from './fixtures/failing.events.jsonl?raw';
import flatText from './fixtures/flat.events.jsonl?raw';
import mappedText from './fixtures/mapped.events.jsonl?raw';
import expected from './fixtures/progress.expected.json';
import timedText from './fixtures/timed.events.jsonl?raw';

const FIXTURES: Record<string, string> = {
  flat: flatText,
  mapped: mappedText,
  failing: failingText,
  cached: cachedText,
  timed: timedText,
};

function logsOf(name: string) {
  return reduceEvents(initialRunState('r1'), parseRunEvents(FIXTURES[name])).logs;
}

describe('progress lines', () => {
  it.each(Object.keys(FIXTURES))('match the server for the %s run', (name) => {
    const progress = filterLogs(logsOf(name), { ...NO_FILTER, stream: PROGRESS_STREAM });
    expect(progress.map((entry) => [entry.seq, entry.step, entry.text])).toEqual(
      (expected as Record<string, unknown[]>)[name],
    );
    expect(progress.every((entry) => entry.instance === null && !entry.buffered)).toBe(true);
  });

  it('come before the traceback of a failed step and stay with the step filter', () => {
    const logs = logsOf('failing');
    const blow = filterLogs(logs, { ...NO_FILTER, step: 'blow' });
    expect(blow.map((entry) => entry.stream)).toEqual(['progress', 'progress', 'traceback']);
    expect(blow[1].text).toBe('[2/2] blow ... failed (0.00s)');
    expect(stepsOf(logs)).toEqual(['start', 'blow']);
  });

  it('keep the step output between a step start and its finish', () => {
    const logs = logsOf('timed');
    expect(filterLogs(logs, { ...NO_FILTER, step: 'proc' }).map((entry) => entry.text)).toEqual([
      '[1/4] proc ... start (3 instances)',
      'working\n',
      '[1/4] proc ... computed (3 instances: 1 computed, 2 loaded; sum 6.00s, avg 2.00s, min 1.00s, max 3.00s)',
    ]);
  });

  it('are not written for other events', () => {
    expect(progressText({ kind: 'plan', data: {} })).toBeNull();
    expect(progressText({ kind: 'log', data: { step: 'a', stream: 'stdout', text: 'x' } })).toBeNull();
    expect(progressText({ kind: 'instance_finished', data: { step: 'a', members: {} } })).toBeNull();
  });
});

describe('formatStepTime', () => {
  it('matches the CLI _format_step_time', () => {
    expect(formatStepTime(1.5)).toBe('1.50s');
    expect(formatStepTime(1.5, [1.5])).toBe('1.50s');
    expect(formatStepTime(6.0, [1.0, 2.0, 3.0])).toBe('3 instances: sum 6.00s, avg 2.00s, min 1.00s, max 3.00s');
  });
});
