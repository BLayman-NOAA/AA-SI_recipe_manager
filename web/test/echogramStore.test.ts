import type { LineSpec, PieceSetSpec, Resolution, ViewSettings } from 'aa-si-echogram-gl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StepArtifacts } from '../src/api/models';
import { LINE_COLORS, LINE_WIDTH, NEEDS_VALUE } from '../src/echogram/layers';
import { actionsFor, freshnessText } from '../src/echogram/LayerList';
import { LAYERS_KEY, legacyKey, NEEDS_WEBGPU, useEchogramStore } from '../src/echogram/store';
import type { Preview } from '../src/api/models';
import type { RunStartedData } from '../src/runs/events';
import { initialRunState, type RunState } from '../src/runs/reducer';
import { useSessionStore } from '../src/state/session';
import { useTabs } from '../src/state/tabs';
import diveMaskSparse from './fixtures/artifacts.dive_mask_sparse.json';
import diveSv from './fixtures/artifacts.dive_sv.json';
import embed from './fixtures/artifacts.embed_results.json';
import svPlain from './fixtures/artifacts.sv.json';
import svLine from './fixtures/artifacts.sv_line.json';
import svMapped from './fixtures/artifacts.sv_mapped.json';
import { errorResponse, makePreview, mockApi, previewStep } from './helpers';

const SID = 'S';
const SV = 'GET /api/sessions/S/steps/sv/artifacts';
const SV_ID = 'S/sv/ds_Sv/Sv';
const SV_CHOSEN = `${SV}?output=ds_Sv&var=Sv`;

/** An EchogramGL panel stand-in that tracks its sources. */
function fakePanel() {
  const ids: string[] = [];
  const panel = {
    get sourceIds() {
      return [...ids];
    },
    addSource: vi.fn(async (id: string) => {
      ids.push(id);
    }),
    replaceSource: vi.fn(async () => {}),
    removeSource: vi.fn(async (id: string) => {
      ids.splice(ids.indexOf(id), 1);
    }),
    retry: vi.fn(async () => {}),
    settingsObject: undefined as ViewSettings | undefined,
    applySettings: vi.fn(async (_settings: ViewSettings) => {}),
    setLines: vi.fn((_specs: LineSpec[]) => {}),
  };
  return panel;
}

/** Panel settings over sv and another source, with a window and the given layers. */
function view(layers: ViewSettings['layers']): ViewSettings {
  return {
    version: 2,
    sources: [{ id: SV_ID, store: 'u' }, { id: 'other', store: 'v' }],
    layers,
    level: 'auto',
    pixelsPerPing: 1,
    colormap: 'magma',
    filter: 'nearest',
    xUnit: 'time',
    yUnit: 'depth',
    aspect: { mode: 'free', exaggeration: 1 },
    window: { x: [0, 10], y: [0, 50] },
  };
}

const store = () => useEchogramStore.getState();
const layer = (id: string) => store().layers.find((entry) => entry.id === id);

/** The fixture answer with its resolution changed. */
function withResolution(found: unknown, change: Partial<Resolution>): StepArtifacts {
  const copy = structuredClone(found) as StepArtifacts;
  copy.echogram.resolution = { ...copy.echogram.resolution, ...change };
  return copy;
}

/** Make every layer look fetched long ago, so a forgotten mount is fetched again. */
function age() {
  useEchogramStore.setState((state) => ({ layers: state.layers.map((entry) => ({ ...entry, addedAt: 0 })) }));
}

/** Wait for the store's queued panel calls. */
async function settle() {
  await vi.waitFor(() => expect(store().layers.every((entry) => entry.state !== 'loading' && entry.state !== 'refreshing')).toBe(true));
}

/** The answer for sv's current hash: the fixture's data at another hash, or nothing saved. */
function svAt(hash: string, saved = true): StepArtifacts {
  const answer = structuredClone((saved ? svMapped : diveMaskSparse) as unknown as StepArtifacts);
  answer.step_id = 'sv';
  answer.step_hash = hash;
  return answer;
}

/** A preview holding sv at a hash, by tier, or for some of its three files. */
function svPreview(hash: string, held: string[] = [], instancesHeld: Record<string, number> = {}): Preview {
  return makePreview([
    previewStep('sv', {
      step_hash: hash,
      held_by: held,
      fanned_out: true,
      instances: 3,
      instances_held: instancesHeld,
      checkpoint: 'will-save',
      recipe_checkpoint: 'always',
    }),
  ]);
}

beforeEach(() => {
  useEchogramStore.setState({
    front: null,
    gpu: 'unknown',
    layers: [],
    panel: null,
    settings: null,
    choice: null,
    pendingRun: null,
    message: null,
    busy: null,
    bySession: {},
    titles: {},
    lines: {},
    bare: [],
  });
  useTabs.setState({ sids: [SID, 'T'], pendingSelection: null });
  store().activate(SID);
});

afterEach(() => {
  vi.unstubAllGlobals();
  useSessionStore.getState().setSid(null);
});

describe('show', () => {
  it('draws a mapped step as a piece set, one piece per mounted instance', async () => {
    mockApi({ [SV]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await settle();

    expect(panel.addSource).toHaveBeenCalledTimes(1);
    const [id, spec] = panel.addSource.mock.calls[0] as unknown as [string, PieceSetSpec];
    expect(id).toBe(SV_ID);
    expect(spec.pieces).toHaveLength(3);
    const mounts = svMapped.echogram.resolution.instances.map((instance) => instance.mount);
    expect(spec.pieces.map((piece) => piece.header)).toEqual(mounts.map((m) => `/echogram/api/describe/${m}`));
    expect(spec.pieces.map((piece) => piece.store)).toEqual(mounts.map((m) => `/echogram/mount/${m}/`));
    expect(layer(SV_ID)).toMatchObject({ step: 'sv', label: 'sv', state: 'shown', stepHash: svMapped.step_hash });
  });

  it('keeps a layer drawn while the pane is closed, and draws it when the panel attaches', async () => {
    mockApi({ [SV]: svPlain });
    await store().show(SID, 'sv');
    expect(layer(SV_ID)?.state).toBe('loading');
    const panel = fakePanel();
    await store().attach(panel);
    expect(panel.addSource).toHaveBeenCalledWith(SV_ID, expect.objectContaining({ pieces: expect.any(Array) }));
    expect(layer(SV_ID)?.state).toBe('shown');
  });

  it('offers a choice when a step holds several things to draw, and fetches the one picked', async () => {
    const labels = withResolution(embed, { var: 'ml_dataset_hdbscan_results_grid' });
    const fetchMock = mockApi({
      'GET /api/sessions/S/steps/embed_results/artifacts': embed,
      'GET /api/sessions/S/steps/embed_results/artifacts?output=ds_normalized&var=ml_dataset_hdbscan_results_grid': labels,
    });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'embed_results');

    const choice = store().choice!;
    expect(choice.choices).toHaveLength(4);
    expect(choice.choices[choice.selected]).toMatchObject({ output: 'ds_normalized', var: 'Sv' });
    expect(panel.addSource).not.toHaveBeenCalled();

    await store().addChoice(choice.choices[0]);
    await settle();
    expect(fetchMock).toHaveBeenLastCalledWith(
      '/api/sessions/S/steps/embed_results/artifacts?output=ds_normalized&var=ml_dataset_hdbscan_results_grid',
      expect.anything(),
    );
    expect(layer('S/embed_results/ds_normalized/ml_dataset_hdbscan_results_grid')?.label).toBe(
      'embed_results:ml_dataset_hdbscan_results_grid',
    );
    // The chooser stays open for the MVBS under the labels, which needs no second request.
    const calls = fetchMock.mock.calls.length;
    await store().addChoice(choice.choices[2]);
    await settle();
    expect(fetchMock.mock.calls.length).toBe(calls);
    expect(panel.sourceIds).toEqual(['S/embed_results/ds_normalized/ml_dataset_hdbscan_results_grid', 'S/embed_results/ds_normalized/Sv']);
    store().cancelChoice();
    expect(store().choice).toBeNull();
  });

  it('draws the Sv of a step whose dataset also lists its depth, with the empty instance counted', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_sv/artifacts': diveSv });
    await store().show(SID, 'dive_sv');
    expect(store().choice).toBeNull();
    expect(layer('S/dive_sv/ds_Sv/Sv')?.resolution?.empty).toBe(1);
  });

  it('offers to run to a step that is not saved', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    await store().show(SID, 'dive_mask_sparse');
    expect(store().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: null, memoryOnly: false });
    expect(store().message).toBe('dive_mask_sparse has no saved result yet. Run to dive_mask_sparse to draw it.');
    expect(store().layers).toEqual([]);
  });

  it('says a step that is not a checkpoint is saved only by a run to it, and offers to make it one', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    store().track(SID, makePreview([previewStep('dive_mask_sparse', { checkpoint: 'will-save', pinned: true })]), null);
    await store().show(SID, 'dive_mask_sparse');
    expect(store().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: null, memoryOnly: true });
    expect(store().message).toBe(
      "dive_mask_sparse isn't a checkpoint, so only a run to it saves it. " +
        'Run to dive_mask_sparse to draw it, or make it a checkpoint so every run saves it.',
    );
    store().checkpointMade('dive_mask_sparse');
    expect(store().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: null, memoryOnly: false });
    expect(store().message).toBe('dive_mask_sparse has no saved result yet. Run to dive_mask_sparse to draw it.');
  });

  it('does not call a checkpoint the preview leaves out of its run a step kept in memory', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    const step = previewStep('dive_mask_sparse', { planned: 'untargeted', checkpoint: 'memory-only', recipe_checkpoint: 'always' });
    store().track(SID, makePreview([step]), null);
    await store().show(SID, 'dive_mask_sparse');
    expect(store().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: null, memoryOnly: false });
    expect(store().message).toBe('dive_mask_sparse has no saved result yet. Run to dive_mask_sparse to draw it.');
  });

  it('says a checkpoint not run for its settings has no saved result yet', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    store().track(SID, makePreview([previewStep('dive_mask_sparse', { checkpoint: 'will-save', recipe_checkpoint: 'always' })]), null);
    await store().show(SID, 'dive_mask_sparse');
    expect(store().pendingRun?.memoryOnly).toBe(false);
    expect(store().message).toBe('dive_mask_sparse has no saved result yet. Run to dive_mask_sparse to draw it.');
  });

  it('says a step drawn earlier is not saved any more when its result for the same hash is gone', async () => {
    mockApi({ [SV]: svMapped });
    await store().show(SID, 'sv');
    mockApi({ [SV]: svAt(svMapped.step_hash, false) });
    await store().show(SID, 'sv');
    expect(store().message).toBe('sv is not saved any more.');
    expect(store().pendingRun).toMatchObject({ step: 'sv', runId: null });
  });

  it('says why when the server cannot draw echograms, or the step drew nothing', async () => {
    const unavailable = structuredClone(svMapped) as unknown as StepArtifacts;
    unavailable.echogram = { available: false, reason: 'aa-si-echogram-gl is not installed', resolution: null };
    mockApi({ [SV]: unavailable });
    await store().show(SID, 'sv');
    expect(store().message).toBe('aa-si-echogram-gl is not installed');

    const nothing = withResolution(svMapped, {
      kind: null,
      outputs: [{ name: 'out', format: 'pickle', kind: null, reason: 'pickle is not drawable' }],
    });
    mockApi({ [SV]: nothing });
    await store().show(SID, 'sv');
    expect(store().message).toBe('sv has no echogram to draw (out: pickle is not drawable).');
  });

  it('says why a lookup failed', async () => {
    mockApi({ [SV]: errorResponse(404, "Step 'sv' not found") });
    await store().show(SID, 'sv');
    expect(store().message).toBe("Could not look up sv: Step 'sv' not found");
    expect(store().busy).toBeNull();
  });
});

describe('a drawn layer', () => {
  async function shown() {
    mockApi({ [SV]: svMapped, [SV_CHOSEN]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await settle();
    return panel;
  }

  it('retries on refresh when the checkpoint is the same, and is current again', async () => {
    const panel = await shown();
    store().track(SID, svPreview('moved', ['user']), null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'available' });
    mockApi({ [SV_CHOSEN]: svAt('moved') });
    await store().refresh(SV_ID);
    await settle();
    expect(panel.retry).toHaveBeenCalledTimes(1);
    expect(panel.replaceSource).not.toHaveBeenCalled();
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'current' });
  });

  it('replaces the source on refresh when a run wrote it again', async () => {
    const panel = await shown();
    mockApi({ [SV_CHOSEN]: withResolution(svMapped, { createdAt: '2026-10-20T00:00:00+00:00' }) });
    await store().refresh(SV_ID);
    await settle();
    expect(panel.replaceSource).toHaveBeenCalledWith(SV_ID, expect.objectContaining({ pieces: expect.any(Array) }));
    expect(layer(SV_ID)?.resolution?.createdAt).toBe('2026-10-20T00:00:00+00:00');
  });

  it('marks the row when its result for the same hash is not saved any more', async () => {
    await shown();
    mockApi({ [SV_CHOSEN]: svAt(svMapped.step_hash, false) });
    await store().refresh(SV_ID);
    expect(layer(SV_ID)).toMatchObject({ state: 'error', error: 'sv is not saved any more.' });
  });

  it('keeps drawing the previous settings when nothing holds the new hash', async () => {
    await shown();
    store().track(SID, svPreview('moved'), null);
    mockApi({ [SV_CHOSEN]: svAt('moved', false) });
    await store().refresh(SV_ID);
    expect(layer(SV_ID)).toMatchObject({ state: 'shown', error: null, freshness: { state: 'not_computed' } });
    expect(layer(SV_ID)?.stepHash).toBe(svMapped.step_hash);
  });

  it('says the new hash is not saved when a refresh finds nothing the preview said was held, until a run', async () => {
    await shown();
    store().track(SID, svPreview('moved', ['user']), null);
    expect(actionsFor(layer(SV_ID)!)).toBe('refresh');
    mockApi({ [SV_CHOSEN]: svAt('moved', false) });
    await store().refresh(SV_ID);
    expect(layer(SV_ID)).toMatchObject({ state: 'shown', error: null, stepHash: svMapped.step_hash });
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'not_saved' });
    expect(freshnessText(layer(SV_ID)!.freshness, true)).toBe(
      'not saved at the current settings: showing the previous settings',
    );
    expect(actionsFor(layer(SV_ID)!)).toBeNull();

    // The same preview again offers no refresh; a run that finishes does.
    store().track(SID, svPreview('moved', ['user']), null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'not_saved' });
    const ran = { steps: new Set(['sv']), runId: 'r1' };
    store().track(SID, svPreview('moved', ['user']), ran);
    expect(actionsFor(layer(SV_ID)!)).toBe('refresh');

    mockApi({ [SV_CHOSEN]: svAt('moved') });
    await store().refresh(SV_ID);
    await settle();
    expect(layer(SV_ID)).toMatchObject({ stepHash: 'moved', missed: undefined, freshness: { state: 'current' } });
  });

  it('goes from not computed after an edit, to available after a run, to current after a refresh', async () => {
    const panel = await shown();
    const drawn = svMapped.step_hash;
    store().track(SID, svPreview(drawn, ['user']), null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'current' });

    // An edit moves the hash; the preview refetches and nothing holds it.
    store().track(SID, svPreview('edited'), null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'not_computed' });

    // A run saves it; the preview refetches after the run.
    const ran = { steps: new Set(['sv']), runId: 'r1' };
    store().track(SID, svPreview('edited', ['user']), ran);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'available' });

    mockApi({ [SV_CHOSEN]: withResolution(svAt('edited'), { stepHash: 'edited' }) });
    await store().refresh(SV_ID);
    await settle();
    expect(panel.replaceSource).toHaveBeenCalled();
    expect(layer(SV_ID)).toMatchObject({ stepHash: 'edited', freshness: { state: 'current' } });
  });

  it('is available when a value is changed back to one computed before', async () => {
    await shown();
    store().track(SID, svPreview('earlier', ['user']), null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'available' });
  });

  it('is available when a later run saved the step again at the same hash', async () => {
    await shown();
    store().track(SID, svPreview(svMapped.step_hash, ['user']), { steps: new Set(['sv']), runId: 'r1' });
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'available' });
  });

  it('is current again after a refresh that follows a run, and available after a later run, whatever the clocks', async () => {
    const drawn = svMapped.step_hash;
    // The browser's clock a day ahead of the server's while the row is drawn and refreshed.
    const now = vi.spyOn(Date, 'now').mockReturnValue(Date.now() + 86400000);
    await shown();
    expect(layer(SV_ID)?.drawnAfter).toBeNull();
    store().track(SID, svPreview(drawn, ['user']), { steps: new Set(['sv']), runId: 'r1' });
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'available' });

    mockApi({ [SV_CHOSEN]: svMapped });
    await store().refresh(SV_ID);
    await settle();
    expect(layer(SV_ID)).toMatchObject({ drawnAfter: 'r1', freshness: { state: 'current' } });

    // Then the browser's clock a day behind, and a later run computes sv again.
    now.mockReturnValue(Date.now() - 2 * 86400000);
    store().track(SID, svPreview(drawn, ['user']), { steps: new Set(['other']), runId: 'r2' });
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'current' });
    store().track(SID, svPreview(drawn, ['user']), { steps: new Set(['sv']), runId: 'r3' });
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'available' });
    now.mockRestore();
  });

  it('says how many files of a mapped step hold the new hash', async () => {
    await shown();
    store().track(SID, svPreview('edited', [], { user: 1, survey: 0 }), null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'partial', held: 1, of: 3 });
    store().track(SID, null, null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'unknown' });
  });

  it('refreshes its own row, not the default, after a run its Run button started', async () => {
    await shown();
    store().track(SID, svPreview('edited'), null);
    store().runStarted(SID, 'sv', 'r1', SV_ID);
    expect(store().pendingRun).toEqual({ sid: SID, step: 'sv', runId: 'r1', layer: SV_ID });
    const refresh = vi.spyOn(store(), 'refresh');
    const show = vi.spyOn(store(), 'show');
    mockApi({ [SV_CHOSEN]: svAt('edited') });
    const instances = { count: null, done: 0, computed: 0, cached: 0, skipped: 0 };
    store().runFinished(
      {
        ...initialRunState('r1'),
        started: { targets: ['sv'] } as unknown as RunStartedData,
        finished: { status: 'completed', seconds: 1, manifest: null, executed: [], skipped: [], pruned: [], untargeted: [], error_type: null, message: null },
        steps: { sv: { status: 'computed', tier: null, hash: null, planned: null, index: 0, total: 0, instances, seconds: null, error: null, chain: null } },
      },
      null,
    );
    expect(refresh).toHaveBeenCalledWith(SV_ID);
    expect(show).not.toHaveBeenCalled();
    await vi.waitFor(() => expect(layer(SV_ID)?.stepHash).toBe('edited'));
    refresh.mockRestore();
    show.mockRestore();
  });

  it('says why a restored row with nothing at its new hash draws nothing', async () => {
    window.localStorage.setItem(
      LAYERS_KEY,
      JSON.stringify({
        layers: [{ sid: 'T', step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'old', addedAt: 1 }],
        settings: null,
      }),
    );
    mockApi({ 'GET /api/sessions/T/steps/sv/artifacts?output=ds_Sv&var=Sv': svAt('new', false) });
    store().restore();
    await store().attach(fakePanel());
    expect(layer('T/sv/ds_Sv/Sv')).toMatchObject({ state: 'error', error: 'sv has no saved result yet. Run to sv to draw it.' });
  });

  it('is removed from the panel with its row', async () => {
    const panel = await shown();
    store().remove(SV_ID);
    await vi.waitFor(() => expect(panel.removeSource).toHaveBeenCalledWith(SV_ID));
    expect(store().layers).toEqual([]);
  });

  it('keeps its row when the user removes its last layer in the panel, and says so until a layer comes back', async () => {
    const panel = await shown();
    store().onPanelLayersChange([{ id: 0, source: 'other' } as never]);
    expect(store().bare).toEqual([SV_ID]);
    expect(layer(SV_ID)?.state).toBe('shown');
    expect(panel.removeSource).not.toHaveBeenCalled();

    store().onPanelLayersChange([{ id: 0, source: SV_ID } as never]);
    expect(store().bare).toEqual([]);
  });

  it('adds a layer of its source on top, on the first free channel, leaving the view where it is', async () => {
    const panel = await shown();
    store().onPanelLayersChange([]);
    const below = { id: 'o', source: 'other', channel: 0 };
    const taken = { id: `${SV_ID}-layer`, source: SV_ID, channel: 0 };
    panel.settingsObject = view([below, taken]);
    await store().addLayer(SV_ID);
    expect(panel.applySettings).toHaveBeenCalledTimes(1);
    const applied = panel.applySettings.mock.calls[0][0];
    expect(applied).toEqual({
      ...view([below, taken, { id: `${SV_ID}-layer-1`, source: SV_ID, channel: 1, color: { colormap: 'magma' } }]),
      window: undefined,
    });
  });

  it('adds a labels layer on the cluster palette', async () => {
    const grid = 'S/embed_results/ds_normalized/ml_dataset_hdbscan_results_grid';
    mockApi({
      'GET /api/sessions/S/steps/embed_results/artifacts': embed,
      'GET /api/sessions/S/steps/embed_results/artifacts?output=ds_normalized&var=ml_dataset_hdbscan_results_grid':
        withResolution(embed, { var: 'ml_dataset_hdbscan_results_grid' }),
    });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'embed_results');
    await store().addChoice(store().choice!.choices[0]);
    await settle();
    store().onPanelLayersChange([]);
    expect(store().bare).toEqual([grid]);
    panel.settingsObject = view([]);
    await store().addLayer(grid);
    expect(panel.applySettings.mock.calls[0][0].layers).toEqual([
      { id: `${grid}-layer-1`, source: grid, channel: 0, color: { palette: 'cluster' } },
    ]);
  });

  it('is removed with its source from the list while it has no layer', async () => {
    const panel = await shown();
    store().onPanelLayersChange([]);
    store().remove(SV_ID);
    expect(store().bare).toEqual([]);
    expect(store().layers).toEqual([]);
    await vi.waitFor(() => expect(panel.removeSource).toHaveBeenCalledWith(SV_ID));
  });

  it('waits for its own panel calls, then reads the newest stack the panel reported', async () => {
    const panel = await shown();
    const hold = () => {
      let release = () => {};
      panel.retry.mockImplementationOnce(() => new Promise<void>((resolve) => (release = resolve)));
      return () => release();
    };
    mockApi({ [SV_CHOSEN]: svMapped });

    let release = hold();
    const kept = store().refresh(SV_ID);
    await vi.waitFor(() => expect(panel.retry).toHaveBeenCalledTimes(1));
    store().onPanelLayersChange([]);
    store().onPanelLayersChange([{ id: 0, source: SV_ID } as never]);
    release();
    await kept;
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(store().layers).toHaveLength(1);
    expect(panel.removeSource).not.toHaveBeenCalled();

    expect(store().bare).toEqual([]);

    release = hold();
    const bared = store().refresh(SV_ID);
    await vi.waitFor(() => expect(panel.retry).toHaveBeenCalledTimes(2));
    store().onPanelLayersChange([]);
    expect(store().bare).toEqual([]);
    release();
    await bared;
    await vi.waitFor(() => expect(store().bare).toEqual([SV_ID]));
    expect(store().layers).toHaveLength(1);
    expect(panel.removeSource).not.toHaveBeenCalled();
  });

  it('stays when the panel still has a layer reading it', async () => {
    const panel = await shown();
    store().onPanelLayersChange([{ id: 0, source: SV_ID } as never]);
    expect(panel.removeSource).not.toHaveBeenCalled();
    expect(store().layers).toHaveLength(1);
  });

  it('is resolved again when the server forgot its mounts, but not again while fresh', async () => {
    const panel = await shown();
    age();
    const fetchMock = mockApi({ [SV_CHOSEN]: svMapped });
    store().onPanelError(new Error('gone'));
    store().onPanelError(new Error('gone'));
    store().onPanelError(new Error('404 Not Found'));
    await vi.waitFor(() => expect(panel.retry).toHaveBeenCalledTimes(1));
    await settle();
    expect(fetchMock).toHaveBeenCalledTimes(1);

    // Just fetched, and still gone: the row says so rather than asking again.
    store().onPanelError(new Error('gone'));
    expect(layer(SV_ID)).toMatchObject({ state: 'error', error: 'gone' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('resolves again only the layer whose mount a chunk error names', async () => {
    const panel = await shown();
    const labels = structuredClone(svMapped) as unknown as StepArtifacts;
    labels.echogram.resolution = { ...labels.echogram.resolution, step: 'other' };
    mockApi({ 'GET /api/sessions/S/steps/other/artifacts': labels });
    await store().show(SID, 'other');
    await settle();
    age();
    const mount = svMapped.echogram.resolution.instances[1].mount;
    const fetchMock = mockApi({ [SV_CHOSEN]: svMapped });
    store().onPanelError(new Error(`410 Gone for http://x/echogram/mount/${mount}/Sv/0.0.0`));
    await vi.waitFor(() => expect(panel.retry).toHaveBeenCalledTimes(1));
    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/S/steps/sv/artifacts?output=ds_Sv&var=Sv`, expect.anything());
  });
});

describe('sessions and reloads', () => {
  const T_SV = 'GET /api/sessions/T/steps/sv/artifacts';
  const T_SV_ID = 'T/sv/ds_Sv/Sv';
  const saved = () => JSON.parse(window.localStorage.getItem(LAYERS_KEY)!);

  it("saves every tab's layers and the settings under one key and restores them on a reload", async () => {
    mockApi({ [SV]: svMapped, [SV_CHOSEN]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await settle();
    const settings = { version: 1, layers: [{ source: SV_ID, channel: 1 }] } as unknown as ViewSettings;
    panel.settingsObject = settings;
    store().captureSettings();
    expect(saved().layers).toEqual([expect.objectContaining({ sid: SID, step: 'sv', output: 'ds_Sv', variable: 'Sv' })]);
    expect(saved().settings).toEqual(settings);

    // A reload: a new panel, the rows read back from storage.
    store().detach();
    useEchogramStore.setState({ layers: [], settings: null });
    const reloaded = fakePanel();
    store().restore();
    await store().attach(reloaded);
    expect(reloaded.sourceIds).toEqual([SV_ID]);
    expect(reloaded.applySettings.mock.calls[0][0]).toMatchObject({ layers: [{ source: SV_ID, channel: 1 }] });
    expect(layer(SV_ID)?.state).toBe('shown');
  });

  it('restores a source saved without a layer as one without a layer', async () => {
    mockApi({ [SV]: svMapped, [SV_CHOSEN]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await settle();
    store().onPanelLayersChange([]);
    panel.settingsObject = view([]);
    store().captureSettings();
    expect(saved()).not.toHaveProperty('bare');

    store().detach();
    useEchogramStore.setState({ layers: [], settings: null });
    const reloaded = fakePanel();
    // The panel opens a source with a layer of its own, which the saved settings replace.
    reloaded.settingsObject = view([{ id: `${SV_ID}-layer`, source: SV_ID, channel: 0 }]);
    store().restore();
    await store().attach(reloaded);
    expect(reloaded.applySettings.mock.calls[0][0].layers).toEqual([]);
    expect(store().bare).toEqual([]);
    store().onPanelLayersChange([]);
    expect(store().bare).toEqual([SV_ID]);
  });

  it("imports the front session's layers from the key it had before, once", () => {
    window.localStorage.setItem(
      legacyKey(SID),
      JSON.stringify({ layers: [{ step: 'sv', output: 'ds_Sv', label: 'sv', stepHash: 'h' }], settings: { version: 1, layers: [] } }),
    );
    window.localStorage.setItem(legacyKey('T'), JSON.stringify({ layers: [{ step: 'mvbs', output: 'ds_MVBS' }] }));
    useSessionStore.getState().setSid(SID);
    store().restore();
    expect(store().layers).toEqual([
      expect.objectContaining({ id: 'S/sv/ds_Sv', sid: SID, step: 'sv', variable: null, addedAt: 0, state: 'loading' }),
    ]);
    expect(store().settings).toEqual({ version: 1, layers: [] });
    expect(saved().layers).toEqual([
      { sid: SID, step: 'sv', output: 'ds_Sv', variable: null, label: 'sv', stepHash: 'h', addedAt: 0 },
    ]);
    expect(window.localStorage.getItem(legacyKey(SID))).toBeNull();

    // Another session's old key is left alone, and never imported once the new key exists.
    useSessionStore.getState().setSid('T');
    store().restore();
    expect(store().layers.map((entry) => entry.sid)).toEqual([SID]);
    expect(window.localStorage.getItem(legacyKey('T'))).not.toBeNull();
  });

  it('imports nothing later when the front session had no old key', () => {
    useSessionStore.getState().setSid(SID);
    store().restore();
    expect(saved()).toEqual({ layers: [], settings: null });
    window.localStorage.setItem(legacyKey('T'), JSON.stringify({ layers: [{ step: 'mvbs', output: 'ds_MVBS' }] }));
    useSessionStore.getState().setSid('T');
    store().restore();
    expect(store().layers).toEqual([]);
  });

  it('keeps a saved row whose session has no open tab, drops one without a session, and reads a malformed value as empty', () => {
    const row = (sid: string) => ({ sid, step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 1 });
    window.localStorage.setItem(LAYERS_KEY, JSON.stringify({ layers: [row(SID), row('X'), { step: 'sv', output: 'ds_Sv' }], settings: null }));
    store().restore();
    expect(store().layers.map((entry) => entry.id)).toEqual([SV_ID, 'X/sv/ds_Sv/Sv']);

    window.localStorage.setItem(LAYERS_KEY, JSON.stringify({ layers: 5, settings: 3 }));
    store().restore();
    expect(store().layers).toEqual([]);
    expect(store().settings).toBeNull();
    window.localStorage.setItem(LAYERS_KEY, '{not json');
    store().restore();
    expect(store().layers).toEqual([]);
  });

  it('keeps every source when another tab comes to the front, and clears what the pane said', async () => {
    mockApi({ [SV]: svMapped, 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await store().show(SID, 'dive_mask_sparse');
    await settle();
    expect(store().pendingRun).not.toBeNull();
    expect(store().message).not.toBeNull();

    store().activate('T');
    expect(store()).toMatchObject({ front: 'T', choice: null, pendingRun: null, message: null, busy: null });
    expect(store().layers.map((entry) => entry.id)).toEqual([SV_ID]);
    expect(panel.sourceIds).toEqual([SV_ID]);
    expect(panel.removeSource).not.toHaveBeenCalled();
  });

  it('draws the same step of two sessions as two sources, each from its own session', async () => {
    const fetchMock = mockApi({ [SV]: svMapped, [T_SV]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    store().activate('T');
    await store().show('T', 'sv');
    await settle();
    expect(panel.sourceIds).toEqual([SV_ID, T_SV_ID]);
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/T/steps/sv/artifacts', expect.anything());
    expect(store().layers.map((entry) => entry.label)).toEqual(['sv', 'sv']);
  });

  it("names the panel's source with the session's title when the layers span sessions", async () => {
    mockApi({ [SV]: svMapped, [T_SV]: svMapped });
    store().setTitles({ S: 'demo_survey_sv', T: 'demo_dive_analysis' });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await store().show('T', 'sv');
    await settle();
    const names = panel.addSource.mock.calls.map((call) => ((call as unknown[])[1] as PieceSetSpec).name);
    expect(names).toEqual(['sv', 'demo_dive_analysis: sv']);
  });

  it("names the panel's source with the bare label while its session's title is not known", async () => {
    mockApi({ [SV]: svMapped, [T_SV]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await store().show('T', 'sv');
    await settle();
    const names = panel.addSource.mock.calls.map((call) => ((call as unknown[])[1] as PieceSetSpec).name);
    expect(names).toEqual(['sv', 'sv']);
  });

  it('draws an answer that comes after a tab switch, but says nothing on the new tab', async () => {
    mockApi({ [SV]: svMapped, 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    const drawn = store().show(SID, 'sv');
    expect(store().busy).toBe('sv');
    store().activate('T');
    await drawn;
    expect(store().layers.map((entry) => entry.id)).toEqual([SV_ID]);

    store().activate(SID);
    const offered = store().show(SID, 'dive_mask_sparse');
    expect(store().busy).toBe('dive_mask_sparse');
    store().activate('T');
    await offered;
    expect(store()).toMatchObject({ busy: null, message: null, pendingRun: null, choice: null });
  });

  it("removes a closed session's rows, sources and tracking, and leaves the others", async () => {
    mockApi({ [SV]: svMapped, [T_SV]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await store().show('T', 'sv');
    await settle();
    store().track(SID, svPreview('moved', ['user']), null);
    store().track('T', svPreview('moved', ['user']), null);

    store().removeSession(SID);
    expect(store().layers.map((entry) => entry.id)).toEqual([T_SV_ID]);
    expect(Object.keys(store().bySession)).toEqual(['T']);
    expect(saved().layers.map((entry: { sid: string }) => entry.sid)).toEqual(['T']);
    await vi.waitFor(() => expect(panel.sourceIds).toEqual([T_SV_ID]));
    expect(panel.removeSource).toHaveBeenCalledTimes(1);
  });

  it("drops only the removed session's saved rows while the pane has no WebGPU", () => {
    const row = (sid: string) => ({ sid, step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'h', addedAt: 1 });
    const settings = { version: 1, layers: [] };
    window.localStorage.setItem(LAYERS_KEY, JSON.stringify({ layers: [row(SID), row('T')], settings }));
    store().restore();
    store().gpuUnavailable();
    store().removeSession(SID);
    expect(store().layers).toEqual([]);
    expect(saved()).toEqual({ layers: [row('T')], settings });
  });

  it("sets the freshness of one session's rows from its own preview", async () => {
    mockApi({ [SV]: svMapped, [T_SV]: svMapped });
    await store().show(SID, 'sv');
    await store().show('T', 'sv');
    store().track(SID, svPreview(svMapped.step_hash, ['user']), null);
    store().track('T', svPreview('moved'), null);
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'current' });
    expect(layer(T_SV_ID)?.freshness).toEqual({ state: 'not_computed' });
    store().track('T', svPreview(svMapped.step_hash, ['user']), { steps: new Set(['sv']), runId: 'r1' });
    expect(layer(SV_ID)?.freshness).toEqual({ state: 'current' });
    expect(layer(T_SV_ID)?.freshness).toEqual({ state: 'available' });
  });

  it('draws the step a run saved in the session the run was started in', async () => {
    const answer = structuredClone(svMapped) as unknown as StepArtifacts;
    answer.step_id = 'dive_mask_sparse';
    answer.echogram.resolution = { ...answer.echogram.resolution, step: 'dive_mask_sparse' };
    mockApi({ 'GET /api/sessions/T/steps/dive_mask_sparse/artifacts': answer });
    store().activate('T');
    store().runStarted('T', 'dive_mask_sparse', 'r1');
    const instances = { count: null, done: 0, computed: 0, cached: 0, skipped: 0 };
    store().runFinished(
      {
        ...initialRunState('r1'),
        started: { targets: ['dive_mask_sparse'] } as unknown as RunStartedData,
        finished: { status: 'completed', seconds: 1, manifest: null, executed: [], skipped: [], pruned: [], untargeted: [], error_type: null, message: null },
        steps: {
          dive_mask_sparse: { status: 'computed', tier: null, hash: null, planned: null, index: 0, total: 0, instances, seconds: null, error: null, chain: null },
        },
      },
      null,
    );
    await vi.waitFor(() => expect(layer('T/dive_mask_sparse/ds_Sv/Sv')).toBeDefined());
  });

  it('drops settings the panel refuses', async () => {
    window.localStorage.setItem(
      LAYERS_KEY,
      JSON.stringify({
        layers: [{ sid: SID, step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'x', addedAt: 1 }],
        settings: { version: 9, layers: [] },
      }),
    );
    mockApi({ [SV_CHOSEN]: svMapped });
    store().restore();
    const panel = fakePanel();
    panel.applySettings.mockRejectedValueOnce(new Error('version 9'));
    await store().attach(panel);
    expect(panel.addSource).toHaveBeenCalledTimes(1);
    expect(store().settings).toBeNull();
    expect(saved().settings).toBeNull();
  });

  it('resolves a layer again when the server restarted and forgot its mounts', async () => {
    const fetchMock = mockApi({ [SV]: svMapped, [SV_CHOSEN]: svMapped });
    await store().show(SID, 'sv');
    age();
    const panel = fakePanel();
    panel.addSource.mockRejectedValueOnce(new Error('gone'));
    await store().attach(panel);
    await vi.waitFor(() => expect(panel.addSource).toHaveBeenCalledTimes(2));
    expect(fetchMock).toHaveBeenLastCalledWith(`/api/sessions/S/steps/sv/artifacts?output=ds_Sv&var=Sv`, expect.anything());
    await vi.waitFor(() => expect(layer(SV_ID)?.state).toBe('shown'));
  });

  it('keeps the layers when the pane closes, to draw them again when it opens', async () => {
    mockApi({ [SV]: svMapped });
    const first = fakePanel();
    await store().attach(first);
    await store().show(SID, 'sv');
    await settle();
    store().detach();
    expect(store().panel).toBeNull();
    expect(layer(SV_ID)?.state).toBe('loading');
    const second = fakePanel();
    await store().attach(second);
    expect(second.addSource).toHaveBeenCalledWith(SV_ID, expect.anything());
  });
});

describe('a run to a step that is not saved', () => {
  /** A finished run; by default one asked to run to the step, which pins it. */
  function finished(runId: string, step: string, status: 'computed' | 'failed', targets = [step]): RunState {
    const instances = { count: null, done: 0, computed: 0, cached: 0, skipped: 0 };
    return {
      ...initialRunState(runId),
      started: { targets } as unknown as RunStartedData,
      finished: { status: 'completed', seconds: 1, manifest: null, executed: [], skipped: [], pruned: [], untargeted: [], error_type: null, message: null },
      finishedAt: 100,
      steps: { [step]: { status, tier: null, hash: null, planned: null, index: 0, total: 0, instances, seconds: null, error: null, chain: null } },
    };
  }

  /** The preview a run was started with, saying how it treats one step. */
  function startPreview(step: string, checkpoint: string, pinned = false): Preview {
    return { steps: [{ step_id: step, checkpoint, pinned, held_by: [] }] } as unknown as Preview;
  }

  function savedAnswer(step: string): StepArtifacts {
    const saved = structuredClone(svMapped) as unknown as StepArtifacts;
    saved.step_id = step;
    saved.echogram.resolution = { ...saved.echogram.resolution, step };
    return saved;
  }

  it('draws the step when its run saved it', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    await store().show(SID, 'dive_mask_sparse');
    store().runStarted(SID, 'dive_mask_sparse', 'r1');
    expect(store().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: 'r1' });

    store().runFinished(finished('r0', 'dive_mask_sparse', 'computed'), null);
    expect(store().pendingRun?.runId).toBe('r1');

    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': savedAnswer('dive_mask_sparse') });
    store().runFinished(finished('r1', 'dive_mask_sparse', 'computed'), null);
    expect(store().pendingRun).toBeNull();
    await vi.waitFor(() => expect(layer('S/dive_mask_sparse/ds_Sv/Sv')).toBeDefined());
  });

  it('draws a step the start preview said the run would save, though not a target', async () => {
    mockApi({ 'GET /api/sessions/S/steps/sv/artifacts': savedAnswer('sv') });
    store().runStarted(SID, 'sv', 'r1');
    store().runFinished(finished('r1', 'sv', 'computed', ['later']), startPreview('sv', 'will-save'));
    await vi.waitFor(() => expect(layer(SV_ID)).toBeDefined());
  });

  it('says so when the run did not save it: failed, or computed in memory only', () => {
    store().runStarted(SID, 'dive_mask_sparse', 'r1');
    store().runFinished(finished('r1', 'dive_mask_sparse', 'failed'), null);
    expect(store().message).toBe('The run to dive_mask_sparse ended (completed) without saving it.');

    store().runStarted(SID, 'dive_mask_sparse', 'r2');
    const memory = startPreview('dive_mask_sparse', 'memory-only');
    store().runFinished(finished('r2', 'dive_mask_sparse', 'computed', ['later']), memory);
    expect(store().message).toBe('The run to dive_mask_sparse ended (completed) without saving it.');
  });

  it('asks to wait when another unsaved step is shown while the run goes, then clears that', async () => {
    mockApi({
      'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse,
      'GET /api/sessions/S/steps/other/artifacts': { ...diveMaskSparse, step_id: 'other' },
    });
    await store().show(SID, 'dive_mask_sparse');
    store().runStarted(SID, 'dive_mask_sparse', 'r1');
    await store().show(SID, 'other');
    expect(store().message).toBe('A run to dive_mask_sparse is going. Click the eye on other again when it finishes.');
    expect(store().pendingRun).toEqual({ sid: SID, step: 'dive_mask_sparse', runId: 'r1' });

    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': savedAnswer('dive_mask_sparse') });
    store().runFinished(finished('r1', 'dive_mask_sparse', 'computed'), null);
    expect(store().message).toBeNull();
    await vi.waitFor(() => expect(layer('S/dive_mask_sparse/ds_Sv/Sv')).toBeDefined());
    expect(store().message).toBeNull();
  });
});

describe('experience test fixes', () => {
  it('resolves layers again on a header error from an older server (not_found)', async () => {
    mockApi({ [SV]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await settle();
    age();
    mockApi({ [SV_CHOSEN]: svMapped });
    store().onPanelError(new Error('not_found'));
    await vi.waitFor(() => expect(panel.retry).toHaveBeenCalledTimes(1));
  });

  it('does not offer a run for a step the recipe never saves', async () => {
    const fetchMock = mockApi({ 'GET /api/sessions/S/steps/label_all_points/artifacts': { ...diveMaskSparse, step_id: 'label_all_points' } });
    await store().show(SID, 'label_all_points', { checkpoint: 'never' });
    expect(store().pendingRun).toBeNull();
    expect(store().message).toBe("label_all_points is never saved (checkpoint: never in the recipe), so it can't be drawn.");
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('does not offer the run again when the run that should have saved the step did not', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    await store().show(SID, 'dive_mask_sparse', { afterRun: true });
    expect(store().pendingRun).toBeNull();
    expect(store().message).toBe('The run to dive_mask_sparse finished, but dive_mask_sparse was not saved.');
  });

  it('says why a server without EchogramGL cannot draw, before any offer to run', async () => {
    const unsaved = structuredClone(diveMaskSparse) as unknown as StepArtifacts;
    unsaved.echogram = { available: false, reason: 'aa-si-echogram-gl is not installed', resolution: null };
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': unsaved });
    await store().show(SID, 'dive_mask_sparse');
    expect(store().pendingRun).toBeNull();
    expect(store().message).toBe('aa-si-echogram-gl is not installed');
  });

  it('adds no rows without WebGPU, keeping the saved ones for another browser', async () => {
    mockApi({ [SV]: svMapped });
    await store().show(SID, 'sv');
    expect(store().layers).toHaveLength(1);
    const saved = window.localStorage.getItem(LAYERS_KEY);
    store().gpuUnavailable();
    expect(store().layers).toEqual([]);
    expect(store().message).toBe(NEEDS_WEBGPU);
    expect(window.localStorage.getItem(LAYERS_KEY)).toBe(saved);

    const fetchMock = mockApi({ [SV]: svMapped });
    await store().show(SID, 'sv');
    expect(store().layers).toEqual([]);
    expect(store().message).toBe(NEEDS_WEBGPU);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('draws the default by itself after a run, for a step with several variables', async () => {
    mockApi({ 'GET /api/sessions/S/steps/embed_results/artifacts': embed });
    await store().show(SID, 'embed_results', { afterRun: true });
    expect(store().choice).toBeNull();
    expect(store().layers.map((entry) => entry.id)).toEqual(['S/embed_results/ds_normalized/Sv']);
  });

  it('gives every row of a step its own label', async () => {
    const gridded = withResolution(embed, { output: 'gridded_results', var: '_da' });
    mockApi({
      'GET /api/sessions/S/steps/embed_results/artifacts': embed,
      'GET /api/sessions/S/steps/embed_results/artifacts?output=gridded_results&var=_da': gridded,
    });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'embed_results');
    const choice = store().choice!;
    await store().addChoice(choice.choices[2]);
    await settle();
    expect(store().layers.map((entry) => entry.label)).toEqual(['embed_results']);
    await store().addChoice(choice.choices[3]);
    await settle();
    expect(store().layers.map((entry) => entry.label)).toEqual(['embed_results:Sv', 'embed_results:gridded_results']);
    const names = panel.addSource.mock.calls.map((call) => ((call as unknown[])[1] as PieceSetSpec).name);
    expect(names).toEqual(['embed_results', 'embed_results:gridded_results']);
  });
});

describe('lines', () => {
  const FIT_ID = 'S/sv/ds_Sv/dive_fit';
  const LINES = 'GET /api/sessions/S/steps/lines/artifacts';
  const LINES_VALUE = `${LINES}?output=ds`;
  const LINE_A_ID = 'S/lines/ds/line_a';
  const LINE_A = `GET /echogram/api/line/${svLine.echogram.resolution.instances[0].mount}/line_a`;
  const instances = svMapped.echogram.resolution.instances;

  /** What the line route answers: two pings, the second a gap. */
  const linePayload = (name: string, at: number) => ({
    name,
    longName: null,
    units: 'm',
    verticalReference: 'depth',
    pingTime: [at * 1e9, (at + 1) * 1e9],
    value: [10 + at, null],
  });

  /** Each mapped instance's line route. */
  const lineRoutes = (name: string) =>
    Object.fromEntries(
      instances.map((instance, at) => [`GET /echogram/api/line/${instance.mount}/${name}`, linePayload(name, at)]),
    );

  /** The mapped fixture with a fitted depth and its upper band listed beside Sv. */
  function svWithLines(): StepArtifacts {
    const copy = structuredClone(svMapped) as unknown as StepArtifacts;
    const resolution = copy.echogram.resolution as unknown as Resolution;
    const line = svLine.echogram.resolution.outputs[0].variables[1];
    resolution.outputs![0].variables!.push(
      { ...line, kind: 'line', verticalReference: 'depth', name: 'dive_fit' },
      { ...line, kind: 'line', verticalReference: 'depth', name: 'dive_u99' },
    );
    return copy;
  }

  /** The specs the panel was last handed. */
  const handed = (panel: ReturnType<typeof fakePanel>): LineSpec[] => panel.setLines.mock.lastCall?.[0] ?? [];

  /** Draw the mapped step's fitted depth from the chooser. */
  async function fitShown() {
    const fetchMock = mockApi({ [SV]: svWithLines(), ...lineRoutes('dive_fit'), ...lineRoutes('dive_u99') });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    const offered = store().choice!.choices;
    await store().addChoice(offered[1]);
    await settle();
    return { panel, fetchMock, offered };
  }

  it("draws a line over its step's value, adding the default value first, one spec per instance", async () => {
    const { panel, fetchMock, offered } = await fitShown();
    expect(offered.map((choice) => choice.var)).toEqual(['Sv', 'dive_fit', 'dive_u99']);
    expect(panel.sourceIds).toEqual([SV_ID]);
    expect(layer(FIT_ID)).toMatchObject({
      kind: 'line',
      label: 'sv:dive_fit',
      state: 'shown',
      color: LINE_COLORS.fit,
      width: LINE_WIDTH,
      visible: true,
    });
    const specs = handed(panel);
    expect(specs.map((spec) => spec.id)).toEqual(instances.map((instance) => `${FIT_ID}#${instance.id}`));
    expect(specs.map((spec) => spec.label)).toEqual(['sv:dive_fit (10)', 'sv:dive_fit (20)', 'sv:dive_fit (30)']);
    expect(specs[0]).toMatchObject({ color: LINE_COLORS.fit, width: LINE_WIDTH, visible: true, verticalRef: 'depth' });
    expect(Array.from(specs[1].value)).toEqual([11, NaN]);
    // The line rides on the value resolution in hand: the line is never asked for as a variable.
    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls.filter((url) => url.includes('/artifacts'))).toEqual(['/api/sessions/S/steps/sv/artifacts']);

    // A second line adds no second value.
    await store().addChoice(offered[2]);
    await settle();
    expect(panel.addSource).toHaveBeenCalledTimes(1);
    expect(handed(panel).map((spec) => spec.color)).toEqual([...Array(3).fill(LINE_COLORS.fit), ...Array(3).fill(LINE_COLORS.band)]);
  });

  it('hands the panel a new style and keeps it, and stops drawing a removed line', async () => {
    const { panel } = await fitShown();
    store().setLineStyle(FIT_ID, { color: '#123456', visible: false });
    expect(handed(panel).map((spec) => [spec.color, spec.visible])).toEqual(Array(3).fill(['#123456', false]));
    store().setLineStyle(FIT_ID, { width: 4 });
    expect(handed(panel).every((spec) => spec.width === 4 && spec.color === '#123456')).toBe(true);
    const saved = JSON.parse(window.localStorage.getItem(LAYERS_KEY)!).layers;
    expect(saved[1]).toMatchObject({ kind: 'line', variable: 'dive_fit', color: '#123456', width: 4, visible: false });

    store().remove(FIT_ID);
    expect(handed(panel)).toEqual([]);
    expect(panel.removeSource).not.toHaveBeenCalled();
    expect(panel.sourceIds).toEqual([SV_ID]);
    expect(store().lines).toEqual({});
  });

  it('restores a saved line by its output alone, and hands it to a panel opened again without fetching', async () => {
    const row = { sid: SID, kind: 'line', step: 'lines', output: 'ds', variable: 'line_a', color: '#123456', width: 3 };
    window.localStorage.setItem(LAYERS_KEY, JSON.stringify({ layers: [row], settings: null }));
    const fetchMock = mockApi({ [LINES_VALUE]: svLine, [LINE_A]: linePayload('line_a', 0) });
    store().restore();
    const panel = fakePanel();
    await store().attach(panel);
    await settle();
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/S/steps/lines/artifacts?output=ds', expect.anything());
    // Saved without its value row, so the row says it needs one.
    const restored = { kind: 'line', color: '#123456', width: 3, visible: true, state: 'error', error: NEEDS_VALUE };
    expect(layer(LINE_A_ID)).toMatchObject(restored);
    expect(handed(panel).map((spec) => [spec.id, spec.label])).toEqual([
      [`${LINE_A_ID}#${svLine.echogram.resolution.instances[0].id}`, 'lines:line_a'],
    ]);

    store().detach();
    const again = fakePanel();
    const calls = fetchMock.mock.calls.length;
    await store().attach(again);
    expect(fetchMock.mock.calls.length).toBe(calls);
    expect(handed(again)).toHaveLength(1);
    expect(layer(LINE_A_ID)).toMatchObject(restored);

    store().removeSession(SID);
    expect(handed(again)).toEqual([]);
    expect(store().lines).toEqual({});
  });

  it('fetches a line again when the server forgot its mount', async () => {
    let asked = 0;
    const gone = () => new Response(JSON.stringify({ error: 'gone', message: 'unknown mount' }), { status: 410 });
    const fetchMock = mockApi({
      [LINES]: svLine,
      [LINES_VALUE]: svLine,
      [LINE_A]: () => (++asked === 1 ? gone() : linePayload('line_a', 0)),
    });
    await store().show(SID, 'lines');
    await store().addChoice(store().choice!.choices[1]);
    expect(store().layers.map((entry) => entry.id)).toEqual(['S/lines/ds/Sv', LINE_A_ID]);
    age();
    const panel = fakePanel();
    await store().attach(panel);
    await vi.waitFor(() => expect(asked).toBe(2));
    await vi.waitFor(() => expect(layer(LINE_A_ID)?.state).toBe('shown'));
    expect(fetchMock).toHaveBeenCalledWith('/api/sessions/S/steps/lines/artifacts?output=ds', expect.anything());
    expect(handed(panel)).toHaveLength(1);
  });

  it('says which line is missing on a 404, and does not fetch the step again', async () => {
    // The line route is not mocked, so it answers 404 not_found.
    const fetchMock = mockApi({ [LINES]: svLine, [LINES_VALUE]: svLine });
    await store().show(SID, 'lines');
    await store().addChoice(store().choice!.choices[1]);
    age();
    const panel = fakePanel();
    await store().attach(panel);
    await settle();
    expect(layer(LINE_A_ID)).toMatchObject({ state: 'error', error: "lines has no line 'line_a' in one of its files." });
    const urls = fetchMock.mock.calls.map(([url]) => String(url));
    expect(urls.filter((url) => url.includes('/artifacts'))).toEqual(['/api/sessions/S/steps/lines/artifacts']);
    expect(urls.filter((url) => url.includes('/echogram/api/line/'))).toHaveLength(1);
    expect(handed(panel)).toEqual([]);
  });

  it('adds the value once when several lines are added at once', async () => {
    mockApi({ [SV]: svWithLines(), ...lineRoutes('dive_fit'), ...lineRoutes('dive_u99') });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    const offered = store().choice!.choices;
    await Promise.all([store().addChoice(offered[1]), store().addChoice(offered[2])]);
    await settle();
    expect(panel.addSource).toHaveBeenCalledTimes(1);
    expect(panel.retry).not.toHaveBeenCalled();
    expect(panel.replaceSource).not.toHaveBeenCalled();
    expect(store().layers.map((entry) => [entry.id, entry.state]).sort()).toEqual([
      [SV_ID, 'shown'],
      [FIT_ID, 'shown'],
      ['S/sv/ds_Sv/dive_u99', 'shown'],
    ]);
  });

  it('says a line needs a value once the last value row goes, and draws it again when one comes', async () => {
    const { panel, offered } = await fitShown();
    store().remove(SV_ID);
    expect(layer(FIT_ID)).toMatchObject({ state: 'error', error: NEEDS_VALUE });
    await store().addChoice(offered[0]);
    await settle();
    expect(layer(FIT_ID)).toMatchObject({ state: 'shown', error: null });
    expect(panel.sourceIds).toEqual([SV_ID]);

    // A value whose last layer the user removed in the panel still holds the lines.
    store().onPanelLayersChange([]);
    expect(store().bare).toEqual([SV_ID]);
    expect(layer(FIT_ID)).toMatchObject({ state: 'shown', error: null });
    expect(handed(panel)).toHaveLength(instances.length);
  });

  it('says so when a refresh finds the step no longer has the line', async () => {
    mockApi({ [LINES]: svLine, [LINE_A]: linePayload('line_a', 0) });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'lines');
    await store().addChoice(store().choice!.choices[1]);
    await settle();
    const without = structuredClone(svLine) as unknown as StepArtifacts;
    const resolution = without.echogram.resolution as unknown as Resolution;
    resolution.outputs![0].variables = resolution.outputs![0].variables!.slice(0, 1);
    mockApi({ [LINES_VALUE]: without });
    await store().refresh(LINE_A_ID);
    expect(layer(LINE_A_ID)).toMatchObject({ state: 'error', error: 'lines has no line line_a in ds any more.' });
  });
});
