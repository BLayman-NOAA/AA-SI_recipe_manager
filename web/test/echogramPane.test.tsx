// jsdom has no WebGPU. createGpuContext is wrapped so a test can give it a
// fake device, and EchogramPanel is replaced by a stand-in recording its sources.
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { DockviewApi } from 'dockview-react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Preview } from '../src/api/models';
import { EchogramPane, SECTIONS_KEY, SOURCES_KEY } from '../src/echogram/EchogramPane';
import { resetGpuContext } from '../src/echogram/gpu';
import { restoredLayer } from '../src/echogram/layers';
import { NEEDS_WEBGPU, useEchogramStore } from '../src/echogram/store';
import { EchogramWatcher } from '../src/echogram/useEchogramStaleness';
import type { RunStartedData } from '../src/runs/events';
import { initialRunState, type RunState } from '../src/runs/reducer';
import { useRunStore } from '../src/runs/store';
import { useDock } from '../src/shell/dock';
import { useCurrentPreview } from '../src/runs/usePreviewIntent';
import { RunControlsContext, NO_RUN_CONTROLS } from '../src/runs/useRunControls';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import diveMaskSparse from './fixtures/artifacts.dive_mask_sparse.json';
import embed from './fixtures/artifacts.embed_results.json';
import svMapped from './fixtures/artifacts.sv_mapped.json';
import { editableSession, makePreview, makeSession, mockApi, previewStep, renderWithClient } from './helpers';

/** A preview holding sv at a hash, by tier, or for some of its three files. */
function svPreview(hash: string, held: string[] = [], instancesHeld: Record<string, number> = {}): Preview {
  return makePreview([
    previewStep('sv', { step_hash: hash, held_by: held, fanned_out: true, instances: 3, instances_held: instancesHeld }),
  ]);
}

const gpu = vi.hoisted(() => ({ fake: false }));
const panels = vi.hoisted(() => [] as { options: Record<string, unknown>; sources: string[]; destroyed: boolean }[]);

vi.mock('aa-si-echogram-gl', async (original) => {
  const library = await original<typeof import('aa-si-echogram-gl')>();
  class FakePanel {
    record = { options: {} as Record<string, unknown>, sources: [] as string[], destroyed: false };
    constructor(options: Record<string, unknown>) {
      this.record.options = options;
      panels.push(this.record);
    }
    get sourceIds() {
      return [...this.record.sources];
    }
    async addSource(id: string) {
      this.record.sources.push(id);
    }
    async replaceSource() {}
    async removeSource(id: string) {
      this.record.sources = this.record.sources.filter((source) => source !== id);
    }
    async retry() {}
    get settingsObject() {
      return undefined;
    }
    async applySettings() {}
    destroy() {
      this.record.destroyed = true;
    }
  }
  return {
    ...library,
    EchogramPanel: FakePanel,
    createGpuContext: () => (gpu.fake ? Promise.resolve({}) : library.createGpuContext()),
  };
});

const SID = '20261013-153012-a1b2c3';

function renderPane(controls = NO_RUN_CONTROLS) {
  return renderWithClient(
    <RunControlsContext.Provider value={controls}>
      <EchogramPane />
    </RunControlsContext.Provider>,
  );
}

beforeEach(() => {
  resetGpuContext();
  panels.length = 0;
  gpu.fake = false;
  useEchogramStore.setState({ sid: null, gpu: 'unknown', layers: [], panel: null, settings: null, choice: null, pendingRun: null, message: null, busy: null });
  useEchogramStore.getState().resetFor(SID);
  useSessionStore.getState().setSid(SID);
  useView.getState().resetScope();
});

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().setSid(null);
});

describe('the echogram pane without WebGPU', () => {
  it('says the pane needs WebGPU and what to do, and creates no panel', async () => {
    mockApi({ [`GET /api/sessions/${SID}`]: makeSession() });
    renderPane();
    const alert = await screen.findByRole('alert');
    expect(within(alert).getByRole('heading')).toHaveTextContent('The echogram pane needs WebGPU');
    expect(alert).toHaveTextContent('navigator.gpu is undefined');
    // The advice repeats the message without a secure context, so it is left out.
    expect(alert.querySelectorAll('p')).toHaveLength(1);
    expect(panels).toHaveLength(0);
    expect(screen.queryByText(/Click the eye icon/)).toBeNull();

    // An eye click adds no row that could never draw, and says why.
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    expect(screen.getByText(NEEDS_WEBGPU)).toBeInTheDocument();
    expect(screen.queryByRole('list', { name: 'Echogram layers' })).toBeNull();
  });
});

describe('the echogram pane with a device', () => {
  beforeEach(() => {
    gpu.fake = true;
  });

  it('creates the panel, shows the empty state, and lists a layer after show', async () => {
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
    });
    renderPane();
    expect(await screen.findByText('Click the eye icon on a step to draw its output here.')).toBeInTheDocument();
    await waitFor(() => expect(panels).toHaveLength(1));
    expect(panels[0].options.storageKey).toBe(SECTIONS_KEY);
    expect(typeof panels[0].options.spawnWorker).toBe('function');

    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    const list = await screen.findByRole('list', { name: 'Echogram layers' });
    await waitFor(() => expect(list).toHaveTextContent('ds_Sv: Sv, 3 datasets'));
    expect(panels[0].sources).toEqual(['sv/ds_Sv/Sv']);
    expect(screen.queryByText(/Click the eye icon/)).toBeNull();
  });

  it('selects the step from a row, and refreshes and removes through the store', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
    });
    renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    const list = await screen.findByRole('list', { name: 'Echogram layers' });

    await user.click(within(list).getByRole('button', { name: /^sv/ }));
    expect(useView.getState().selected).toEqual({ kind: 'step', id: 'sv' });

    expect(within(list).queryByRole('button', { name: 'Refresh' })).toBeNull();
    act(() => useEchogramStore.getState().track(svPreview('moved', ['user']), null));
    expect(within(list).getByText('data available: refresh to display')).toBeInTheDocument();
    const refresh = vi.spyOn(useEchogramStore.getState(), 'refresh');
    await user.click(within(list).getByRole('button', { name: 'Refresh' }));
    expect(refresh).toHaveBeenCalledWith('sv/ds_Sv/Sv');

    await user.click(within(list).getByRole('button', { name: 'Remove sv' }));
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Echogram layers' })).toBeNull());
    await waitFor(() => expect(panels[0].sources).toEqual([]));
  });

  it('offers a run on a row whose new settings are not computed, or computed for some files', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
    });
    const runToHere = vi.fn((_step: string, onStarted?: (runId: string) => void) => onStarted?.('r8'));
    renderPane({ ...NO_RUN_CONTROLS, canRun: true, runToHere });
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    const list = await screen.findByRole('list', { name: 'Echogram layers' });

    act(() => useEchogramStore.getState().track(svPreview('edited'), null));
    expect(within(list).getByText('not computed yet: showing the previous settings')).toBeInTheDocument();
    expect(within(list).queryByRole('button', { name: 'Refresh' })).toBeNull();
    act(() => useEchogramStore.getState().track(svPreview('edited', [], { user: 2 }), null));
    expect(within(list).getByText('partly computed: 2 of 3 files')).toBeInTheDocument();

    await user.click(within(list).getByRole('button', { name: 'Run to sv' }));
    expect(runToHere).toHaveBeenCalledWith('sv', expect.any(Function));
    expect(useEchogramStore.getState().pendingRun).toEqual({ step: 'sv', runId: 'r8', layer: 'sv/ds_Sv/Sv' });

    act(() => useEchogramStore.getState().track(svPreview(svMapped.step_hash, ['user']), null));
    expect(within(list).queryByText(/not computed|partly computed|data available/)).toBeNull();
    expect(within(list).queryByRole('button', { name: 'Run to sv' })).toBeNull();
  });

  it('makes a step that is not a checkpoint one from the run offer', async () => {
    const user = userEvent.setup();
    const session = editableSession();
    const fetchMock = mockApi({
      [`GET /api/sessions/${SID}`]: session,
      [`GET /api/sessions/${SID}/steps/seed/artifacts`]: { ...diveMaskSparse, step_id: 'seed' },
      [`POST /api/sessions/${SID}/edits`]: { session, changed: true, changed_hashes: [], entries: [] },
    });
    renderPane({ ...NO_RUN_CONTROLS, canRun: true });
    act(() => useEchogramStore.getState().track(makePreview([previewStep('seed', { pinned: true, checkpoint: 'will-save' })]), null));
    await act(() => useEchogramStore.getState().show(SID, 'seed'));
    expect(screen.getByText(/seed isn't a checkpoint, so only a run to it saves it/)).toBeInTheDocument();
    await user.click(await screen.findByRole('button', { name: 'Make it a checkpoint' }));
    await waitFor(() => expect(screen.getByText('seed has no saved result yet. Run to seed to draw it.')).toBeInTheDocument());
    const posted = fetchMock.mock.calls.filter(([, init]) => init?.method === 'POST');
    expect(JSON.parse(String(posted[0][1]?.body)).operations).toEqual([
      { kind: 'set_checkpoint', step: 'seed', value: 'always', position: null },
    ]);
    expect(screen.queryByRole('button', { name: 'Make it a checkpoint' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Run to seed and show' })).toBeEnabled();
  });

  it('offers a run for a step that is not saved, and records the run it starts', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/dive_mask_sparse/artifacts`]: diveMaskSparse,
    });
    const runToHere = vi.fn((_step: string, onStarted?: (runId: string) => void) => onStarted?.('r7'));
    renderPane({ ...NO_RUN_CONTROLS, canRun: true, runToHere });
    await act(() => useEchogramStore.getState().show(SID, 'dive_mask_sparse'));
    expect(screen.getByText(/dive_mask_sparse has no saved result yet/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Run to dive_mask_sparse and show' }));
    expect(runToHere).toHaveBeenCalledWith('dive_mask_sparse', expect.any(Function));
    expect(useEchogramStore.getState().pendingRun).toEqual({ step: 'dive_mask_sparse', runId: 'r7' });
    expect(screen.getByText(/Running to dive_mask_sparse/)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /and show/ })).toBeNull();
  });

  it('disables the run offer while a run cannot start', async () => {
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/dive_mask_sparse/artifacts`]: diveMaskSparse,
    });
    renderPane();
    await act(() => useEchogramStore.getState().show(SID, 'dive_mask_sparse'));
    expect(screen.getByRole('button', { name: 'Run to dive_mask_sparse and show' })).toBeDisabled();
  });

  it('collapses the sources, remembers it, and keeps the messages in view', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
      [`GET /api/sessions/${SID}/steps/dive_mask_sparse/artifacts`]: diveMaskSparse,
    });
    const view = renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    expect(screen.queryByText(/^Sources/)).toBeNull();
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    const summary = await screen.findByText('Sources (1)');
    const list = screen.getByRole('list', { name: 'Echogram layers' });
    expect(list).toBeVisible();

    await user.click(summary);
    await waitFor(() => expect(list).not.toBeVisible());
    expect(window.localStorage.getItem(SOURCES_KEY)).toBe('false');
    await act(() => useEchogramStore.getState().show(SID, 'dive_mask_sparse'));
    expect(screen.getByText(/dive_mask_sparse has no saved result yet/)).toBeVisible();
    expect(screen.getByRole('button', { name: 'Run to dive_mask_sparse and show' })).toBeVisible();

    view.unmount();
    renderPane();
    expect(await screen.findByText('Sources (1)')).toBeInTheDocument();
    expect(screen.getByRole('list', { name: 'Echogram layers' })).not.toBeVisible();
    await user.click(screen.getByText('Sources (1)'));
    await waitFor(() => expect(screen.getByRole('list', { name: 'Echogram layers' })).toBeVisible());
    expect(window.localStorage.getItem(SOURCES_KEY)).toBe('true');
  });

  it('opens the sources for a chooser without remembering them open', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
      [`GET /api/sessions/${SID}/steps/embed_results/artifacts`]: embed,
    });
    window.localStorage.setItem(SOURCES_KEY, 'false');
    try {
      renderPane();
      await waitFor(() => expect(panels).toHaveLength(1));
      await act(() => useEchogramStore.getState().show(SID, 'sv'));
      const list = await screen.findByRole('list', { name: 'Echogram layers' });
      expect(list).not.toBeVisible();

      await act(() => useEchogramStore.getState().show(SID, 'embed_results'));
      await waitFor(() => expect(list).toBeVisible());
      // The toggle event that follows the open comes in a task of its own.
      await act(() => new Promise((resolve) => setTimeout(resolve, 0)));
      expect(window.localStorage.getItem(SOURCES_KEY)).toBe('false');

      // The user's own toggles are still remembered.
      await user.click(screen.getByText('Sources (1)'));
      await waitFor(() => expect(list).not.toBeVisible());
      expect(window.localStorage.getItem(SOURCES_KEY)).toBe('false');
      await user.click(screen.getByText('Sources (1)'));
      await waitFor(() => expect(list).toBeVisible());
      expect(window.localStorage.getItem(SOURCES_KEY)).toBe('true');
    } finally {
      window.localStorage.removeItem(SOURCES_KEY);
    }
  });

  it('makes the panel again, once, when its host moves to another document', async () => {
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
    });
    // Dockview's layout change event, which follows every move, popout and tab change.
    const listeners = new Set<() => void>();
    const layoutChanged = (times = 1) =>
      act(async () => {
        for (let fired = 0; fired < times; fired++) listeners.forEach((listener) => listener());
        await new Promise((resolve) => setTimeout(resolve, 0));
      });
    const api = {
      onDidLayoutChange: (listener: () => void) => {
        listeners.add(listener);
        return { dispose: () => listeners.delete(listener) };
      },
    };
    useDock.setState({ api: api as unknown as DockviewApi });
    const view = renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    await waitFor(() => expect(panels[0].sources).toEqual(['sv/ds_Sv/Sv']));
    const host = view.container.querySelector('.rc-echogram-host')!;
    expect(panels[0].options.container).toBe(host);
    await layoutChanged();
    expect(panels).toHaveLength(1);

    // A popout window, as dockview moves a pane into one: an iframe's document.
    // A move reports more than one layout change and makes one panel.
    const frame = document.createElement('iframe');
    document.body.append(frame);
    try {
      frame.contentDocument!.body.append(view.container);
      await layoutChanged(2);
      await waitFor(() => expect(panels).toHaveLength(2));
      expect(panels[0].destroyed).toBe(true);
      expect(panels[1].options.container).toBe(host);
      expect(panels[1].options.storageKey).toBe(SECTIONS_KEY);
      await waitFor(() => expect(panels[1].sources).toEqual(['sv/ds_Sv/Sv']));
      await layoutChanged();
      expect(panels).toHaveLength(2);

      // And again on the way back.
      document.body.append(view.container);
      await layoutChanged(2);
      await waitFor(() => expect(panels).toHaveLength(3));
      expect(panels[1].destroyed).toBe(true);
      await layoutChanged();
      expect(panels).toHaveLength(3);
    } finally {
      document.body.append(view.container);
      frame.remove();
      useDock.setState({ api: null });
    }
  });

  it('destroys the panel and keeps the layers when the pane closes', async () => {
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
    });
    const view = renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    view.unmount();
    expect(panels[0].destroyed).toBe(true);
    expect(useEchogramStore.getState().panel).toBeNull();
    expect(useEchogramStore.getState().layers).toHaveLength(1);
  });
});

describe('the staleness watcher', () => {
  function finished(runId: string, step: string): RunState {
    const instances = { count: null, done: 0, computed: 0, cached: 0, skipped: 0 };
    return {
      ...initialRunState(runId),
      started: { targets: [step] } as unknown as RunStartedData,
      finishedAt: Date.now() / 1000,
      finished: { status: 'completed', seconds: 1, manifest: null, executed: [], skipped: [], pruned: [], untargeted: [], error_type: null, message: null },
      steps: { [step]: { status: 'computed', tier: null, hash: null, planned: null, index: 0, total: 0, instances, seconds: null, error: null, chain: null } },
    };
  }

  afterEach(() => {
    useRunStore.getState().resetFor(null);
  });

  it('marks a layer whose step a finished run saved, and draws the step a run was started to save', async () => {
    const saved = structuredClone(svMapped);
    saved.step_id = 'dive_mask_sparse';
    saved.echogram.resolution.step = 'dive_mask_sparse';
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/preview`]: svPreview('h', ['user']),
      [`GET /api/sessions/${SID}/steps/dive_mask_sparse/artifacts`]: saved,
    });
    useEchogramStore.setState({
      layers: [restoredLayer({ step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 })],
    });
    renderWithClient(<EchogramWatcher />);
    await waitFor(() => expect(useEchogramStore.getState().layers[0].freshness).toEqual({ state: 'current' }));

    act(() => useRunStore.setState({ last: finished('r1', 'sv') }));
    await waitFor(() => expect(useEchogramStore.getState().layers[0].freshness).toEqual({ state: 'available' }));

    act(() => {
      useRunStore.getState().begin('r2');
      useEchogramStore.getState().runStarted('dive_mask_sparse', 'r2');
    });
    act(() => useRunStore.setState({ current: null, last: finished('r2', 'dive_mask_sparse') }));
    await waitFor(() => expect(useEchogramStore.getState().layers.map((layer) => layer.step)).toContain('dive_mask_sparse'));
    expect(useEchogramStore.getState().pendingRun).toBeNull();
  });

  it('reads freshness from the run all preview, whatever step is selected', async () => {
    const targeted = { ...svPreview('earlier'), mode: 'to', targets: ['upstream'] };
    targeted.steps = [{ ...targeted.steps[0], planned: 'untargeted' }];
    const fetchMock = mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/preview`]: svPreview('earlier', ['user']),
      [`GET /api/sessions/${SID}/preview?targets=upstream`]: targeted,
    });
    useEchogramStore.setState({
      layers: [restoredLayer({ step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 })],
    });
    // The diagram follows the selection, so the targeted preview is fetched as the user selects upstream.
    function SelectionPreview() {
      return <span>{useCurrentPreview().data?.mode ?? 'none'}</span>;
    }
    renderWithClient(
      <>
        <EchogramWatcher />
        <SelectionPreview />
      </>,
    );
    await waitFor(() => expect(useEchogramStore.getState().layers[0].freshness).toEqual({ state: 'available' }));

    act(() => useView.getState().select({ kind: 'step', id: 'upstream' }));
    await screen.findByText('to', undefined, { timeout: 2000 });
    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${SID}/preview?targets=upstream`, expect.anything());
    expect(useEchogramStore.getState().layers[0].freshness).toEqual({ state: 'available' });
  });

  it('lets go of a run to a step it never sees finish', async () => {
    mockApi({ [`GET /api/sessions/${SID}`]: makeSession(), [`GET /api/sessions/${SID}/preview`]: svPreview('h') });
    renderWithClient(<EchogramWatcher />);
    act(() => {
      useRunStore.getState().begin('r1');
      useEchogramStore.getState().runStarted('dive_mask_sparse', 'r1');
    });
    expect(useEchogramStore.getState().pendingRun).toEqual({ step: 'dive_mask_sparse', runId: 'r1' });

    // The server restarted: the run store follows no run any more, and no run_finished comes.
    act(() => useRunStore.getState().resetFor(null));
    await waitFor(() => expect(useEchogramStore.getState().pendingRun).toBeNull());
  });
});
