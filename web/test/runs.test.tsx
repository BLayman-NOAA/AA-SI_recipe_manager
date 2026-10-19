import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Preview, RunRecord } from '../src/api/models';
import { intentFor, previewIntent, previewQuery, toStartRequest } from '../src/runs/intent';
import { parseRunEvents } from '../src/runs/events';
import { initialRunState, reduceEvents } from '../src/runs/reducer';
import { RunTable, runReason, runWhat } from '../src/runs/RunsPane';
import { shownRun, shownRunPreview, useRunStore } from '../src/runs/store';
import { CANCELLING_TEXT, cancellingText, runStatusText, runStatusTitle } from '../src/shell/StatusBar';
import failingText from './fixtures/failing.events.jsonl?raw';
import flatText from './fixtures/flat.events.jsonl?raw';

const flat = parseRunEvents(flatText);
const failing = parseRunEvents(failingText);

function record(id: string, overrides: Partial<RunRecord> = {}): RunRecord {
  return {
    schema: 1,
    id,
    session: 's1',
    created: '2026-10-14T10:00:00Z',
    started: '2026-10-14T10:00:01Z',
    finished: '2026-10-14T10:01:18Z',
    status: 'completed',
    pid: 1,
    pid_created: 1,
    exit_code: 0,
    mode: 'all',
    targets: [],
    force: false,
    force_steps: [],
    executor: null,
    cancel_requested_at: null,
    killed: false,
    summary: { executed: 3, skipped: 0, pruned: 0, untargeted: 1, seconds: 77 },
    manifest: null,
    outputs_dir: null,
    log: '/runs/run.log',
    last_seq: 9,
    ...overrides,
  };
}

beforeEach(() => {
  useRunStore.getState().resetFor('s1');
});

describe('intent', () => {
  it('builds intents from actions and the selection', () => {
    const step = { kind: 'step' as const, id: 'x' };
    expect(intentFor('to', step)).toEqual({ mode: 'to', targets: ['x'], force: false, forceSteps: [] });
    expect(intentFor('only', { kind: 'include', id: 'inc:a' })).toBeNull();
    expect(intentFor('forceAll', null)).toMatchObject({ mode: 'all', force: true });
    expect(intentFor('forceSelected', step)).toMatchObject({ mode: 'to', forceSteps: ['x'] });
    expect(previewIntent(null).mode).toBe('all');
    expect(previewIntent(step)).toMatchObject({ mode: 'to', targets: ['x'] });
    expect(toStartRequest(intentFor('only', step)!)).toEqual({ mode: 'only', targets: ['x'], force: false, force_steps: [] });
  });

  it('writes only the preview arguments that are set', () => {
    expect(previewQuery(previewIntent(null))).toBe('');
    expect(previewQuery({ mode: 'only', targets: ['a', 'b'], force: true, forceSteps: ['a'] })).toBe(
      '?targets=a&targets=b&only=true&force=true&force_steps=a',
    );
  });
});

describe('runStatusText', () => {
  it('says what the run is doing or how it ended', () => {
    const pending = initialRunState('r1');
    expect(runStatusText(pending, false)).toBe('starting');
    const running = reduceEvents(pending, flat.slice(0, 5));
    expect(runStatusText(running, false)).toBe('running 1/3');
    expect(runStatusText(running, true)).toBe('cancelling: waiting for bump to finish');
    expect(runStatusText(reduceEvents(pending, flat.slice(0, 4)), true)).toBe(CANCELLING_TEXT);
    expect(runStatusText(reduceEvents(pending, flat), false)).toMatch(/^completed in \d/);
    expect(runStatusText(reduceEvents(pending, failing), false)).toBe('failed at blow');
    const cancelled = reduceEvents(running, [
      { seq: 50, time: 1, kind: 'run_finished', run_id: null, data: { status: 'cancelled' } },
    ]);
    expect(runStatusText(cancelled, true)).toBe('cancelled');
  });

  it('names the steps a cancel waits for', () => {
    const started = (seq: number, step: string, index: number) => ({
      seq,
      time: seq,
      kind: 'step_started',
      run_id: null,
      data: { step, index, total: 9, instances: 3, chain: null },
    });
    const two = reduceEvents(initialRunState('r1'), [started(2, 'b', 2), started(1, 'a', 1)]);
    expect(cancellingText(two)).toBe('cancelling: waiting for a and b to finish');
    const four = reduceEvents(two, [started(3, 'c', 3), started(4, 'd', 4)]);
    expect(cancellingText(four)).toBe('cancelling: waiting for a, b and 2 more to finish');
  });

  it('shows the first sentence of a run end message the server wrote, and all of it as the tooltip', () => {
    const message = 'The run process stopped before it reported an end. More detail follows here.';
    const ended = reduceEvents(initialRunState('r1'), [
      ...flat.slice(0, 5),
      { seq: 6, time: 6, kind: 'run_finished', run_id: null, data: { status: 'failed', message, synthetic: true } },
    ]);
    expect(runStatusText(ended, false)).toBe('failed: The run process stopped before it reported an end.');
    expect(runStatusTitle(ended)).toBe(message);
    const silent = reduceEvents(initialRunState('r2'), [
      { seq: 1, time: 1, kind: 'run_finished', run_id: null, data: { status: 'failed', message: null } },
    ]);
    expect(runStatusText(silent, false)).toBe('failed, see the log');
    const interrupted = reduceEvents(initialRunState('r3'), [
      { seq: 1, time: 1, kind: 'run_finished', run_id: null, data: { status: 'interrupted', message, synthetic: true } },
    ]);
    expect(runStatusText(interrupted, false)).toBe('interrupted');
    expect(runStatusTitle(interrupted)).toBe(message);
  });
});

describe('run store', () => {
  it('moves a finished run to last and shows the current run first', () => {
    const store = useRunStore.getState();
    store.begin('r1');
    store.apply('r1', flat);
    store.finish('r1');
    expect(useRunStore.getState().current).toBeNull();
    expect(shownRun(useRunStore.getState())?.runId).toBe('r1');
    useRunStore.getState().begin('r2');
    expect(shownRun(useRunStore.getState())?.runId).toBe('r2');
    expect(useRunStore.getState().last?.runId).toBe('r1');
    useRunStore.getState().show('r1');
    expect(shownRun(useRunStore.getState())?.runId).toBe('r1');
    useRunStore.getState().show('r0');
    expect(useRunStore.getState().replay?.runId).toBe('r0');
  });

  it('keeps the start previews of the current and last runs only', () => {
    const preview = (id: string) => ({ recipe: id }) as unknown as Preview;
    const store = useRunStore.getState();
    store.begin('r1', preview('p1'));
    store.apply('r1', flat);
    expect(shownRunPreview(useRunStore.getState())).toEqual(preview('p1'));
    useRunStore.getState().begin('r2', preview('p2'));
    expect(Object.keys(useRunStore.getState().previews).sort()).toEqual(['r1', 'r2']);
    useRunStore.getState().show('r1');
    expect(shownRunPreview(useRunStore.getState())).toEqual(preview('p1'));
    useRunStore.getState().apply('r2', flat);
    useRunStore.getState().begin('r3');
    expect(Object.keys(useRunStore.getState().previews)).toEqual(['r2']);
    expect(shownRunPreview(useRunStore.getState())).toBeNull();
    useRunStore.getState().resetFor(null);
    expect(useRunStore.getState().previews).toEqual({});
  });

  it('ignores events for runs it does not hold', () => {
    const before = useRunStore.getState();
    before.apply('nope', flat);
    expect(useRunStore.getState().current).toBeNull();
    expect(useRunStore.getState().last).toBeNull();
  });
});

describe('RunTable', () => {
  it('lists the runs newest first with their summary, and its actions', async () => {
    const user = userEvent.setup();
    const onLogs = vi.fn();
    const onShow = vi.fn();
    const runs = [
      record('r2', { status: 'failed', mode: 'to', targets: ['blow'], summary: { executed: 1, skipped: 0, pruned: 0, untargeted: 0, failed_step: 'blow' } }),
      record('r1'),
    ];
    render(<RunTable runs={runs} shown="r1" onLogs={onLogs} onShow={onShow} />);
    const rows = screen.getAllByRole('row').slice(1);
    expect(rows[0]).toHaveTextContent('2');
    expect(rows[0]).toHaveTextContent('to blow');
    expect(rows[0]).toHaveTextContent('failed');
    expect(rows[0]).toHaveTextContent('1 / 0 / 0');
    expect(rows[1]).toHaveTextContent('77 s');
    expect(rows[1]).toHaveTextContent('3 / 0 / 1');
    const [logs] = screen.getAllByRole('button', { name: 'Logs' });
    await user.click(logs);
    expect(onLogs).toHaveBeenCalledWith('r2');
    const shows = screen.getAllByRole('button', { name: 'Show on diagram' });
    expect(shows[1]).toBeDisabled();
    await user.click(shows[0]);
    expect(onShow).toHaveBeenCalledWith('r2');
  });

  it('gives the failed step, else the first sentence of the end message, as the reason', () => {
    const message = 'The run process stopped before it reported an end. Detail.';
    const summary = { executed: 0, skipped: 0, pruned: 0, untargeted: 0 };
    expect(runReason(record('r', { status: 'failed', summary: { ...summary, failed_step: 'blow', message } }))).toBe('blow');
    expect(runReason(record('r', { status: 'failed', summary: { ...summary, message } }))).toBe(
      'The run process stopped before it reported an end.',
    );
    expect(runReason(record('r', { status: 'interrupted', summary: null }))).toBe('');
    expect(runReason(record('r'))).toBe('');
  });

  it('names what a run did', () => {
    expect(runWhat(record('r', { mode: 'only', targets: ['a'], force_steps: ['a'] }))).toBe('only a, force a');
    expect(runWhat(record('r', { force: true }))).toBe('all, force all');
  });
});
