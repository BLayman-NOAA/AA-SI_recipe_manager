import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LogEntry } from '../src/api/models';
import { filterLogs, lastEntries, NO_FILTER, stepsOf } from '../src/logs/logList';
import { LogsPane } from '../src/logs/LogsPane';
import { ProblemList, previewProblems, RunFailureList } from '../src/problems/ProblemsPane';
import { parseRunEvents } from '../src/runs/events';
import { failedSteps } from '../src/runs/reducer';
import { openStepLog, useRunStore } from '../src/runs/store';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import failingText from './fixtures/failing.events.jsonl?raw';
import { mockApi, renderWithClient, withSubGraph } from './helpers';

const SID = '20261013-153012-a1b2c3';
const failing = parseRunEvents(failingText);

function entry(seq: number, step: string | null, stream: string, text: string): LogEntry {
  return { seq, time: seq, step, stream, text, buffered: false, instance: null };
}

const ENTRIES = [
  entry(1, 'start', 'stdout', 'reading 3 files'),
  entry(2, 'start', 'stderr', 'Warning: slow disk'),
  entry(3, 'blow', 'logging', 'INFO starting blow'),
  entry(4, 'blow', 'traceback', 'Traceback\nRuntimeError: boom'),
  entry(5, null, 'stdout', 'run summary'),
];

function runRecord(id: string, status: string) {
  return { id, session: SID, status, created: '2026-10-14T10:00:00Z', started: '2026-10-14T10:00:01Z' };
}

/** The failing fixture run held as the last finished run. */
function holdFailingRun() {
  const store = useRunStore.getState();
  store.resetFor(SID);
  store.begin('r2');
  store.apply('r2', failing);
  store.finish('r2');
}

beforeEach(() => {
  useSessionStore.getState().setSid(null);
  useView.getState().resetScope();
  useRunStore.getState().resetFor(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('logList', () => {
  it('filters by step, stream and text', () => {
    expect(filterLogs(ENTRIES, { ...NO_FILTER, step: 'blow' }).map((item) => item.seq)).toEqual([3, 4]);
    expect(filterLogs(ENTRIES, { ...NO_FILTER, stream: 'stdout' }).map((item) => item.seq)).toEqual([1, 5]);
    expect(filterLogs(ENTRIES, { ...NO_FILTER, text: 'BOOM' }).map((item) => item.seq)).toEqual([4]);
    expect(filterLogs(ENTRIES, { step: 'start', stream: 'stderr', text: '' }).map((item) => item.seq)).toEqual([2]);
    expect(filterLogs(ENTRIES, NO_FILTER)).toHaveLength(5);
  });

  it('lists the steps with entries and keeps the newest entries', () => {
    expect(stepsOf(ENTRIES)).toEqual(['start', 'blow']);
    expect(lastEntries(ENTRIES, 2)).toEqual({ shown: ENTRIES.slice(3), hidden: 3 });
    expect(lastEntries(ENTRIES)).toEqual({ shown: ENTRIES, hidden: 0 });
  });
});

describe('LogsPane', () => {
  it('shows the run held by the store, filtered by step and stream', async () => {
    const user = userEvent.setup();
    mockApi({ [`GET /api/sessions/${SID}/runs`]: [runRecord('r2', 'failed'), runRecord('r1', 'completed')] });
    useSessionStore.getState().setSid(SID);
    holdFailingRun();
    renderWithClient(<LogsPane />);

    const log = screen.getByRole('log', { name: 'Run log' });
    expect(within(log).getByText(/RuntimeError: boom/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Run' })).toHaveDisplayValue(/run 2: failed/));

    await user.selectOptions(screen.getByRole('combobox', { name: 'Stream' }), 'stdout');
    expect(within(log).queryByText(/RuntimeError/)).toBeNull();
    expect(within(log).getByText('No log entries.')).toBeInTheDocument();
  });

  it('opens on a step traceback from openStepLog', () => {
    mockApi({ [`GET /api/sessions/${SID}/runs`]: [runRecord('r2', 'failed')] });
    useSessionStore.getState().setSid(SID);
    holdFailingRun();
    renderWithClient(<LogsPane />);
    act(() => openStepLog('blow'));
    expect(useRunStore.getState().logStep).toBe('blow');
    expect(useRunStore.getState().logRun).toBe('r2');
    expect(screen.getByRole('combobox', { name: 'Step' })).toHaveValue('blow');
    const traceback = document.querySelector('[data-stream="traceback"]');
    expect(traceback).toHaveTextContent('RuntimeError: boom');
    expect(screen.getByRole('checkbox', { name: 'Follow' })).not.toBeChecked();
  });

  it('fetches the log of an older run from the server', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}/runs`]: [runRecord('r2', 'failed'), runRecord('r1', 'completed')],
      'GET /api/runs/r1/log': {
        run_id: 'r1',
        step: null,
        stream: null,
        entries: [entry(7, 'start', 'stdout', 'from the server')],
        truncated: false,
      },
    });
    useSessionStore.getState().setSid(SID);
    holdFailingRun();
    renderWithClient(<LogsPane />);
    const select = screen.getByRole('combobox', { name: 'Run' });
    await waitFor(() => expect(within(select).getAllByRole('option')).toHaveLength(2));
    await user.selectOptions(select, 'r1');
    expect(await screen.findByText('from the server')).toBeInTheDocument();
  });

  it('asks for a run when the session has none', async () => {
    mockApi({ [`GET /api/sessions/${SID}/runs`]: [] });
    useSessionStore.getState().setSid(SID);
    useRunStore.getState().resetFor(SID);
    renderWithClient(<LogsPane />);
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
  });
});

describe('Problems pane additions', () => {
  it('lists the failed steps of the last run; a click selects the step and opens its log', async () => {
    const user = userEvent.setup();
    holdFailingRun();
    const failures = failedSteps(useRunStore.getState().last!);
    const graph = structuredClone(withSubGraph);
    renderWithClient(<RunFailureList failures={failures} graph={graph} />);
    await user.click(screen.getByRole('button', { name: /blow/ }));
    expect(useView.getState().selected).toEqual({ kind: 'step', id: 'blow' });
    expect(useRunStore.getState().logStep).toBe('blow');
  });

  it('shows the preview warnings and blockers as warnings', () => {
    const problems = previewProblems({
      recipe: 'r',
      version: '1',
      mode: 'all',
      targets: [],
      force_steps: [],
      tiers: {},
      write_tier: null,
      steps: [],
      blockers: ['start'],
      warnings: ['survey tier not reachable'],
    });
    renderWithClient(<ProblemList problems={problems} graph={null} />);
    expect(screen.getByText('Preview: survey tier not reachable')).toBeInTheDocument();
    expect(screen.getByText(/later runs start before them: start/)).toBeInTheDocument();
  });
});
