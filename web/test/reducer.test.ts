// Recorded with: aa-recipe run tests/fixtures/client_recipes/<name>.yaml --user-cache-dir <tmp>
// --outputs-dir <tmp> --events <file> (PYTHONPATH=tests), local paths replaced.
import { describe, expect, it } from 'vitest';
import { parseRunEvent, parseRunEvents, type RunEvent } from '../src/runs/events';
import {
  failedSteps,
  initialRunState,
  reduceEvent,
  reduceEvents,
  runProgress,
  terminal,
  type RunState,
} from '../src/runs/reducer';
import { runMarks, stepBadges } from '../src/runs/badges';
import failingText from './fixtures/failing.events.jsonl?raw';
import flatText from './fixtures/flat.events.jsonl?raw';
import mappedText from './fixtures/mapped.events.jsonl?raw';
import { withSubGraph } from './helpers';

const flat = parseRunEvents(flatText);
const mapped = parseRunEvents(mappedText);
const failing = parseRunEvents(failingText);

function fold(events: RunEvent[], runId = 'r1'): RunState {
  return events.reduce(reduceEvent, initialRunState(runId));
}

function statuses(state: RunState): Record<string, string> {
  return Object.fromEntries(Object.entries(state.steps).map(([id, step]) => [id, step.status]));
}

/** A deterministic shuffle. */
function shuffled<T>(items: T[], seed: number): T[] {
  const copy = items.slice();
  let value = seed;
  for (let index = copy.length - 1; index > 0; index -= 1) {
    value = (value * 9301 + 49297) % 233280;
    const other = Math.floor((value / 233280) * (index + 1));
    [copy[index], copy[other]] = [copy[other], copy[index]];
  }
  return copy;
}

function event(seq: number, kind: string, data: Record<string, unknown>): RunEvent {
  return { seq, time: seq, kind, run_id: 'core', data };
}

describe('parseRunEvent', () => {
  it('parses an SSE data field and rejects other JSON', () => {
    expect(parseRunEvent('{"seq": 3, "time": 1, "kind": "log", "run_id": null, "data": {"text": "x"}}').kind).toBe(
      'log',
    );
    expect(() => parseRunEvent('{"hello": 1}')).toThrow();
  });
});

describe('reduceEvent', () => {
  it('folds a recorded flat run to three computed steps', () => {
    const state = fold(flat);
    expect(state.status).toBe('completed');
    expect(statuses(state)).toEqual({ start: 'computed', bump: 'computed', scaled: 'computed' });
    expect(state.lastSeq).toBe(flat.length);
    expect(state.started?.recipe).toBe('client_flat');
    expect(state.finished?.executed).toEqual(['start', 'bump', 'scaled']);
    expect(state.steps.bump.hash).toMatch(/^7518d8a3/);
    expect(runProgress(state)).toEqual({ done: 3, total: 3 });
  });

  it('gives the same state when the events are replayed', () => {
    const once = fold(flat);
    const twice = fold([...flat, ...flat]);
    expect(twice).toEqual(once);
    expect(reduceEvent(once, flat[3])).toBe(once);
  });

  it('reaches the same terminal statuses from events delivered out of order', () => {
    const expected = statuses(fold(mapped));
    for (const seed of [1, 7, 42, 99]) {
      const state = fold(shuffled(mapped, seed));
      expect(statuses(state)).toEqual(expected);
      expect(state.status).toBe('completed');
    }
  });

  it('never moves a finished step back to running', () => {
    const finished = fold(flat.filter((item) => item.kind !== 'run_finished'));
    const late = reduceEvent(finished, event(100, 'step_started', { step: 'bump', index: 2, total: 3 }));
    expect(late.steps.bump.status).toBe('computed');
  });

  it('lands a step_finished that arrives before its step_started', () => {
    const state = fold([
      event(2, 'step_finished', { step: 'x', disposition: 'hit-user-cache', tier: 'user', hash: 'h', index: 1, total: 1 }),
      event(1, 'step_started', { step: 'x', index: 1, total: 1, instances: null, chain: null }),
    ]);
    expect(state.steps.x.status).toBe('hit-user-cache');
    expect(state.steps.x.tier).toBe('user');
  });

  it('counts instance_finished per member of a mapped chain', () => {
    const running = fold(mapped.slice(0, 7));
    expect(running.steps.proc.status).toBe('running');
    expect(running.steps.proc.instances).toMatchObject({ count: 3, done: 1, computed: 1 });
    const state = fold(mapped);
    for (const id of ['proc', 'proc2']) {
      expect(state.steps[id].instances).toMatchObject({ count: 3, done: 3, computed: 3, cached: 0 });
      expect(state.steps[id].chain).toEqual(['proc', 'proc2']);
    }
  });

  it('counts instance cache hits while the run is going', () => {
    // Core reports a cached instance as "hit" (executor/engine/tasks.py); the fixtures have none.
    const state = fold([
      event(1, 'step_started', { step: 'proc', index: 1, total: 2, instances: 3, chain: ['proc', 'proc2'] }),
      event(2, 'step_started', { step: 'proc2', index: 2, total: 2, instances: 3, chain: ['proc', 'proc2'] }),
      event(3, 'instance_finished', { step: 'proc', members: { proc: 'hit', proc2: 'computed' }, instance: 0, count: 3 }),
      event(4, 'instance_finished', { step: 'proc', members: { proc: 'hit', proc2: 'hit-user-cache' }, instance: 1, count: 3 }),
      event(5, 'instance_finished', { step: 'proc', members: { proc: 'skipped', proc2: 'hit' }, instance: 2, count: 3 }),
    ]);
    expect(state.steps.proc.status).toBe('running');
    expect(state.steps.proc.instances).toEqual({ count: 3, done: 3, computed: 0, cached: 2, skipped: 1 });
    expect(state.steps.proc2.instances).toEqual({ count: 3, done: 3, computed: 1, cached: 2, skipped: 0 });
  });

  it('marks a failed step with its error and a traceback log entry', () => {
    const state = fold(failing);
    expect(state.status).toBe('failed');
    expect(state.steps.blow.status).toBe('failed');
    expect(state.steps.blow.error?.message).toContain('boom');
    const traceback = state.logs.find((entry) => entry.stream === 'traceback');
    expect(traceback?.step).toBe('blow');
    expect(traceback?.text).toContain('RuntimeError: boom');
    expect(failedSteps(state).map((failure) => failure.step)).toEqual(['blow']);
  });

  it('stops the other running members of a failed chain', () => {
    const state = fold([
      event(1, 'step_started', { step: 'a', index: 1, total: 2, instances: 3, chain: ['a', 'b'] }),
      event(2, 'step_started', { step: 'b', index: 2, total: 2, instances: 3, chain: ['a', 'b'] }),
      event(3, 'step_failed', { step: 'a', index: 1, total: 2, error_type: 'E', message: 'm', traceback: 't', chain: ['a', 'b'] }),
    ]);
    expect(statuses(state)).toEqual({ a: 'failed', b: 'stopped' });
  });

  it('stops running steps and leaves queued ones not run on a cancelled run', () => {
    const state = fold([
      event(1, 'run_started', { recipe: 'r' }),
      event(2, 'plan', {
        steps: {
          a: { hash: 'ha', planned: 'compute', tier: null },
          b: { hash: 'hb', planned: 'compute', tier: null },
          c: { hash: 'hc', planned: 'untargeted', tier: null },
        },
        blockers: [],
      }),
      event(3, 'step_started', { step: 'a', index: 1, total: 2, instances: null, chain: null }),
      event(4, 'run_finished', { status: 'cancelled', executed: [], skipped: [], pruned: [], untargeted: ['c'] }),
    ]);
    expect(state.status).toBe('cancelled');
    expect(statuses(state)).toEqual({ a: 'stopped', b: 'not-run', c: 'untargeted' });
    expect(terminal(state.steps.a.status)).toBe(true);
  });

  it('accepts a synthetic interrupted run_finished', () => {
    const partial = fold(flat.slice(0, 5));
    const state = reduceEvent(
      partial,
      event(6, 'run_finished', { status: 'interrupted', synthetic: true, executed: [], skipped: [], pruned: [], untargeted: [] }),
    );
    expect(state.status).toBe('interrupted');
    expect(state.finished?.synthetic).toBe(true);
    expect(statuses(state)).toEqual({ start: 'computed', bump: 'stopped', scaled: 'not-run' });
  });

  it('appends log and warning events', () => {
    const state = reduceEvents(initialRunState('r1'), [
      event(1, 'log', { step: 'a', stream: 'stdout', text: 'hello', buffered: false, instance: 2 }),
      event(2, 'warning', { step: 'a', message: 'careful' }),
    ]);
    expect(state.logs).toEqual([{ seq: 1, time: 1, step: 'a', stream: 'stdout', text: 'hello', buffered: false, instance: 2 }]);
    expect(state.warnings).toEqual([{ step: 'a', message: 'careful' }]);
  });

  it('keeps the steps object for a batch of log events, so the marks do not change', () => {
    const before = fold([
      event(1, 'step_finished', { step: 'seed', disposition: 'computed', tier: 'user', hash: 'h', index: 1, total: 2 }),
      event(2, 'step_started', { step: 'total', index: 2, total: 2, instances: null, chain: null }),
    ]);
    const after = reduceEvents(before, [
      event(3, 'log', { step: 'total', stream: 'stdout', text: 'one', buffered: false, instance: null }),
      event(4, 'warning', { step: 'total', message: 'careful' }),
    ]);
    expect(after).not.toBe(before);
    expect(after.steps).toBe(before.steps);
    // Each step event also writes its progress line.
    expect(before.logs.map((entry) => entry.stream)).toEqual(['progress', 'progress']);
    expect(after.logs.map((entry) => entry.stream)).toEqual(['progress', 'progress', 'stdout']);
    const badges = stepBadges(withSubGraph, null, runMarks(after));
    expect(badges).toEqual(stepBadges(withSubGraph, null, runMarks(before)));
    expect(badges.total.actual).toBe('running');
    const stepped = reduceEvent(after, event(5, 'step_finished', { step: 'total', disposition: 'computed', index: 2, total: 2 }));
    expect(stepped.steps).not.toBe(after.steps);
    expect(stepped.steps.seed).toBe(after.steps.seed);
    expect(stepped.logs.slice(0, 3)).toEqual(after.logs);
    expect(stepped.logs[3].text).toBe('[2/2] total ... computed');
  });

  it('leaves the input state unchanged', () => {
    const before = fold(flat.slice(0, 4));
    const snapshot = structuredClone({ ...before, seen: [...before.seen] });
    reduceEvents(before, flat.slice(4));
    expect({ ...before, seen: [...before.seen] }).toEqual(snapshot);
  });
});
