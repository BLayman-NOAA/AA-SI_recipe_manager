import { act, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Graph } from '../src/api/graph';
import type { Preview } from '../src/api/models';
import { queryKeys } from '../src/api/queries';
import { ApiError } from '../src/api/client';
import { useEditingStore } from '../src/editing/store';
import { RunHint } from '../src/runs/RunHint';
import { useCurrentPreview, usePreviewIntent } from '../src/runs/usePreviewIntent';
import { useRunStore } from '../src/runs/store';
import {
  HINT_KEY,
  NO_RUN_CONTROLS,
  STOP_NOW_CONFIRM,
  upstreamMessage,
  useRunControls,
  type RunControls,
} from '../src/runs/useRunControls';
import { Toolbar } from '../src/shell/Toolbar';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import { apiErrorResponse, editableSession, makeSession, mockApi, renderWithClient, withSubGraph } from './helpers';

const SID = '20261013-153012-a1b2c3';
const RUNS = `POST /api/sessions/${SID}/runs`;
const EDITS = `POST /api/sessions/${SID}/edits`;

function Harness() {
  const run = useRunControls();
  return (
    <>
      <RunHint />
      <Toolbar run={run} onFit={() => {}} onToggleDirection={() => {}} />
    </>
  );
}

/** The run controls, as the echogram pane gets them from the Shell. */
let controls: ReturnType<typeof useRunControls> | null = null;
function ControlsProbe() {
  controls = useRunControls();
  return <RunHint />;
}

function runRecord(id: string, body: Record<string, unknown>) {
  return { id, session: SID, status: 'starting', reloaded: [], ...body };
}

function button(name: string): HTMLElement {
  return screen.getByRole('button', { name });
}

function openSession(graph: Graph = withSubGraph, routes: Record<string, unknown> = {}) {
  const fetchMock = mockApi({ [`GET /api/sessions/${SID}`]: makeSession({ graph }), ...routes });
  useSessionStore.getState().setSid(SID);
  useRunStore.getState().resetFor(SID);
  return fetchMock;
}

/** The JSON bodies sent to a route, in order. */
function bodies(fetchMock: ReturnType<typeof mockApi>, key: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => `${init?.method ?? 'GET'} ${String(url)}` === key)
    .map(([, init]) => JSON.parse(String(init?.body)));
}

beforeEach(() => {
  useSessionStore.getState().setSid(null);
  useView.getState().resetScope();
  useRunStore.getState().resetFor(null);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('Toolbar', () => {
  it('disables every run button with no session', () => {
    mockApi({});
    renderWithClient(<Harness />);
    for (const name of ['Dry run', 'Run all', 'Run to selected', 'Run selected', 'Force ▾', 'Cancel']) {
      expect(button(name)).toBeDisabled();
    }
  });

  it('needs a selected step for the selected actions, or a sub-recipe for Run to selected', async () => {
    openSession();
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run all')).toBeEnabled());
    expect(button('Dry run')).toBeEnabled();
    expect(button('Run to selected')).toBeDisabled();
    expect(button('Run to selected')).toHaveAttribute('title', 'Select a step or a sub-recipe in the diagram first');
    act(() => useView.getState().select({ kind: 'input', id: 'in:n' }));
    expect(button('Run to selected')).toBeDisabled();
    act(() => useView.getState().select({ kind: 'include', id: 'inc:sub/chain.yaml' }));
    expect(button('Run to selected')).toBeEnabled();
    expect(button('Run selected')).toBeDisabled();
    expect(button('Run selected')).toHaveAttribute('title', 'Select a step in the diagram first');
    act(() => useView.getState().select({ kind: 'step', id: 'seed' }));
    expect(button('Run to selected')).toBeEnabled();
    expect(button('Run selected')).toBeEnabled();
  });

  it('disables the run buttons during a run and enables Cancel', async () => {
    openSession();
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run all')).toBeEnabled());
    act(() => useRunStore.getState().begin('r1'));
    expect(button('Run all')).toBeDisabled();
    expect(button('Force ▾')).toBeDisabled();
    expect(button('Cancel')).toBeEnabled();
  });

  it('starts a run to the selected step and follows it', async () => {
    const user = userEvent.setup();
    const fetchMock = openSession(withSubGraph, { [RUNS]: runRecord('r7', { mode: 'to' }) });
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run to selected')).toBeEnabled());
    await user.click(button('Run to selected'));
    await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r7'));
    expect(bodies(fetchMock, RUNS)).toEqual([{ mode: 'to', targets: ['seed'], force: false, force_steps: [] }]);
  });

  it('runs through a selected sub-recipe to its exit steps, with no hint', async () => {
    const user = userEvent.setup();
    const fetchMock = openSession(withSubGraph, { [RUNS]: runRecord('r9', { mode: 'to' }) });
    useView.getState().select({ kind: 'include', id: 'inc:sub/chain.yaml' });
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run to selected')).toBeEnabled());
    await user.click(button('Run to selected'));
    await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r9'));
    expect(bodies(fetchMock, RUNS)).toEqual([{ mode: 'to', targets: ['leaf_add'], force: false, force_steps: [] }]);
    // leaf_add is no checkpoint, so as the target step it would get the FR-57 hint.
    expect(useRunStore.getState().hint).toBeNull();
  });

  it('does nothing for Run selected only or Force rerun selected on a sub-recipe', async () => {
    const fetchMock = openSession(withSubGraph, { [RUNS]: runRecord('r9', {}) });
    useView.getState().select({ kind: 'include', id: 'inc:sub/chain.yaml' });
    function Exposed() {
      controls = useRunControls();
      return null;
    }
    renderWithClient(<Exposed />);
    await waitFor(() => expect(controls?.canRunSelected).toBe(true));
    expect(controls?.canRunStep).toBe(false);
    act(() => {
      controls?.runSelectedOnly();
      controls?.forceSelected();
    });
    expect(bodies(fetchMock, RUNS)).toEqual([]);
  });

  it('previews a run through a selected sub-recipe', async () => {
    const fetchMock = openSession();
    useView.getState().select({ kind: 'include', id: 'inc:sub/chain.yaml' });
    function Probe() {
      const intent = usePreviewIntent();
      useCurrentPreview();
      return <div data-testid="intent">{JSON.stringify(intent)}</div>;
    }
    renderWithClient(<Probe />);
    await waitFor(() =>
      expect(JSON.parse(screen.getByTestId('intent').textContent!)).toMatchObject({
        mode: 'to',
        targets: ['leaf_add'],
        include: 'inc:sub/chain.yaml',
      }),
    );
    const urls = () => fetchMock.mock.calls.map(([url]) => String(url));
    await waitFor(() => expect(urls()).toContain(`/api/sessions/${SID}/preview?targets=leaf_add`));
  });

  it('keeps the preview of the run started, and none for an intent not previewed', async () => {
    const user = userEvent.setup();
    let next = 0;
    openSession(withSubGraph, { [RUNS]: () => runRecord(`r${(next += 1)}`, {}) });
    useView.getState().select({ kind: 'step', id: 'seed' });
    const { client } = renderWithClient(<Harness />);
    const preview = { recipe: 'client_with_sub', steps: [] } as unknown as Preview;
    client.setQueryData(queryKeys.preview(SID, ['seed'], false, false, []), preview);
    await waitFor(() => expect(button('Run to selected')).toBeEnabled());
    await user.click(button('Run to selected'));
    await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r1'));
    expect(useRunStore.getState().previews).toEqual({ r1: preview });

    act(() => useRunStore.getState().apply('r1', [{ seq: 1, time: 1, kind: 'run_finished', run_id: 'x', data: { status: 'completed' } }]));
    await waitFor(() => expect(button('Run all')).toBeEnabled());
    await user.click(button('Run all'));
    await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r2'));
    expect(useRunStore.getState().previews).toEqual({ r1: preview });
  });

  it('forces the selected step from the Force menu', async () => {
    const user = userEvent.setup();
    const fetchMock = openSession(withSubGraph, { [RUNS]: runRecord('r8', {}) });
    useView.getState().select({ kind: 'step', id: 'total' });
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Force ▾')).toBeEnabled());
    await user.click(button('Force ▾'));
    await user.click(screen.getByRole('menuitem', { name: 'Force Rerun Selected' }));
    await waitFor(() => expect(bodies(fetchMock, RUNS)).toHaveLength(1));
    expect(bodies(fetchMock, RUNS)[0]).toEqual({ mode: 'to', targets: ['total'], force: false, force_steps: ['total'] });
  });

  it('offers a run to the step when Run selected finds parents not cached', async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    let calls = 0;
    const graph = structuredClone(withSubGraph);
    const seed = graph.nodes.find((node) => node.id === 'seed');
    if (seed?.kind === 'step') seed.checkpoint = 'always';
    const fetchMock = openSession(graph, {
      [RUNS]: () => {
        calls += 1;
        if (calls > 1) return runRecord('r2', { mode: 'to' });
        return new Response(
          JSON.stringify({
            error: 'UpstreamNotCachedError',
            message: 'not cached',
            details: { targets: ['total'], missing: ['seed', 'leaf_add'] },
          }),
          { status: 409 },
        );
      },
    });
    useView.getState().select({ kind: 'step', id: 'total' });
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run selected')).toBeEnabled());
    await user.click(button('Run selected'));
    await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r2'));
    expect(confirm).toHaveBeenCalledWith(
      'Parents of total are not cached: seed; leaf_add, which is memory only (not a checkpoint in the recipe) ' +
        'and can never be cached until the recipe checkpoints it. Run to total instead?',
    );
    expect(bodies(fetchMock, RUNS)).toEqual([
      { mode: 'only', targets: ['total'], force: false, force_steps: [] },
      { mode: 'to', targets: ['total'], force: false, force_steps: [] },
    ]);
  });

  it('sends no second start while one is pending', async () => {
    const user = userEvent.setup();
    let release: (value: unknown) => void = () => {};
    const fetchMock = openSession(withSubGraph, {
      [RUNS]: () => new Promise((resolve) => (release = resolve)),
    });
    let controls: RunControls = NO_RUN_CONTROLS;
    function Exposed() {
      controls = useRunControls();
      return <Toolbar run={controls} onFit={() => {}} onToggleDirection={() => {}} />;
    }
    renderWithClient(<Exposed />);
    await waitFor(() => expect(button('Run all')).toBeEnabled());
    // Two starts in one moment, as a play click and Ctrl+Enter together.
    act(() => {
      controls.runAll();
      controls.runToHere('seed');
    });
    await user.click(button('Run all'));
    expect(bodies(fetchMock, RUNS)).toHaveLength(1);
    act(() => release(runRecord('r1', {})));
    await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r1'));
    expect(useRunStore.getState().error).toBeNull();
    expect(bodies(fetchMock, RUNS)).toHaveLength(1);
  });

  it('shows why a run did not start', async () => {
    const user = userEvent.setup();
    openSession(withSubGraph, {
      [RUNS]: new Response(JSON.stringify({ error: 'ActiveRunExists', message: 'A run is active.' }), { status: 409 }),
    });
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run all')).toBeEnabled());
    await user.click(button('Run all'));
    await waitFor(() => expect(useRunStore.getState().error).toBe('Run not started: A run is active.'));
  });

  it('cancels after confirmation and shows cancelling', async () => {
    const user = userEvent.setup();
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const fetchMock = openSession(withSubGraph, { 'POST /api/runs/r1/cancel': runRecord('r1', { status: 'running' }) });
    renderWithClient(<Harness />);
    act(() => useRunStore.getState().begin('r1'));
    await user.click(button('Cancel'));
    expect(useRunStore.getState().cancelling).toBe('r1');
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/runs/r1/cancel' && init?.method === 'POST')).toBe(
        true,
      ),
    );
    await waitFor(() =>
      expect(useRunStore.getState().current?.logs.map((entry) => entry.text)).toEqual([
        'run ... cancel requested: stops after the running step',
      ]),
    );
  });

  it('turns Cancel into Stop now while cancelling, which stops the run after confirmation', async () => {
    const user = userEvent.setup();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    const fetchMock = openSession(withSubGraph, {
      'POST /api/runs/r1/cancel': runRecord('r1', { status: 'running' }),
      'POST /api/runs/r1/stop': runRecord('r1', { status: 'running', killed: true }),
    });
    renderWithClient(<Harness />);
    act(() => useRunStore.getState().begin('r1'));
    expect(screen.queryByRole('button', { name: 'Stop now' })).toBeNull();
    await user.click(button('Cancel'));
    const stop = await screen.findByRole('button', { name: 'Stop now' });
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
    expect(stop).toBeEnabled();

    confirm.mockReturnValue(false);
    await user.click(stop);
    expect(confirm).toHaveBeenLastCalledWith(STOP_NOW_CONFIRM);
    const stops = () => fetchMock.mock.calls.filter(([url, init]) => url === '/api/runs/r1/stop' && init?.method === 'POST');
    expect(stops()).toHaveLength(0);

    confirm.mockReturnValue(true);
    await user.click(stop);
    await waitFor(() => expect(stops()).toHaveLength(1));
    await waitFor(() =>
      expect(useRunStore.getState().current?.logs.map((entry) => entry.text)).toContain('run ... stop now requested'),
    );

    act(() => useRunStore.getState().apply('r1', [{ seq: 1, time: 1, kind: 'run_finished', run_id: 'x', data: { status: 'cancelled' } }]));
    await waitFor(() => expect(button('Cancel')).toBeDisabled());
  });
});

describe('RunHint', () => {
  function checkpointedTotal(): Graph {
    const graph = structuredClone(withSubGraph);
    const total = graph.nodes.find((node) => node.id === 'total');
    if (total?.kind === 'step') total.checkpoint = 'always';
    return graph;
  }

  it('offers the nearest checkpoint after a run to a step the recipe does not checkpoint', async () => {
    const user = userEvent.setup();
    let calls = 0;
    const fetchMock = openSession(checkpointedTotal(), {
      [RUNS]: () => runRecord(`r${++calls}`, {}),
      'POST /api/runs/r1/cancel': runRecord('r1', {}),
    });
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run to selected')).toBeEnabled());
    await user.click(button('Run to selected'));
    const hint = await screen.findByText(/is not a checkpoint in the recipe/);
    expect(hint).toHaveTextContent('seed');
    expect(hint).toHaveTextContent("It is saved now because it is this run's target");
    expect(hint).not.toHaveTextContent('for this run only');
    await user.click(screen.getByRole('button', { name: /instead \(3 more steps\)/ }));
    expect(useRunStore.getState().queued).toMatchObject({ mode: 'to', targets: ['total'] });
    expect(useRunStore.getState().cancelling).toBe('r1');
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url, init]) => url === '/api/runs/r1/cancel' && init?.method === 'POST')).toBe(
        true,
      ),
    );
    expect(screen.queryByText(/is not a checkpoint in the recipe/)).toBeNull();
  });

  it('is not shown for a run the echogram pane waits on, which still hears its id', async () => {
    openSession(checkpointedTotal(), { [RUNS]: runRecord('r1', {}) });
    renderWithClient(<ControlsProbe />);
    await waitFor(() => expect(controls?.canRun).toBe(true));
    const started = vi.fn();
    act(() => controls!.runToHere('seed', started));
    await waitFor(() => expect(started).toHaveBeenCalledWith('r1'));
    expect(useRunStore.getState().hint).toBeNull();
    expect(screen.queryByText(/is not a checkpoint in the recipe/)).toBeNull();
  });

  it('does not claim a target is saved when its start preview does not pin it', async () => {
    const user = userEvent.setup();
    const graph = checkpointedTotal();
    const seed = graph.nodes.find((node) => node.id === 'seed');
    if (seed?.kind === 'step') seed.checkpoint = 'never';
    openSession(graph, { [RUNS]: runRecord('r1', {}) });
    useView.getState().select({ kind: 'step', id: 'seed' });
    const { client } = renderWithClient(<Harness />);
    const preview = {
      recipe: 'client_with_sub',
      steps: [{ step_id: 'seed', pinned: false, checkpoint: 'memory-only', held_by: [] }],
    } as unknown as Preview;
    client.setQueryData(queryKeys.preview(SID, ['seed'], false, false, []), preview);
    await waitFor(() => expect(button('Run to selected')).toBeEnabled());
    await user.click(button('Run to selected'));
    const hint = await screen.findByText(/is not a checkpoint in the recipe/);
    expect(hint).toHaveTextContent('This run does not save it (checkpoint: never in the recipe).');
    expect(hint).not.toHaveTextContent('It is saved now');
  });

  it('is not shown for a checkpointed target, nor once turned off', async () => {
    const user = userEvent.setup();
    openSession(checkpointedTotal(), { [RUNS]: runRecord('r1', {}) });
    useView.getState().select({ kind: 'step', id: 'total' });
    renderWithClient(<Harness />);
    await waitFor(() => expect(button('Run to selected')).toBeEnabled());
    await user.click(button('Run to selected'));
    await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r1'));
    expect(screen.queryByText(/is not a checkpoint in the recipe/)).toBeNull();

    act(() => useRunStore.getState().setHint({ step: 'seed', alternative: null }));
    await user.click(screen.getByRole('button', { name: "Don't show again" }));
    expect(window.localStorage.getItem(HINT_KEY)).toBe('off');
    expect(useRunStore.getState().hint).toBeNull();
  });

  it('makes the step a checkpoint from its third button, then hides', async () => {
    const user = userEvent.setup();
    const session = editableSession();
    const fetchMock = mockApi({
      [`GET /api/sessions/${SID}`]: session,
      [EDITS]: { session, changed: true, changed_hashes: [], entries: [] },
    });
    useSessionStore.getState().setSid(SID);
    renderWithClient(<RunHint />);
    act(() => useRunStore.getState().setHint({ step: 'seed', alternative: null }));
    await user.click(await screen.findByRole('button', { name: 'Make this a checkpoint' }));
    await waitFor(() => expect(useRunStore.getState().hint).toBeNull());
    expect(bodies(fetchMock, EDITS)).toEqual([
      { operations: [{ kind: 'set_checkpoint', step: 'seed', value: 'always', position: null }], version: 0 },
    ]);
  });

  it('keeps the hint when the edit fails, and offers no checkpoint in a read only session', async () => {
    useEditingStore.getState().reset();
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: editableSession(),
      [EDITS]: apiErrorResponse(422, 'EditRejected', 'refused', { problems: ['refused'] }),
    });
    useSessionStore.getState().setSid(SID);
    const { unmount } = renderWithClient(<RunHint />);
    act(() => useRunStore.getState().setHint({ step: 'seed', alternative: null }));
    await user.click(await screen.findByRole('button', { name: 'Make this a checkpoint' }));
    await waitFor(() => expect(useEditingStore.getState().error).toBe('refused'));
    expect(useRunStore.getState().hint?.step).toBe('seed');
    unmount();

    openSession();
    renderWithClient(<RunHint />);
    act(() => useRunStore.getState().setHint({ step: 'seed', alternative: null }));
    await screen.findByText(/is not a checkpoint in the recipe/);
    expect(screen.queryByRole('button', { name: 'Make this a checkpoint' })).toBeNull();
  });
});

describe('upstreamMessage', () => {
  it('names a parent the recipe checkpoints plainly and a memory only one with the note', () => {
    const graph = structuredClone(withSubGraph);
    const seed = graph.nodes.find((node) => node.id === 'seed');
    if (seed?.kind === 'step') seed.checkpoint = 'always';
    const error = (missing: string[]) =>
      new ApiError(409, 'UpstreamNotCachedError', 'not cached', { targets: ['total'], missing });
    expect(upstreamMessage(error(['seed']), 'total', graph)).toBe(
      'Parents of total are not cached: seed. Run to total instead?',
    );
    expect(upstreamMessage(error(['leaf_add']), 'total', graph)).toBe(
      'Parents of total are not cached: leaf_add, which is memory only (not a checkpoint in the recipe) and can ' +
        'never be cached until the recipe checkpoints it. Run to total instead?',
    );
    expect(upstreamMessage(error(['leaf_add']), 'total')).toBe(
      'Parents of total are not cached: leaf_add. Run to total instead?',
    );
    expect(upstreamMessage(new ApiError(409, 'StaleVersion', 'stale'), 'total', graph)).toBeNull();
  });
});
