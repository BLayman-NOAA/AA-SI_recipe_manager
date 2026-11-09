import { choices, type LineData, type Resolution, type ViewSettings } from 'aa-si-echogram-gl';
import { describe, expect, it } from 'vitest';
import type { Preview, StepArtifacts, StepPreview } from '../src/api/models';
import {
  asResolution,
  computedSteps,
  defaultLineColor,
  foundText,
  goneText,
  isGone,
  isLineChoice,
  layerFreshness,
  layerId,
  LINE_ACCENT,
  LINE_COLORS,
  LINE_WIDTH,
  lineErrorText,
  lineSpecsFor,
  linesOf,
  missText,
  orderedRows,
  parseSavedLayers,
  restoredLayer,
  rowLabel,
  savedLayer,
  saveKind,
  settingsFor,
  sourcesWithoutLayers,
  spansSessions,
  tierLetters,
  unsavedText,
  withFreshness,
  withLabels,
  type EchogramLayer,
  type LineLayer,
} from '../src/echogram/layers';
import { actionsFor } from '../src/echogram/LayerList';
import { initialRunState, type RunState } from '../src/runs/reducer';
import diveSv from './fixtures/artifacts.dive_sv.json';
import embed from './fixtures/artifacts.embed_results.json';
import svLine from './fixtures/artifacts.sv_line.json';
import svMapped from './fixtures/artifacts.sv_mapped.json';

const resolutionOf = (found: unknown) => asResolution((found as StepArtifacts).echogram.resolution);

function layer(step: string, overrides: Partial<EchogramLayer> = {}): EchogramLayer {
  const sid = overrides.sid ?? 'S';
  return {
    id: layerId(sid, step, 'ds_Sv', 'Sv'),
    sid,
    kind: 'value',
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
  it('names a layer by session, step, output and variable', () => {
    expect(layerId('S', 'dive_sv', 'ds_Sv', 'Sv')).toBe('S/dive_sv/ds_Sv/Sv');
    expect(layerId('S', 'pyramid', 'store', null)).toBe('S/pyramid/store');
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
    expect(Object.keys(saved).sort()).toEqual(['addedAt', 'label', 'output', 'sid', 'step', 'stepHash', 'variable']);
    expect(restoredLayer(saved)).toEqual(layer('sv', { state: 'loading' }));
    const other = savedLayer(layer('sv', { sid: 'T' }));
    expect(restoredLayer(other)).toEqual(layer('sv', { sid: 'T', state: 'loading' }));
    expect(restoredLayer(other).id).toBe('T/sv/ds_Sv/Sv');
  });

  it('reads saved rows, filling defaults and dropping rows it cannot place', () => {
    const rows = [
      { step: 'sv', output: 'ds_Sv' },
      { sid: 'T', step: 'mvbs', output: 'ds_MVBS', variable: 'Sv', label: 'mvbs:Sv', stepHash: 'h', addedAt: 5 },
      { sid: 'T', output: 'ds_Sv' },
      { sid: 'T', step: 'sv' },
      { sid: 'T', step: 'sv', output: '' },
      null,
      'sv',
    ];
    expect(parseSavedLayers(rows, 'S')).toEqual([
      { sid: 'S', step: 'sv', output: 'ds_Sv', variable: null, label: 'sv', stepHash: '', addedAt: 0 },
      { sid: 'T', step: 'mvbs', output: 'ds_MVBS', variable: 'Sv', label: 'mvbs:Sv', stepHash: 'h', addedAt: 5 },
    ]);
    // Without a fallback, a row that names no session is dropped rather than guessed.
    expect(parseSavedLayers(rows).map((row) => row.sid)).toEqual(['T']);
    expect(parseSavedLayers({ layers: rows }, 'S')).toEqual([]);
    expect(parseSavedLayers(undefined, 'S')).toEqual([]);
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
    const tracked = { S: { preview: previewOf(stepAt('a'), stepAt('b', { step_hash: 'h2' })), computed: null } };
    const next = withFreshness(rows, tracked);
    expect(next[0]).toBe(rows[0]);
    expect(next[1].freshness).toEqual({ state: 'not_computed' });
    expect(withFreshness(next, {}).map((row) => row.freshness.state)).toEqual(['unknown', 'unknown']);
  });

  it("reads each row's freshness from its own session's preview and last run", () => {
    const rows = [layer('a'), layer('a', { sid: 'T' }), layer('a', { sid: 'U' })];
    const next = withFreshness(rows, {
      S: { preview: previewOf(stepAt('a')), computed: null },
      T: { preview: previewOf(stepAt('a', { step_hash: 'h2' })), computed: null },
      U: { preview: null, computed: computed(['a'], 'r1') },
    });
    expect(next.map((row) => row.freshness.state)).toEqual(['current', 'not_computed', 'unknown']);
    const ran = withFreshness(rows, {
      S: { preview: previewOf(stepAt('a')), computed: null },
      T: { preview: previewOf(stepAt('a')), computed: computed(['a'], 'r1') },
    });
    expect(ran.map((row) => row.freshness.state)).toEqual(['current', 'available', 'unknown']);
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
    expect(isGone(Object.assign(new Error('test_error'), { status: 410 }))).toBe(true);
  });

  it('takes a 404 or a 400 from the line route as something the mount lacks', () => {
    expect(isGone(new Error('not_found'))).toBe(false);
    expect(isGone(Object.assign(new Error('not_found'), { status: 404 }))).toBe(false);
    expect(isGone(new Error('bad_request'))).toBe(false);
    expect(lineErrorText('sv', 'dive_fit', new Error('not_found'))).toBe("sv has no line 'dive_fit' in one of its files.");
    expect(lineErrorText('sv', 'flag', new Error('bad_request'))).toBe("sv's 'flag' is not a line in one of its files.");
    const detailed = Object.assign(new Error('bad_request'), { status: 400, detail: "'flag' is not a line" });
    expect(lineErrorText('sv', 'flag', detailed)).toBe("sv, line 'flag': 'flag' is not a line");
    expect(lineErrorText('sv', 'flag', new Error('internal_server_error'))).toBe('internal_server_error');
  });
});

describe('experience test fixes', () => {
  it('takes a header error as gone: gone, not_found from an older server, a describe 404', () => {
    const header = { header: true };
    expect(isGone(new Error('not_found'), header)).toBe(true);
    expect(isGone(new Error('404 from /echogram/api/describe/ab12: <html>'), header)).toBe(true);
    expect(isGone(new Error('404 from /echogram/api/describe/ab12: <html>'))).toBe(false);
    expect(isGone(new Error('404 from /api/other: <html>'), header)).toBe(false);
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
      layer('embed_results', { id: layerId('S', 'embed_results', output, variable), output, variable, resolution, label: 'x' });
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

  it('counts labels per session, so a step two sessions share keeps its plain label in each', () => {
    const resolution = resolutionOf(svMapped);
    const crop = (sid: string) => layer('crop_survey_range', { sid, resolution, label: 'x' });
    expect(withLabels([crop('S'), crop('T')]).map((entry) => entry.label)).toEqual([
      'crop_survey_range',
      'crop_survey_range',
    ]);
  });
});

describe('rows across sessions', () => {
  const titles = { S: 'demo_survey_sv', T: 'demo_dive_analysis' };

  it('knows when the layers span sessions', () => {
    expect(spansSessions([])).toBe(false);
    expect(spansSessions([{ sid: 'S' }, { sid: 'S' }])).toBe(false);
    expect(spansSessions([{ sid: 'S' }, { sid: 'T' }])).toBe(true);
  });

  it("puts the session's title before the label when the layers span sessions or the row's tab is behind", () => {
    expect(rowLabel(layer('sv'), titles, false, 'S')).toBe('sv');
    expect(rowLabel(layer('sv'), titles, true, 'S')).toBe('demo_survey_sv: sv');
    expect(rowLabel(layer('sv'), titles, false, 'T')).toBe('demo_survey_sv: sv');
    expect(rowLabel(layer('sv'), titles, false, null)).toBe('demo_survey_sv: sv');
    expect(rowLabel(layer('sv', { sid: 'X' }), titles, true, 'S')).toBe('X: sv');
  });

  it("lists the front session's rows first, each session's in stored order", () => {
    const rows = [
      layer('a', { sid: 'S' }),
      layer('b', { sid: 'T' }),
      layer('c', { sid: 'U' }),
      layer('d', { sid: 'T' }),
      layer('e', { sid: 'S' }),
    ];
    const ids = (list: EchogramLayer[]) => list.map((row) => `${row.sid}:${row.step}`);
    expect(ids(orderedRows(rows, 'T'))).toEqual(['T:b', 'T:d', 'S:a', 'S:e', 'U:c']);
    expect(ids(orderedRows(rows, 'S'))).toEqual(['S:a', 'S:e', 'T:b', 'T:d', 'U:c']);
    expect(ids(orderedRows(rows, null))).toEqual(['S:a', 'S:e', 'T:b', 'T:d', 'U:c']);
    expect(ids(orderedRows(rows, 'X'))).toEqual(['S:a', 'S:e', 'T:b', 'T:d', 'U:c']);
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

describe('line layers', () => {
  const resolution = resolutionOf(svLine);
  /** The mapped fixture's three instances, with the line fixture's variables listed. */
  const mapped = (): Resolution => ({
    ...resolutionOf(svMapped),
    output: 'ds',
    outputs: [{ ...resolution.outputs![0] }],
  });
  const data = (offset: number): LineData => ({
    name: 'line_a',
    longName: 'line a',
    units: 'm',
    verticalRef: 'depth',
    pingTime: Float64Array.from([1, 2, 3]),
    value: Float64Array.from([offset, offset + 1, NaN]),
  });
  const line = (overrides: Partial<EchogramLayer> = {}) =>
    layer('lines', {
      id: layerId('S', 'lines', 'ds', 'line_a'),
      kind: 'line',
      output: 'ds',
      variable: 'line_a',
      label: 'lines:line_a',
      resolution,
      color: '#00e5ff',
      width: 3,
      visible: false,
      ...overrides,
    }) as LineLayer;

  it('lists the lines a resolution offers beside its value, and knows a line choice', () => {
    expect(linesOf(resolution)).toEqual([
      { output: 'ds', name: 'line_a', verticalRef: 'depth', units: 'm', longName: 'line a' },
      { output: 'ds', name: 'line_b', verticalRef: 'range', units: 'm', longName: 'line b' },
    ]);
    expect(linesOf(resolutionOf(svMapped))).toEqual([]);
    expect(choices(resolution).map((choice) => [choice.var, isLineChoice(choice)])).toEqual([
      ['Sv', false],
      ['line_a', true],
      ['line_b', true],
    ]);
  });

  it('colours a fit magenta, its band cyan, and anything else with the accent', () => {
    expect(defaultLineColor('dive_fit', '#4051b5')).toBe(LINE_COLORS.fit);
    expect(defaultLineColor('dive_u99', '#4051b5')).toBe(LINE_COLORS.band);
    expect(defaultLineColor('dive_L99', '#4051b5')).toBe(LINE_COLORS.band);
    expect(defaultLineColor('seafloor', '#4051b5')).toBe('#4051b5');
    expect(defaultLineColor('seafloor')).toBe(LINE_ACCENT);
  });

  it('makes one spec per fetched instance, with its id, label and style', () => {
    const [only] = resolution.instances!;
    expect(lineSpecsFor(line(), { [only.id]: data(10) }, 'lines:line_a')).toEqual([
      {
        id: `S/lines/ds/line_a#${only.id}`,
        label: 'lines:line_a',
        pingTime: data(10).pingTime,
        value: data(10).value,
        verticalRef: 'depth',
        color: '#00e5ff',
        width: 3,
        opacity: 1,
        visible: false,
      },
    ]);
    expect(lineSpecsFor(line(), undefined, 'x')).toEqual([]);

    const pieces = mapped();
    const [a, , c] = pieces.instances!;
    const specs = lineSpecsFor(line({ resolution: pieces }), { [a.id]: data(1), [c.id]: data(3) }, 'lines:line_a');
    expect(specs.map((spec) => [spec.id, spec.label])).toEqual([
      [`S/lines/ds/line_a#${a.id}`, 'lines:line_a (10)'],
      [`S/lines/ds/line_a#${c.id}`, 'lines:line_a (30)'],
    ]);
  });

  it('names a mapped instance by the last part of its item', () => {
    const pieces = mapped();
    pieces.instances = pieces.instances!.map((instance, at) => ({ ...instance, item: `https://x/raw/D${at}.raw` }));
    const fetched = Object.fromEntries(pieces.instances.map((instance) => [instance.id, data(0)]));
    expect(lineSpecsFor(line({ resolution: pieces }), fetched, 'fit').map((spec) => spec.label)).toEqual([
      'fit (D0.raw)',
      'fit (D1.raw)',
      'fit (D2.raw)',
    ]);
  });

  it('saves its style, and reads an old row as a value and a line row with defaults', () => {
    const saved = savedLayer(line());
    expect(saved).toMatchObject({ kind: 'line', color: '#00e5ff', width: 3, visible: false });
    expect(restoredLayer(saved)).toEqual(line({ resolution: null, state: 'loading' }));
    expect(restoredLayer(parseSavedLayers([{ sid: 'S', step: 'sv', output: 'ds_Sv' }])[0]).kind).toBe('value');

    const rows = [
      { sid: 'S', kind: 'line', step: 'd', output: 'ds', variable: 'dive_fit' },
      { sid: 'S', kind: 'line', step: 'd', output: 'ds', variable: 'bottom', color: 'red', width: -1, visible: 'no' },
      { sid: 'S', kind: 'line', step: 'd', output: 'ds', variable: 'top', color: '#123456', width: 4, visible: false },
      { sid: 'S', kind: 'line', step: 'd', output: 'ds' },
      { sid: 'S', kind: 'other', step: 'd', output: 'ds' },
    ];
    const style = parseSavedLayers(rows).map(({ kind, variable, color, width, visible }) => ({
      kind,
      variable,
      color,
      width,
      visible,
    }));
    expect(style).toEqual([
      { kind: 'line', variable: 'dive_fit', color: LINE_COLORS.fit, width: LINE_WIDTH, visible: true },
      { kind: 'line', variable: 'bottom', color: LINE_ACCENT, width: LINE_WIDTH, visible: true },
      { kind: 'line', variable: 'top', color: '#123456', width: 4, visible: false },
      { kind: undefined, variable: null, color: undefined, width: undefined, visible: undefined },
    ]);
  });

  it('labels a line by its variable, and counts what it draws in lines', () => {
    const value = layer('lines', { id: layerId('S', 'lines', 'ds', 'Sv'), output: 'ds', variable: 'Sv', resolution });
    expect(withLabels([line({ label: 'x' })]).map((entry) => entry.label)).toEqual(['lines:line_a']);
    expect(withLabels([value, line()]).map((entry) => entry.label)).toEqual(['lines:Sv', 'lines:line_a']);
    expect(foundText(resolution, 'line')).toBe('1 line');
    expect(foundText(mapped(), 'line')).toBe('3 lines');
    expect(foundText(null, 'line')).toBe('');
  });
});
