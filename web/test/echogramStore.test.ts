import type { PieceSetSpec, Resolution, ViewSettings } from 'aa-si-echogram-gl';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { StepArtifacts } from '../src/api/models';
import { NEEDS_WEBGPU, storageKey, useEchogramStore } from '../src/echogram/store';
import type { Preview } from '../src/api/models';
import type { RunStartedData } from '../src/runs/events';
import { initialRunState, type RunState } from '../src/runs/reducer';
import diveMaskSparse from './fixtures/artifacts.dive_mask_sparse.json';
import diveSv from './fixtures/artifacts.dive_sv.json';
import embed from './fixtures/artifacts.embed_results.json';
import svPlain from './fixtures/artifacts.sv.json';
import svMapped from './fixtures/artifacts.sv_mapped.json';
import { errorResponse, mockApi } from './helpers';

const SID = 'S';
const SV = 'GET /api/sessions/S/steps/sv/artifacts';
const SV_ID = 'sv/ds_Sv/Sv';
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
  };
  return panel;
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

beforeEach(() => {
  useEchogramStore.setState({ sid: null, gpu: 'unknown', layers: [], panel: null, settings: null, choice: null, pendingRun: null, message: null, busy: null });
  store().resetFor(SID);
});

afterEach(() => {
  vi.unstubAllGlobals();
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
    expect(layer('embed_results/ds_normalized/ml_dataset_hdbscan_results_grid')?.label).toBe(
      'embed_results:ml_dataset_hdbscan_results_grid',
    );
    // The chooser stays open for the MVBS under the labels, which needs no second request.
    const calls = fetchMock.mock.calls.length;
    await store().addChoice(choice.choices[2]);
    await settle();
    expect(fetchMock.mock.calls.length).toBe(calls);
    expect(panel.sourceIds).toEqual(['embed_results/ds_normalized/ml_dataset_hdbscan_results_grid', 'embed_results/ds_normalized/Sv']);
    store().cancelChoice();
    expect(store().choice).toBeNull();
  });

  it('draws the Sv of a step whose dataset also lists its depth, with the empty instance counted', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_sv/artifacts': diveSv });
    await store().show(SID, 'dive_sv');
    expect(store().choice).toBeNull();
    expect(layer('dive_sv/ds_Sv/Sv')?.resolution?.empty).toBe(1);
  });

  it('offers to run to a step that is not saved', async () => {
    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': diveMaskSparse });
    await store().show(SID, 'dive_mask_sparse');
    expect(store().pendingRun).toEqual({ step: 'dive_mask_sparse', runId: null });
    expect(store().message).toMatch(/dive_mask_sparse has not been saved\. Run to dive_mask_sparse\?/);
    expect(store().layers).toEqual([]);
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

  it('retries on refresh when the checkpoint is the same, and clears the mark', async () => {
    const panel = await shown();
    store().markOutOfDate({ sv: 'moved' }, null);
    expect(layer(SV_ID)?.outOfDate).toBe(true);
    mockApi({ [SV_CHOSEN]: svMapped });
    await store().refresh(SV_ID);
    await settle();
    expect(panel.retry).toHaveBeenCalledTimes(1);
    expect(panel.replaceSource).not.toHaveBeenCalled();
    expect(layer(SV_ID)?.outOfDate).toBe(false);
  });

  it('replaces the source on refresh when a run wrote it again', async () => {
    const panel = await shown();
    mockApi({ [SV_CHOSEN]: withResolution(svMapped, { createdAt: '2026-10-20T00:00:00+00:00' }) });
    await store().refresh(SV_ID);
    await settle();
    expect(panel.replaceSource).toHaveBeenCalledWith(SV_ID, expect.objectContaining({ pieces: expect.any(Array) }));
    expect(layer(SV_ID)?.resolution?.createdAt).toBe('2026-10-20T00:00:00+00:00');
  });

  it('marks the row when the step is not saved any more', async () => {
    await shown();
    mockApi({ [SV_CHOSEN]: diveMaskSparse });
    await store().refresh(SV_ID);
    expect(layer(SV_ID)).toMatchObject({ state: 'error', error: 'sv is not saved any more.' });
  });

  it('is removed from the panel with its row', async () => {
    const panel = await shown();
    store().remove(SV_ID);
    await vi.waitFor(() => expect(panel.removeSource).toHaveBeenCalledWith(SV_ID));
    expect(store().layers).toEqual([]);
  });

  it('goes when the user removes its last layer in the panel', async () => {
    const panel = await shown();
    store().onPanelLayersChange([{ id: 0, source: 'other' } as never]);
    await vi.waitFor(() => expect(panel.removeSource).toHaveBeenCalledWith(SV_ID));
    expect(store().layers).toEqual([]);
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

    release = hold();
    const removed = store().refresh(SV_ID);
    await vi.waitFor(() => expect(panel.retry).toHaveBeenCalledTimes(2));
    store().onPanelLayersChange([]);
    expect(store().layers).toHaveLength(1);
    release();
    await removed;
    await vi.waitFor(() => expect(store().layers).toEqual([]));
    expect(panel.removeSource).toHaveBeenCalledWith(SV_ID);
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
  it('saves the layers and settings per session and restores them on return', async () => {
    mockApi({ [SV]: svMapped, [SV_CHOSEN]: svMapped });
    const panel = fakePanel();
    await store().attach(panel);
    await store().show(SID, 'sv');
    await settle();
    const settings = { version: 1, layers: [{ source: SV_ID, channel: 1 }] } as unknown as ViewSettings;
    panel.settingsObject = settings;
    store().captureSettings();
    expect(JSON.parse(window.localStorage.getItem(storageKey(SID))!).layers).toEqual([
      expect.objectContaining({ step: 'sv', output: 'ds_Sv', variable: 'Sv' }),
    ]);

    store().resetFor('T');
    expect(store().layers).toEqual([]);
    expect(store().settings).toBeNull();
    await vi.waitFor(() => expect(panel.removeSource).toHaveBeenCalledWith(SV_ID));

    store().resetFor(SID);
    await vi.waitFor(() => expect(panel.sourceIds).toEqual([SV_ID]));
    await vi.waitFor(() => expect(panel.applySettings).toHaveBeenCalled());
    expect(panel.applySettings.mock.calls[0][0]).toMatchObject({ layers: [{ source: SV_ID, channel: 1 }] });
    expect(layer(SV_ID)?.state).toBe('shown');
  });

  it('drops settings the panel refuses', async () => {
    window.localStorage.setItem(
      storageKey('R'),
      JSON.stringify({ layers: [{ step: 'sv', output: 'ds_Sv', variable: 'Sv', label: 'sv', stepHash: 'x', addedAt: 1 }], settings: { version: 9, layers: [] } }),
    );
    mockApi({ 'GET /api/sessions/R/steps/sv/artifacts?output=ds_Sv&var=Sv': svMapped });
    store().resetFor('R');
    const panel = fakePanel();
    panel.applySettings.mockRejectedValueOnce(new Error('version 9'));
    await store().attach(panel);
    expect(panel.addSource).toHaveBeenCalledTimes(1);
    expect(store().settings).toBeNull();
    expect(JSON.parse(window.localStorage.getItem(storageKey('R'))!).settings).toBeNull();
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
    store().runStarted('dive_mask_sparse', 'r1');
    expect(store().pendingRun).toEqual({ step: 'dive_mask_sparse', runId: 'r1' });

    store().runFinished(finished('r0', 'dive_mask_sparse', 'computed'), null);
    expect(store().pendingRun?.runId).toBe('r1');

    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': savedAnswer('dive_mask_sparse') });
    store().runFinished(finished('r1', 'dive_mask_sparse', 'computed'), null);
    expect(store().pendingRun).toBeNull();
    await vi.waitFor(() => expect(layer('dive_mask_sparse/ds_Sv/Sv')).toBeDefined());
  });

  it('draws a step the start preview said the run would save, though not a target', async () => {
    mockApi({ 'GET /api/sessions/S/steps/sv/artifacts': savedAnswer('sv') });
    store().runStarted('sv', 'r1');
    store().runFinished(finished('r1', 'sv', 'computed', ['later']), startPreview('sv', 'will-save'));
    await vi.waitFor(() => expect(layer(SV_ID)).toBeDefined());
  });

  it('says so when the run did not save it: failed, or computed in memory only', () => {
    store().runStarted('dive_mask_sparse', 'r1');
    store().runFinished(finished('r1', 'dive_mask_sparse', 'failed'), null);
    expect(store().message).toBe('The run to dive_mask_sparse ended (completed) without saving it.');

    store().runStarted('dive_mask_sparse', 'r2');
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
    store().runStarted('dive_mask_sparse', 'r1');
    await store().show(SID, 'other');
    expect(store().message).toBe('A run to dive_mask_sparse is going. Click the eye on other again when it finishes.');
    expect(store().pendingRun).toEqual({ step: 'dive_mask_sparse', runId: 'r1' });

    mockApi({ 'GET /api/sessions/S/steps/dive_mask_sparse/artifacts': savedAnswer('dive_mask_sparse') });
    store().runFinished(finished('r1', 'dive_mask_sparse', 'computed'), null);
    expect(store().message).toBeNull();
    await vi.waitFor(() => expect(layer('dive_mask_sparse/ds_Sv/Sv')).toBeDefined());
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
    await store().show(SID, 'label_all_points', { neverSaved: true });
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
    const saved = window.localStorage.getItem(storageKey(SID));
    store().gpuUnavailable();
    expect(store().layers).toEqual([]);
    expect(store().message).toBe(NEEDS_WEBGPU);
    expect(window.localStorage.getItem(storageKey(SID))).toBe(saved);

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
    expect(store().layers.map((entry) => entry.id)).toEqual(['embed_results/ds_normalized/Sv']);
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
