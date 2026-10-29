import { choices, type Resolution, type ViewSettings } from 'aa-si-echogram-gl';
import { describe, expect, it } from 'vitest';
import type { StepArtifacts } from '../src/api/models';
import {
  asResolution,
  computedSteps,
  foundText,
  isGone,
  layerId,
  missText,
  outOfDateLayers,
  restoredLayer,
  savedLayer,
  settingsFor,
  sourcesWithoutLayers,
  tierLetters,
  withLabels,
  type EchogramLayer,
} from '../src/echogram/layers';
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
    outOfDate: false,
    addedAt: 1000,
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
    const saved = savedLayer(layer('sv', { resolution: resolutionOf(svMapped), outOfDate: true }));
    expect(Object.keys(saved).sort()).toEqual(['addedAt', 'label', 'output', 'step', 'stepHash', 'variable']);
    expect(restoredLayer(saved)).toEqual(layer('sv', { state: 'loading' }));
  });
});

describe('out of date layers', () => {
  const computed = (steps: string[], at: number) => ({ steps: new Set(steps), at });

  it('marks a layer whose step hash moved in the preview', () => {
    const layers = [layer('a'), layer('b')];
    expect(outOfDateLayers(layers, { a: 'h2', b: 'h1' }, null)).toEqual(new Set(['a/ds_Sv/Sv']));
  });

  it('marks a layer whose step a run computed after it was drawn', () => {
    const layers = [layer('a', { addedAt: 1000 }), layer('b', { addedAt: 3000 })];
    expect(outOfDateLayers(layers, {}, computed(['a', 'b'], 2000))).toEqual(new Set(['a/ds_Sv/Sv']));
  });

  it('leaves alone a layer with the same hash and no later run, or a step the preview lacks', () => {
    expect(outOfDateLayers([layer('a')], { a: 'h1' }, computed(['b'], 5000)).size).toBe(0);
    expect(outOfDateLayers([layer('a')], {}, null).size).toBe(0);
  });

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

  it('reads what a finished run computed, and when from its run_finished event', () => {
    expect(computedSteps(initialRunState('r1'))).toBeNull();
    const run = finishedRun({ finishedAt: 20, logs: [log(1, 10), log(2, 12.5)] });
    expect(computedSteps(run)).toEqual({ steps: new Set(['a', 'c']), at: 20000 });
  });

  it('falls back to the last event logged, and to nothing rather than the clock', () => {
    expect(computedSteps(finishedRun({ logs: [log(1, 12.5), log(2, 10)] }))?.at).toBe(12500);
    expect(computedSteps(finishedRun())).toBeNull();
  });

  it('reads a long log without spreading it into arguments', () => {
    const logs = Array.from({ length: 300000 }, (_, index) => log(index, index));
    expect(computedSteps(finishedRun({ logs }))?.at).toBe(299999000);
  });

  it('does not mark a layer refreshed after the run again', () => {
    const run = finishedRun({ finishedAt: Date.now() / 1000 - 5 });
    const refreshed = layer('a', { addedAt: Date.now() });
    expect(outOfDateLayers([refreshed], {}, computedSteps(run))).toEqual(new Set());
    // Asked again later, the run's time is still its own.
    expect(outOfDateLayers([refreshed], {}, computedSteps(run))).toEqual(new Set());
    expect(outOfDateLayers([layer('a', { addedAt: 0 })], {}, computedSteps(run))).toEqual(new Set(['a/ds_Sv/Sv']));
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
