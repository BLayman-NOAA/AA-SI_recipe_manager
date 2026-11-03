import { choices, type Resolution, type ViewSettings } from 'aa-si-echogram-gl';
import { describe, expect, it } from 'vitest';
import type { Preview, StepArtifacts, StepPreview } from '../src/api/models';
import {
  asResolution,
  computedSteps,
  foundText,
  goneText,
  isGone,
  layerFreshness,
  layerId,
  missText,
  restoredLayer,
  savedLayer,
  saveKind,
  settingsFor,
  sourcesWithoutLayers,
  tierLetters,
  unsavedText,
  withFreshness,
  withLabels,
  type EchogramLayer,
} from '../src/echogram/layers';
import { actionsFor } from '../src/echogram/LayerList';
import { initialRunState, type RunState } from '../src/runs/reducer';
import diveSv from './fixtures/artifacts.dive_sv.json';
import embed from './fixtures/artifacts.embed_results.json';
import svMapped from './fixtures/artifacts.sv_mapped.json';

const resolutionOf = (found: unknown) => asResolution((found as StepArtifacts).echogram.resolution);

function layer(step: string, overrides: Partial<EchogramLayer> = {}): EchogramLayer {
  return {
    id: layerId(step, 'ds_Sv', 'Sv'),
    step,
    output: 'ds_Sv',
    variable: 'Sv',
    label: step,
    stepHash: 'h1',
    resolution: null,
    state: 'shown',
    error: null,
    freshness: { state: 'unknown' },
    addedAt: 1000,
    drawnAfter: null,
    ...overrides,
  };
}

describe('layer ids and resolutions', () => {
  it('names a layer by step, output and variable', () => {
    expect(layerId('dive_sv', 'ds_Sv', 'Sv')).toBe('dive_sv/ds_Sv/Sv');
    expect(layerId('pyramid', 'store', null)).toBe('pyramid/store');
  });

  it('accepts a resolution and rejects anything else', () => {
    expect(resolutionOf(svMapped).kind).toBe('dataset');
    expect(() => asResolution(null)).toThrow(/no echogram resolution/);
    expect(() => asResolution('found')).toThrow(/no echogram resolution/);
    expect(() => asResolution([1])).toThrow(/no echogram resolution/);
    expect(() => asResolution({ status: 'found' })).toThrow(/no status or step/);
  });

  it('offers what the server lists, which leaves out the vertical coordinates', () => {
    expect(choices(resolutionOf(diveSv)).map((choice) => choice.var)).toEqual(['Sv']);
    expect(choices(resolutionOf(embed)).map((choice) => `${choice.output}:${choice.var}`)).toEqual([
      'ds_normalized:ml_dataset_hdbscan_results_grid',
      'ds_normalized:ml_dataset_valid_mask',
      'ds_normalized:Sv',
      'gridded_results:_da',
    ]);
  });

  it('describes what a layer draws: datasets, empty instances, tiers', () => {
    expect(foundText(resolutionOf(svMapped))).toBe('3 datasets');
    expect(foundText(resolutionOf(diveSv))).toBe('2 datasets, 1 empty');
    expect(foundText({ ...resolutionOf(svMapped), kind: 'pyramid' })).toBe('pyramid');
    expect(foundText(null)).toBe('');
    expect(tierLetters(resolutionOf(svMapped))).toBe('U');
    expect(tierLetters(null)).toBe('');
  });

  it('round trips a layer through what is saved across a reload', () => {
    const saved = savedLayer(layer('sv', { resolution: resolutionOf(svMapped), freshness: { state: 'available' } }));
    expect(Object.keys(saved).sort()).toEqual(['addedAt', 'label', 'output', 'step', 'stepHash', 'variable']);
    expect(restoredLayer(saved)).toEqual(layer('sv', { state: 'loading' }));
  });
});

/** A step's preview entry; by default at hash h1, held by nothing. */
function stepAt(step: string, overrides: Partial<StepPreview> = {}): StepPreview {
  return {
    step_id: step,
    step_hash: 'h1',
    held_by: [],
    instances: null,
    instances_held: {},
    fanned_out: false,
    planned: 'compute',
    tier: null,
    note: null,
    checkpoint: 'will-save',
    recipe_checkpoint: 'always',
    pinned: false,
    ...overrides,
  };
}

const previewOf = (...steps: StepPreview[]) => ({ steps }) as unknown as Preview;

describe('layer freshness', () => {
  const computed = (steps: string[], runId: string) => ({ steps: new Set(steps), runId });
  const state = (entry: StepPreview | undefined, ran: ReturnType<typeof computed> | null = null, at = layer('a')) =>
    layerFreshness(at, entry, ran);

  it('is current at the drawn hash with no later run, and unknown without a preview entry', () => {
    expect(state(stepAt('a', { held_by: ['user'] }))).toEqual({ state: 'current' });
    expect(state(stepAt('a'), computed(['b'], 'r1'))).toEqual({ state: 'current' });
    expect(state(undefined)).toEqual({ state: 'unknown' });
  });

  it('is available when a run saved the step after it was drawn, at the same hash', () => {
    expect(state(stepAt('a', { held_by: ['user'] }), computed(['a'], 'r1'))).toEqual({ state: 'available' });
    expect(state(stepAt('a'), computed(['a'], 'r1'), layer('a', { drawnAfter: 'r1' }))).toEqual({ state: 'current' });
    expect(state(stepAt('a'), computed(['a'], 'r2'), layer('a', { drawnAfter: 'r1' }))).toEqual({ state: 'available' });
  });

  it('is partial for a fanned out step held for some files only, at the drawn hash too', () => {
    const mapped = { fanned_out: true, instances: 3, instances_held: { user: 2 } };
    expect(state(stepAt('a', mapped))).toEqual({ state: 'partial', held: 2, of: 3 });
    expect(state(stepAt('a', mapped), computed(['a'], 'r1'))).toEqual({ state: 'partial', held: 2, of: 3 });
  });

  it('is available when a tier holds another hash than the drawn one, as after a value is changed back', () => {
    expect(state(stepAt('a', { step_hash: 'h0', held_by: ['user'] }))).toEqual({ state: 'available' });
    expect(state(stepAt('a', { step_hash: 'h0', held_by: ['survey'] }))).toEqual({ state: 'available' });
  });

  it('is not computed when nothing holds the new hash', () => {
    expect(state(stepAt('a', { step_hash: 'h2' }))).toEqual({ state: 'not_computed' });
  });

  it('is partial for a fanned out step held for some files only, and available once all are', () => {
    const mapped = { step_hash: 'h2', fanned_out: true, instances: 3 };
    expect(state(stepAt('a', { ...mapped, instances_held: { user: 2, survey: 0 } }))).toEqual({ state: 'partial', held: 2, of: 3 });
    expect(state(stepAt('a', { ...mapped, instances_held: { user: 0 } }))).toEqual({ state: 'not_computed' });
    expect(state(stepAt('a', { ...mapped, held_by: ['user'], instances_held: { user: 3 } }))).toEqual({ state: 'available' });
  });

  it('keeps the same row object when its freshness is unchanged', () => {
    const rows = [layer('a', { freshness: { state: 'current' } }), layer('b')];
    const next = withFreshness(rows, previewOf(stepAt('a'), stepAt('b', { step_hash: 'h2' })), null);
    expect(next[0]).toBe(rows[0]);
    expect(next[1].freshness).toEqual({ state: 'not_computed' });
    expect(withFreshness(next, null, null).map((row) => row.freshness.state)).toEqual(['unknown', 'unknown']);
  });
});

describe('unsaved step messages', () => {
  it('tells a checkpoint from a step kept in memory or pinned only as a target, and never', () => {
    expect(saveKind(stepAt('a'))).toBe('checkpoint');
    expect(saveKind(stepAt('a', { checkpoint: 'memory-only', recipe_checkpoint: null }))).toBe('memory');
    expect(saveKind(stepAt('a', { checkpoint: 'will-save', pinned: true, recipe_checkpoint: null }))).toBe('memory');
    expect(saveKind(stepAt('a', { recipe_checkpoint: 'never', checkpoint: 'memory-only' }))).toBe('never');
    expect(saveKind(undefined, 'never')).toBe('never');
    expect(saveKind(undefined, 'always')).toBe('checkpoint');
    expect(saveKind(undefined, null)).toBe('unknown');
  });

  it('takes a step the recipe checkpoints as a checkpoint, though this run leaves it out', () => {
    for (const planned of ['untargeted', 'pruned', 'skipped']) {
      const step = stepAt('a', { planned, checkpoint: 'memory-only', recipe_checkpoint: 'always' });
      expect(saveKind(step)).toBe('checkpoint');
      expect(unsavedText('a', saveKind(step))).not.toMatch(/isn't a checkpoint/);
    }
    expect(saveKind(stepAt('a', { checkpoint: 'will-save', pinned: true, recipe_checkpoint: 'always' }))).toBe('checkpoint');
  });

  it('words each case', () => {
    expect(unsavedText('sv', 'memory')).toBe(
      "sv isn't a checkpoint, so only a run to it saves it. Run to sv to draw it, or make it a checkpoint so every run saves it.",
    );
    expect(unsavedText('sv', 'checkpoint')).toBe('sv has no saved result yet. Run to sv to draw it.');
    expect(unsavedText('sv', 'unknown', 'no cache configured')).toBe(
      'sv has no saved result yet (no cache configured). Run to sv to draw it.',
    );
    expect(unsavedText('sv', 'never')).toBe("sv is never saved (checkpoint: never in the recipe), so it can't be drawn.");
    expect(goneText('sv')).toBe('sv is not saved any more.');
  });
});

describe('what a finished run computed', () => {

  const instances = { count: null, done: 0, computed: 0, cached: 0, skipped: 0 };
  const step = { tier: null, hash: null, planned: null, index: 0, total: 0, seconds: null, error: null, chain: null };
  const log = (seq: number, time: number) => ({ seq, time, step: null, stream: 'progress', text: '', buffered: false, instance: null });

  function finishedRun(overrides: Partial<RunState> = {}): RunState {
    return {
      ...initialRunState('r1'),
      finished: { status: 'completed', seconds: 1, manifest: null, executed: [], skipped: [], pruned: [], untargeted: [], error_type: null, message: null },
      steps: {
        a: { ...step, status: 'computed', instances },
        b: { ...step, status: 'hit-user-cache', instances },
        c: { ...step, status: 'running', instances: { ...instances, computed: 2 } },
      },
      ...overrides,
    };
  }

  it('reads what a finished run computed, and which run it was', () => {
    expect(computedSteps(initialRunState('r1'))).toBeNull();
    expect(computedSteps(finishedRun())).toEqual({ steps: new Set(['a', 'c']), runId: 'r1' });
  });

  it('leaves out a step the start preview says the run kept in memory only', () => {
    const run = finishedRun();
    const start = previewOf(stepAt('a', { checkpoint: 'memory-only' }), stepAt('c', { checkpoint: 'memory-only', pinned: true }));
    expect(computedSteps(run, start)?.steps).toEqual(new Set(['c']));
  });

  it('marks a layer drawn before the run, whatever the two clocks say', () => {
    const now = Date.now();
    // The server's clock an hour behind the browser's.
    const behind = finishedRun({ finishedAt: now / 1000 - 3600, logs: [log(1, now / 1000 - 3600)] });
    const drawnBefore = layer('a', { addedAt: now - 60000, drawnAfter: null });
    expect(layerFreshness(drawnBefore, stepAt('a'), computedSteps(behind)).state).toBe('available');
    // The server's clock an hour ahead.
    const ahead = finishedRun({ finishedAt: now / 1000 + 3600, logs: [log(1, now / 1000 + 3600)] });
    const drawnAfter = layer('a', { addedAt: now, drawnAfter: 'r1' });
    expect(layerFreshness(drawnAfter, stepAt('a'), computedSteps(ahead)).state).toBe('current');
  });
});

describe('the panel side', () => {
  it('finds sources no layer reads', () => {
    expect(sourcesWithoutLayers(['a', 'b', 'c'], [{ source: 'a' }, { source: 'c' }])).toEqual(['b']);
    expect(sourcesWithoutLayers(['a'], [])).toEqual(['a']);
  });

  it('fits saved settings to the open sources, keeping the layers of a source added since', () => {
    const base = {
      version: 1,
      level: 'auto' as const,
      pixelsPerPing: 1,
      colormap: 'viridis',
      filter: 'nearest' as const,
      xUnit: 'time',
      yUnit: 'depth',
      aspect: { mode: 'free' as const, exaggeration: 1 },
    };
    const saved: ViewSettings = {
      ...base,
      store: 'old',
      sources: [{ id: 'a', store: 'u' }, { id: 'gone', store: 'v' }],
      layers: [{ source: 'a', channel: 1 }, { source: 'gone', channel: 0 }],
    };
    const current: ViewSettings = {
      ...base,
      sources: [{ id: 'a', store: 'u2' }, { id: 'new', store: 'w' }],
      layers: [{ source: 'a', channel: 0 }, { source: 'new', channel: 0 }],
    };
    const fitted = settingsFor(saved, current, ['a', 'new']);
    expect(fitted.store).toBeUndefined();
    expect(fitted.sources).toEqual(current.sources);
    expect(fitted.layers).toEqual([{ source: 'a', channel: 1 }, { source: 'new', channel: 0 }]);
    expect(fitted.colormap).toBe('viridis');
  });

  it('knows a forgotten mount when it sees one', () => {
    expect(isGone(new Error('gone'))).toBe(true);
    expect(isGone(new Error('410 Gone for http://x/echogram/mount/ab/Sv/0.0.0'))).toBe(true);
    expect(isGone(new Error('unknown mount; resolve the step again'))).toBe(true);
    expect(isGone(new Error('404 Not Found'))).toBe(false);
  });
});

describe('experience test fixes', () => {
  it('takes a header error as gone: gone, not_found from an older server, a describe 404', () => {
    expect(isGone(new Error('not_found'))).toBe(true);
    expect(isGone(new Error('404 from /echogram/api/describe/ab12: <html>'))).toBe(true);
    expect(isGone(new Error('404 from /api/other: <html>'))).toBe(false);
  });

  it('says why nothing can be drawn, output by output', () => {
    const hdbscan = {
      ...resolutionOf(svMapped),
      step: 'run_hdbscan',
      kind: null,
      outputs: [
        { name: 'clustering_results', format: 'zarr', kind: null, reason: 'no variable along ping_time and a vertical coordinate' },
        { name: 'clustering_model', format: 'pickle', kind: null, reason: 'a pickled output, which only Python can read' },
      ],
    } as Resolution;
    expect(missText(hdbscan)).toBe(
      'run_hdbscan has no echogram to draw (clustering_results: no variable along ping_time and a vertical ' +
        'coordinate; clustering_model: a pickled output, which only Python can read).',
    );
    expect(missText({ ...hdbscan, status: 'not_run', mode: 'artifacts' })).toBe('run_hdbscan has no saved output to draw.');
  });

  it('labels each layer uniquely, the step alone for a lone default', () => {
    const resolution = resolutionOf(embed);
    const make = (output: string, variable: string) =>
      layer('embed_results', { id: layerId('embed_results', output, variable), output, variable, resolution, label: 'x' });
    expect(withLabels([make('ds_normalized', 'Sv')]).map((entry) => entry.label)).toEqual(['embed_results']);
    expect(withLabels([make('gridded_results', '_da')]).map((entry) => entry.label)).toEqual([
      'embed_results:gridded_results',
    ]);
    const labels = withLabels([
      make('ds_normalized', 'Sv'),
      make('ds_normalized', 'ml_dataset_hdbscan_results_grid'),
      make('gridded_results', '_da'),
    ]).map((entry) => entry.label);
    expect(labels).toEqual([
      'embed_results:Sv',
      'embed_results:ml_dataset_hdbscan_results_grid',
      'embed_results:gridded_results',
    ]);
    expect(withLabels([layer('sv', { label: 'kept' })])[0].label).toBe('kept');
  });
});

describe('row actions', () => {
  it('offers Run when nothing holds the current hash, Refresh for newer data or an error, else nothing', () => {
    const row = (state: EchogramLayer['state'], freshness: EchogramLayer['freshness']) => actionsFor({ state, freshness });
    expect(row('shown', { state: 'current' })).toBeNull();
    expect(row('shown', { state: 'unknown' })).toBeNull();
    expect(row('shown', { state: 'available' })).toBe('refresh');
    expect(row('error', { state: 'current' })).toBe('refresh');
    expect(row('shown', { state: 'not_computed' })).toBe('run');
    expect(row('error', { state: 'not_computed' })).toBe('run');
    expect(row('shown', { state: 'partial', held: 1, of: 3 })).toBe('run');
  });
});
