// jsdom has no WebGPU. createGpuContext is wrapped so a test can give it a
// fake device, and EchogramPanel is replaced by a stand-in recording its sources.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ViewSettings } from 'aa-si-echogram-gl';
import type { DockviewApi } from 'dockview-react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Preview } from '../src/api/models';
import { EchogramPane, SECTIONS_KEY, SOURCES_KEY } from '../src/echogram/EchogramPane';
import { resetGpuContext } from '../src/echogram/gpu';
import { LINE_ACCENT, restoredLayer } from '../src/echogram/layers';
import { NEEDS_WEBGPU, useEchogramStore } from '../src/echogram/store';
import { EchogramWatcher } from '../src/echogram/useEchogramStaleness';
import type { RunStartedData } from '../src/runs/events';
import { initialRunState, type RunState } from '../src/runs/reducer';
import { useRunStore } from '../src/runs/store';
import { useDock } from '../src/shell/dock';
import { useCurrentPreview } from '../src/runs/usePreviewIntent';
import { RunControlsContext, NO_RUN_CONTROLS } from '../src/runs/useRunControls';
import { useSessionStore } from '../src/state/session';
import { useTabs } from '../src/state/tabs';
import { useView } from '../src/state/view';
import diveMaskSparse from './fixtures/artifacts.dive_mask_sparse.json';
import embed from './fixtures/artifacts.embed_results.json';
import svLine from './fixtures/artifacts.sv_line.json';
import svMapped from './fixtures/artifacts.sv_mapped.json';
import {
  editableSession,
  errorResponse,
  makePreview,
  makeSession,
  mockApi,
  previewStep,
  renderWithClient,
} from './helpers';

/** A preview holding sv at a hash, by tier, or for some of its three files. */
function svPreview(hash: string, held: string[] = [], instancesHeld: Record<string, number> = {}): Preview {
  return makePreview([
    previewStep('sv', { step_hash: hash, held_by: held, fanned_out: true, instances: 3, instances_held: instancesHeld }),
  ]);
}

const gpu = vi.hoisted(() => ({ fake: false }));
const panels = vi.hoisted(
  () =>
    [] as {
      options: Record<string, unknown>;
      sources: string[];
      names: Record<string, string | undefined>;
      lines: { id: string; color: string; width: number; visible: boolean }[];
      retries: number;
      settings?: ViewSettings;
      applied: ViewSettings[];
      destroyed: boolean;
    }[],
);

vi.mock('aa-si-echogram-gl', async (original) => {
  const library = await original<typeof import('aa-si-echogram-gl')>();
  class FakePanel {
    record = {
      options: {} as Record<string, unknown>,
      sources: [] as string[],
      names: {} as Record<string, string | undefined>,
      lines: [] as (typeof panels)[number]['lines'],
      retries: 0,
      settings: undefined as ViewSettings | undefined,
      applied: [] as ViewSettings[],
      destroyed: false,
    };
    constructor(options: Record<string, unknown>) {
      this.record.options = options;
      panels.push(this.record);
    }
    get sourceIds() {
      return [...this.record.sources];
    }
    async addSource(id: string, spec?: { name?: string }) {
      this.record.sources.push(id);
      this.record.names[id] = spec?.name;
    }
    async replaceSource() {}
    async removeSource(id: string) {
      this.record.sources = this.record.sources.filter((source) => source !== id);
    }
    async retry() {
      this.record.retries += 1;
    }
    setLines(specs: (typeof panels)[number]['lines']) {
      this.record.lines = specs;
    }
    get settingsObject() {
      return this.record.settings;
    }
    async applySettings(settings: ViewSettings) {
      this.record.applied.push(settings);
    }
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
const SV_ID = `${SID}/sv/ds_Sv/Sv`;

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
  useEchogramStore.setState({ front: null, gpu: 'unknown', layers: [], panel: null, settings: null, choice: null, pendingRun: null, message: null, busy: null, bySession: {}, titles: {}, bare: [] });
  useEchogramStore.getState().activate(SID);
  useTabs.setState({ sids: [SID], pendingSelection: null });
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
    expect(panels[0].sources).toEqual([SV_ID]);
    expect(screen.queryByText(/Click the eye icon/)).toBeNull();
  });

  it('adds every line from the chooser, and draws each as its row sets it', async () => {
    const user = userEvent.setup();
    const mount = svLine.echogram.resolution.instances[0].mount;
    const line = (name: string) => ({
      name,
      longName: null,
      units: 'm',
      verticalReference: 'depth',
      pingTime: [1, 2],
      value: [10, 11],
    });
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/lines/artifacts`]: svLine,
      [`GET /echogram/api/line/${mount}/line_a`]: line('line_a'),
      [`GET /echogram/api/line/${mount}/line_b`]: line('line_b'),
    });
    renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'lines'));
    await user.click(await screen.findByRole('button', { name: 'Add all lines' }));
    const list = await screen.findByRole('list', { name: 'Echogram layers' });
    await waitFor(() => expect(panels[0].lines).toHaveLength(2));
    expect(within(list).getAllByRole('listitem')).toHaveLength(3);
    expect(panels[0].sources).toEqual([`${SID}/lines/ds/Sv`]);
    expect(within(list).getByText('ds: line_a, 1 line')).toBeInTheDocument();

    const colour = within(list).getByLabelText('Colour of lines:line_a');
    expect(colour).toHaveValue(LINE_ACCENT);
    fireEvent.change(colour, { target: { value: '#ff0000' } });
    await user.selectOptions(within(list).getByLabelText('Width of lines:line_a'), '4');
    await user.click(within(list).getByLabelText('Show lines:line_a'));
    const drawn = (id: string) => panels[0].lines.find((spec) => spec.id.startsWith(`${SID}/lines/ds/${id}#`));
    expect(drawn('line_a')).toMatchObject({ color: '#ff0000', width: 4, visible: false });
    expect(drawn('line_b')).toMatchObject({ color: LINE_ACCENT, width: 2, visible: true });
    expect(within(list).getByLabelText('Show lines:line_a')).not.toBeChecked();
    // A value row has no line controls.
    expect(within(list).queryByLabelText('Colour of lines:Sv')).toBeNull();

    await user.click(within(list).getByRole('button', { name: 'Remove lines:line_a' }));
    await waitFor(() => expect(panels[0].lines.map((spec) => spec.id.split('#')[0])).toEqual([`${SID}/lines/ds/line_b`]));
  });

  it('adds three lines over one value from Add all lines, drawing the value once', async () => {
    const user = userEvent.setup();
    const mount = svLine.echogram.resolution.instances[0].mount;
    const three = structuredClone(svLine);
    const variables = three.echogram.resolution.outputs[0].variables;
    variables.push({ ...variables[2], name: 'line_c' });
    const line = (name: string) => ({
      name,
      longName: null,
      units: 'm',
      verticalReference: 'depth',
      pingTime: [1, 2],
      value: [10, 11],
    });
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/lines/artifacts`]: three,
      ...Object.fromEntries(
        ['line_a', 'line_b', 'line_c'].map((name) => [`GET /echogram/api/line/${mount}/${name}`, line(name)]),
      ),
    });
    renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'lines'));
    await user.click(await screen.findByRole('button', { name: 'Add all lines' }));
    await waitFor(() => expect(panels[0].lines).toHaveLength(3));
    expect(panels[0].sources).toEqual([`${SID}/lines/ds/Sv`]);
    expect(panels[0].retries).toBe(0);
    const rows = useEchogramStore.getState().layers;
    expect(rows.map((row) => [row.kind, row.state])).toEqual([
      ['value', 'shown'],
      ['line', 'shown'],
      ['line', 'shown'],
      ['line', 'shown'],
    ]);
  });

  it('offers no Add all lines for a step without several lines', async () => {
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/embed_results/artifacts`]: embed,
    });
    renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'embed_results'));
    expect(await screen.findByRole('button', { name: 'Add' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Add all lines' })).toBeNull();
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
    act(() => useEchogramStore.getState().track(SID, svPreview('moved', ['user']), null));
    expect(within(list).getByText('data available: refresh to display')).toBeInTheDocument();
    const refresh = vi.spyOn(useEchogramStore.getState(), 'refresh');
    await user.click(within(list).getByRole('button', { name: 'Refresh' }));
    expect(refresh).toHaveBeenCalledWith(SV_ID);

    await user.click(within(list).getByRole('button', { name: 'Remove sv' }));
    await waitFor(() => expect(screen.queryByRole('list', { name: 'Echogram layers' })).toBeNull());
    await waitFor(() => expect(panels[0].sources).toEqual([]));
  });

  it('says when a row draws no layer in the panel, and adds one from the row', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
    });
    renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    const list = await screen.findByRole('list', { name: 'Echogram layers' });
    await waitFor(() => expect(list).toHaveTextContent('ds_Sv: Sv, 3 datasets'));
    expect(within(list).queryByRole('button', { name: 'Add layer' })).toBeNull();

    act(() => useEchogramStore.getState().onPanelLayersChange([]));
    expect(list).toHaveTextContent('ds_Sv: Sv, 3 datasets, no layers drawn');
    const settings = { version: 2, layers: [], colormap: 'viridis' } as unknown as ViewSettings;
    panels[0].settings = settings;
    await user.click(within(list).getByRole('button', { name: 'Add layer' }));
    await waitFor(() => expect(panels[0].applied).toHaveLength(1));
    expect(panels[0].applied[0].layers).toEqual([
      { id: `${SV_ID}-layer-1`, source: SV_ID, channel: 0, color: { colormap: 'viridis' } },
    ]);

    act(() => useEchogramStore.getState().onPanelLayersChange(panels[0].applied[0].layers as never));
    expect(list).not.toHaveTextContent('no layers drawn');
    expect(within(list).queryByRole('button', { name: 'Add layer' })).toBeNull();
    expect(panels[0].sources).toEqual([SV_ID]);
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

    act(() => useEchogramStore.getState().track(SID, svPreview('edited'), null));
    expect(within(list).getByText('not computed yet: showing the previous settings')).toBeInTheDocument();
    expect(within(list).queryByRole('button', { name: 'Refresh' })).toBeNull();
    act(() => useEchogramStore.getState().track(SID, svPreview('edited', [], { user: 2 }), null));
    expect(within(list).getByText('partly computed: 2 of 3 files')).toBeInTheDocument();

    await user.click(within(list).getByRole('button', { name: 'Run to sv' }));
    expect(runToHere).toHaveBeenCalledWith('sv', expect.any(Function));
    expect(useEchogramStore.getState().pendingRun).toEqual({ sid: SID, step: 'sv', runId: 'r8', layer: SV_ID });

    act(() => useEchogramStore.getState().track(SID, svPreview(svMapped.step_hash, ['user']), null));
    expect(within(list).queryByText(/not computed|partly computed|data available/)).toBeNull();
    expect(within(list).queryByRole('button', { name: 'Run to sv' })).toBeNull();
  });

  describe('with rows of two sessions', () => {
    const OTHER = 'T';
    const OTHER_ID = `${OTHER}/sv/ds_Sv/Sv`;

    async function showBoth(controls = NO_RUN_CONTROLS) {
      mockApi({
        [`GET /api/sessions/${SID}`]: makeSession(),
        [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
        [`GET /api/sessions/${OTHER}/steps/sv/artifacts`]: svMapped,
      });
      renderPane(controls);
      await waitFor(() => expect(panels).toHaveLength(1));
      await act(() => useEchogramStore.getState().show(OTHER, 'sv'));
      await act(() => useEchogramStore.getState().show(SID, 'sv'));
      act(() => useEchogramStore.getState().setTitles({ [SID]: 'with_sub', [OTHER]: 'other' }));
      await waitFor(() => expect(panels[0].sources).toEqual([OTHER_ID, SV_ID]));
      return screen.findByRole('list', { name: 'Echogram layers' });
    }

    it("lists the front session's rows first, named with their session's title", async () => {
      const list = await showBoth();
      const rows = within(list).getAllByRole('listitem');
      expect(rows.map((row) => row.dataset.sid)).toEqual([SID, OTHER]);
      expect(rows.map((row) => row.querySelector('.rc-row-title')?.textContent)).toEqual([
        'with_sub: sv U',
        'other: sv U',
      ]);
      expect(screen.getByText('Sources (2)')).toBeInTheDocument();
      expect(within(rows[1]).getByRole('button', { name: /^other: sv/ })).toHaveAttribute(
        'title',
        'Switch to other and select sv',
      );
      expect(within(rows[1]).getByRole('button', { name: 'Remove other: sv' })).toBeInTheDocument();
    });

    it("brings another session's tab to the front, to select the row's step there", async () => {
      const user = userEvent.setup();
      const list = await showBoth();
      await user.click(within(list).getByRole('button', { name: /^other: sv/ }));
      expect(useSessionStore.getState().sid).toBe(OTHER);
      expect(useTabs.getState().sids).toEqual([SID, OTHER]);
      expect(useTabs.getState().pendingSelection).toEqual({ sid: OTHER, step: 'sv' });
      expect(useView.getState().selected).toBeNull();
      // The other session is in front now, so its row comes first.
      expect(within(list).getAllByRole('listitem').map((row) => row.dataset.sid)).toEqual([OTHER, SID]);
    });

    it("offers a run only on the front session's rows, and a refresh on any", async () => {
      const list = await showBoth({ ...NO_RUN_CONTROLS, canRun: true });
      act(() => {
        useEchogramStore.getState().track(SID, svPreview('edited'), null);
        useEchogramStore.getState().track(OTHER, svPreview('edited'), null);
      });
      const [front, other] = within(list).getAllByRole('listitem');
      expect(within(front).getByRole('button', { name: 'Run to sv' })).toBeEnabled();
      expect(within(other).getByText('not computed yet: showing the previous settings')).toBeInTheDocument();
      expect(within(other).queryByRole('button', { name: 'Run to sv' })).toBeNull();

      act(() => useEchogramStore.getState().track(OTHER, svPreview('moved', ['user']), null));
      const refresh = vi.spyOn(useEchogramStore.getState(), 'refresh');
      await userEvent.setup().click(within(other).getByRole('button', { name: 'Refresh' }));
      expect(refresh).toHaveBeenCalledWith(OTHER_ID);
    });
  });

  it("names a row with its recipe once its tab is closed and another is in front", async () => {
    const DIVE = 'T';
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/sv/artifacts`]: svMapped,
    });
    useTabs.setState({ sids: [SID, DIVE] });
    useEchogramStore.getState().setTitles({ [SID]: 'demo_survey_sv', [DIVE]: 'demo_dive_analysis' });
    renderPane();
    await waitFor(() => expect(panels).toHaveLength(1));
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    const list = await screen.findByRole('list', { name: 'Echogram layers' });
    const title = () => list.querySelector('.rc-row-title')?.textContent;
    await waitFor(() => expect(title()).toBe('sv U'));
    expect(panels[0].names[SV_ID]).toBe('sv');

    act(() => {
      useTabs.setState({ sids: [DIVE] });
      useSessionStore.getState().setSid(DIVE);
      useEchogramStore.getState().activate(DIVE);
    });
    expect(title()).toBe('demo_survey_sv: sv U');

    // A source added while its tab is behind is named with its recipe in the panel too.
    act(() => useEchogramStore.getState().remove(SV_ID));
    await waitFor(() => expect(panels[0].sources).toEqual([]));
    await act(() => useEchogramStore.getState().show(SID, 'sv'));
    await waitFor(() => expect(panels[0].names[SV_ID]).toBe('demo_survey_sv: sv'));
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
    act(() => useEchogramStore.getState().track(SID, makePreview([previewStep('seed', { pinned: true, checkpoint: 'will-save' })]), null));
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
    expect(useEchogramStore.getState().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: 'r7' });
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
    await waitFor(() => expect(panels[0].sources).toEqual([SV_ID]));
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
      await waitFor(() => expect(panels[1].sources).toEqual([SV_ID]));
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

  beforeEach(() => {
    useRunStore.getState().resetFor(SID);
  });

  afterEach(() => {
    useRunStore.getState().resetFor(null);
  });

  it("reads each session's rows from its own preview, and the followed run only for the front", async () => {
    const OTHER = 'T';
    const row = (sid: string, stepHash: string) =>
      restoredLayer({ sid, step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash, addedAt: 0 });
    mockApi({
      'GET /api/sessions': [
        { id: SID, title: 'with_sub', origin: 'C:/recipes/with_sub.yaml', created: '2026-10-13T15:30:12Z' },
        { id: OTHER, title: 'other', origin: 'C:/recipes/other.yaml', created: '2026-10-13T15:31:12Z' },
      ],
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${OTHER}`]: makeSession({ id: OTHER }),
      [`GET /api/sessions/${SID}/preview`]: svPreview('h', ['user']),
      [`GET /api/sessions/${OTHER}/preview`]: svPreview('g', ['user']),
    });
    useTabs.setState({ sids: [SID, OTHER] });
    useEchogramStore.setState({ layers: [row(SID, 'h'), row(OTHER, 'g')] });
    const freshness = () => useEchogramStore.getState().layers.map((layer) => layer.freshness.state);
    renderWithClient(<EchogramWatcher />);
    await waitFor(() => expect(freshness()).toEqual(['current', 'current']));
    await waitFor(() => expect(useEchogramStore.getState().titles).toEqual({ [SID]: 'with_sub', [OTHER]: 'other' }));

    act(() => useRunStore.setState({ last: finished('r1', 'sv') }));
    await waitFor(() => expect(freshness()).toEqual(['available', 'current']));

    // The run store follows the other tab now; the session left keeps the run it was tracked with.
    act(() => useRunStore.getState().resetFor(OTHER));
    expect(useEchogramStore.getState().bySession[SID]?.computed?.runId).toBe('r1');
    expect(freshness()).toEqual(['available', 'current']);
  });

  it('watches a session that has layers but no open tab', async () => {
    mockApi({
      'GET /api/sessions/X': makeSession({ id: 'X' }),
      'GET /api/sessions/X/preview': svPreview('moved', ['user']),
    });
    useTabs.setState({ sids: [] });
    useEchogramStore.setState({
      layers: [restoredLayer({ sid: 'X', step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 })],
    });
    renderWithClient(<EchogramWatcher />);
    await waitFor(() => expect(useEchogramStore.getState().layers[0].freshness).toEqual({ state: 'available' }));
  });

  it('drops the layers of a session the server no longer has', async () => {
    mockApi({
      'GET /api/sessions/X': () => errorResponse(404, 'No session'),
      'GET /api/sessions/Y': makeSession({ id: 'Y' }),
      'GET /api/sessions/Y/preview': svPreview('h'),
    });
    const row = (sid: string) =>
      restoredLayer({ sid, step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 });
    useTabs.setState({ sids: [] });
    useEchogramStore.setState({ layers: [row('X'), row('Y')] });
    renderWithClient(<EchogramWatcher />);
    await waitFor(() => expect(useEchogramStore.getState().layers.map((layer) => layer.sid)).toEqual(['Y']));
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
      layers: [restoredLayer({ sid: SID, step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 })],
    });
    renderWithClient(<EchogramWatcher />);
    await waitFor(() => expect(useEchogramStore.getState().layers[0].freshness).toEqual({ state: 'current' }));

    act(() => useRunStore.setState({ last: finished('r1', 'sv') }));
    await waitFor(() => expect(useEchogramStore.getState().layers[0].freshness).toEqual({ state: 'available' }));

    act(() => {
      useRunStore.getState().begin('r2');
      useEchogramStore.getState().runStarted(SID, 'dive_mask_sparse', 'r2');
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
      layers: [restoredLayer({ sid: SID, step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 0 })],
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
      useEchogramStore.getState().runStarted(SID, 'dive_mask_sparse', 'r1');
    });
    expect(useEchogramStore.getState().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: 'r1' });

    // The server restarted: the run store follows no run any more, and no run_finished comes.
    act(() => useRunStore.getState().resetFor(null));
    await waitFor(() => expect(useEchogramStore.getState().pendingRun).toBeNull());
  });
});
