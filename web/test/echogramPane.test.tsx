// jsdom has no WebGPU. createGpuContext is wrapped so a test can give it a
// fake device, and EchogramPanel is replaced by a stand-in recording its sources.
import { act, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EchogramPane, SECTIONS_KEY } from '../src/echogram/EchogramPane';
import { resetGpuContext } from '../src/echogram/gpu';
import { restoredLayer } from '../src/echogram/layers';
import { NEEDS_WEBGPU, useEchogramStore } from '../src/echogram/store';
import { EchogramWatcher } from '../src/echogram/useEchogramStaleness';
import type { RunStartedData } from '../src/runs/events';
import { initialRunState, type RunState } from '../src/runs/reducer';
import { useRunStore } from '../src/runs/store';
import { RunControlsContext, NO_RUN_CONTROLS } from '../src/runs/useRunControls';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import diveMaskSparse from './fixtures/artifacts.dive_mask_sparse.json';
import svMapped from './fixtures/artifacts.sv_mapped.json';
import { makeSession, mockApi, renderWithClient } from './helpers';

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
    act(() => useEchogramStore.getState().markOutOfDate({ sv: 'moved' }, null));
    expect(within(list).getByText('out of date')).toBeInTheDocument();
    const refresh = vi.spyOn(useEchogramStore.getState(), 'refresh');
    await user.click(within(list).getByRole('button', { name: 'Refresh' }));
    expect(refresh).toHaveBeenCalledWith('sv/ds_Sv/Sv');

    await user.click(within(list).getByRole('button', { name: 'Remove sv' }));
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Echogram layers' })).toBeNull());
    await waitFor(() => expect(panels[0].sources).toEqual([]));
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
    expect(screen.getByText(/dive_mask_sparse has not been saved/)).toBeInTheDocument();
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

  it('marks a layer whose step a finished run computed, and draws the step a run was started to save', async () => {
    const saved = structuredClone(svMapped);
    saved.step_id = 'dive_mask_sparse';
    saved.echogram.resolution.step = 'dive_mask_sparse';
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/dive_mask_sparse/artifacts`]: saved,
    });
    useEchogramStore.setState({
      layers: [restoredLayer({ step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 })],
    });
    renderWithClient(<EchogramWatcher />);

    act(() => useRunStore.setState({ last: finished('r1', 'sv') }));
    await waitFor(() => expect(useEchogramStore.getState().layers[0].outOfDate).toBe(true));

    act(() => useEchogramStore.getState().runStarted('dive_mask_sparse', 'r2'));
    act(() => useRunStore.setState({ last: finished('r2', 'dive_mask_sparse') }));
    await waitFor(() => expect(useEchogramStore.getState().layers.map((layer) => layer.step)).toContain('dive_mask_sparse'));
    expect(useEchogramStore.getState().pendingRun).toBeNull();
  });
});
