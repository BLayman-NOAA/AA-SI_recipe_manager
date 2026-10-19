import { describe, expect, it } from 'vitest';
import { findInclude, type Graph } from '../src/api/graph';
import type { Preview, StepPreview } from '../src/api/models';
import { buildView } from '../src/diagram/buildView';
import { layoutKey } from '../src/diagram/useLayout';
import {
  includeSummary,
  NOT_IN_RUN,
  NOT_NEEDED,
  notNeeded,
  predictedOf,
  runMarks,
  statusText,
  stepBadges,
  surveyBackedSteps,
  tierLetter,
} from '../src/runs/badges';
import { parseRunEvents, type RunEvent } from '../src/runs/events';
import { initialRunState, reduceEvents } from '../src/runs/reducer';
import cachedText from './fixtures/cached.events.jsonl?raw';
import uc1Json from './fixtures/uc1.graph.json';
import { withSubGraph } from './helpers';

const CHAIN = 'inc:sub/chain.yaml';

function step(id: string, overrides: Partial<StepPreview> = {}): StepPreview {
  return {
    step_id: id,
    step_hash: `hash-${id}`,
    held_by: [],
    planned: 'compute',
    tier: null,
    checkpoint: 'memory-only',
    pinned: false,
    recipe_checkpoint: null,
    fanned_out: false,
    instances: null,
    instances_held: {},
    note: null,
    ...overrides,
  };
}

/** seed in both tiers, the chain from the survey tier, total to compute as a pinned target. */
const PREVIEW: Preview = {
  recipe: 'client_with_sub',
  version: '1.0',
  mode: 'to',
  targets: ['total'],
  force_steps: [],
  tiers: { user: '/u', survey: '/s' },
  write_tier: 'user',
  steps: [
    step('seed', { held_by: ['user', 'survey'], planned: 'load-user', tier: 'user', checkpoint: 'saved' }),
    step('chain_inc', { held_by: ['survey'], planned: 'load-survey', tier: 'survey', checkpoint: 'saved', fanned_out: true }),
    step('leaf_add', { held_by: ['survey'], planned: 'load-survey', tier: 'survey', checkpoint: 'saved', fanned_out: true }),
    step('total', { pinned: true, checkpoint: 'will-save', note: 'a target' }),
  ],
  blockers: [],
  warnings: [],
};

function event(seq: number, kind: string, data: Record<string, unknown>): RunEvent {
  return { seq, time: seq, kind, run_id: 'core', data };
}

/** A finished run: seed computed with an older hash, total computed, and the chain loaded. */
function finishedRun() {
  return reduceEvents(initialRunState('r1'), [
    event(1, 'run_started', { recipe: 'client_with_sub' }),
    event(2, 'step_finished', { step: 'seed', disposition: 'computed', tier: null, hash: 'old-seed', index: 1, total: 4 }),
    event(3, 'step_finished', {
      step: 'chain_inc',
      disposition: 'hit-survey-cache',
      tier: 'survey',
      hash: 'hash-chain_inc',
      index: 2,
      total: 4,
      instances: { count: 3, computed: 0, cached: 3, skipped: 0 },
    }),
    event(4, 'step_finished', { step: 'total', disposition: 'computed', tier: 'user', hash: 'hash-total', index: 4, total: 4 }),
    event(5, 'run_finished', { status: 'completed', executed: ['seed', 'total'], skipped: [], pruned: [], untargeted: [] }),
  ]);
}

describe('predictedOf', () => {
  it('maps the preview and plan vocabularies to the same marks', () => {
    expect(predictedOf('load-survey', 'survey')).toBe('survey');
    expect(predictedOf('load', 'survey')).toBe('survey');
    expect(predictedOf('load-user', 'user')).toBe('user');
    expect(predictedOf('load', 'user')).toBe('user');
    expect(predictedOf('compute', null)).toBe('compute');
    expect(predictedOf('marker', 'user')).toBe('marker');
    expect(predictedOf('pruned', null)).toBe('pruned');
    expect(predictedOf('untargeted', null)).toBe('untargeted');
    expect(predictedOf('unknown', null)).toBe('unknown');
    expect(predictedOf('something new', null)).toBe('unknown');
  });
});

describe('stepBadges', () => {
  it('outlines from the preview before any run', () => {
    const badges = stepBadges(withSubGraph, PREVIEW, null);
    expect(badges.seed).toMatchObject({ predicted: 'user', actual: null, saved: 'U', willSave: false });
    expect(badges.chain_inc).toMatchObject({ predicted: 'survey', saved: 'S', surveyBacked: true });
    expect(badges.total).toMatchObject({ predicted: 'compute', saved: null, willSave: true, note: 'a target' });
    expect(badges.seed.surveyBacked).toBe(true);
    expect(badges.total.surveyBacked).toBe(false);
    expect(Object.values(badges).every((badge) => !badge.checkpointed)).toBe(true);
  });

  it('fills from a finished run and marks a result with another hash stale', () => {
    const badges = stepBadges(withSubGraph, PREVIEW, runMarks(finishedRun()));
    expect(badges.seed).toMatchObject({ predicted: 'user', actual: 'computed', stale: true });
    expect(badges.chain_inc).toMatchObject({ actual: 'hit-survey-cache', stale: false, progress: { done: 3, count: 3 } });
    expect(badges.total).toMatchObject({ actual: 'computed', stale: false });
    expect(badges.leaf_add.actual).toBeNull();
  });

  it('outlines from the run plan while the run is going', () => {
    const live = reduceEvents(initialRunState('r2'), [
      event(1, 'plan', {
        steps: {
          seed: { hash: 'hash-seed', planned: 'load', tier: 'user' },
          total: { hash: 'hash-total', planned: 'untargeted', tier: null },
        },
        blockers: [],
      }),
      event(2, 'step_started', { step: 'seed', index: 1, total: 1, instances: null, chain: null }),
    ]);
    const badges = stepBadges(withSubGraph, PREVIEW, runMarks(live));
    expect(badges.seed).toMatchObject({ predicted: 'user', actual: 'running' });
    expect(badges.total).toMatchObject({ predicted: 'untargeted', actual: 'untargeted' });
    expect(badges.chain_inc.predicted).toBe('survey');
  });

  it('marks the steps the recipe checkpoints', () => {
    const graph = structuredClone(withSubGraph);
    const total = graph.nodes.find((node) => node.id === 'total');
    if (total?.kind === 'step') total.checkpoint = 'always';
    expect(stepBadges(graph, null, null).total).toMatchObject({ checkpointed: true, predicted: null });
  });

  it('gives the S, U and M letters, the actual status first', () => {
    const badges = stepBadges(withSubGraph, PREVIEW, runMarks(finishedRun()));
    expect(tierLetter(badges.chain_inc)).toBe('S');
    expect(tierLetter(badges.seed)).toBeNull();
    expect(tierLetter(stepBadges(withSubGraph, PREVIEW, null).seed)).toBe('U');
    expect(tierLetter({ ...badges.total, actual: 'marker' })).toBe('M');
  });
});

describe('include marks', () => {
  it('counts an include box members by prediction', () => {
    const badges = stepBadges(withSubGraph, PREVIEW, null);
    expect(includeSummary(findInclude(withSubGraph, CHAIN)!, badges)).toBe('2 survey');
    const all = { ...findInclude(withSubGraph, CHAIN)!, steps: ['seed', 'chain_inc', 'leaf_add', 'total'] };
    expect(includeSummary(all, badges)).toBe('2 survey, 1 user, 1 to compute');
  });

  it('says a sub-recipe is not in the run rather than nothing, and is empty before a prediction', () => {
    const outside: Preview = {
      ...PREVIEW,
      steps: [
        step('chain_inc', { planned: 'skipped', fanned_out: true }),
        step('leaf_add', { planned: 'untargeted' }),
      ],
    };
    const chain = findInclude(withSubGraph, CHAIN)!;
    expect(includeSummary(chain, stepBadges(withSubGraph, outside, null))).toBe(NOT_IN_RUN);
    expect(includeSummary(chain, stepBadges(withSubGraph, null, null))).toBe('');
    const unknown: Preview = { ...PREVIEW, steps: [step('chain_inc', { planned: 'unknown' }), step('leaf_add', { planned: 'pruned' })] };
    expect(includeSummary(chain, stepBadges(withSubGraph, unknown, null))).toBe('1 not yet known');

    const options = { drill: [], connected: false, selected: null, expandedSteps: new Set<string>(), expandedBoxes: new Set<string>() };
    const box = (preview: Preview | null) =>
      buildView(withSubGraph, { ...options, badges: stepBadges(withSubGraph, preview, null) }).nodes.find(
        (node) => node.id === CHAIN,
      );
    const outsideBox = box(outside);
    expect(outsideBox?.kind === 'include' && outsideBox.data.summary).toBe(NOT_IN_RUN);
    const bareBox = box(null);
    expect(bareBox?.kind === 'include' && bareBox.data.summary).toBeNull();
  });

  it('lists the survey backed steps', () => {
    expect([...surveyBackedSteps(PREVIEW)].sort()).toEqual(['chain_inc', 'leaf_add', 'seed']);
    expect(surveyBackedSteps(null).size).toBe(0);
  });

  it('reaches the view through buildView without changing the layout key', () => {
    const options = {
      drill: [],
      connected: false,
      selected: null,
      expandedSteps: new Set<string>(),
      expandedBoxes: new Set<string>(),
    };
    const plain = buildView(withSubGraph, options);
    const marked = buildView(withSubGraph, { ...options, badges: stepBadges(withSubGraph, PREVIEW, runMarks(finishedRun())) });
    expect(layoutKey(marked, 'RIGHT')).toBe(layoutKey(plain, 'RIGHT'));
    const seed = marked.nodes.find((node) => node.id === 'seed');
    expect(seed?.kind === 'step' && seed.data.badge?.actual).toBe('computed');
    const box = marked.nodes.find((node) => node.id === CHAIN);
    expect(box?.kind === 'include' && box.data).toMatchObject({ summary: '2 survey', surveyBacked: true, status: null });
    const bare = plain.nodes.find((node) => node.id === CHAIN);
    expect(bare?.kind === 'include' && bare.data).toMatchObject({ summary: null, surveyBacked: false });
  });
});

describe('outlines while a run goes', () => {
  const uc1Graph = uc1Json as unknown as Graph;
  // The user's run to dive_file_mvbs: every file's MVBS was cached, so the chain up to it was not needed.
  const cached = parseRunEvents(cachedText);
  const CHAIN_STEPS = ['read_raw', 'dive_sv', 'dive_file_mvbs'];
  const started: Preview = {
    ...PREVIEW,
    targets: ['dive_file_mvbs'],
    steps: [
      step('plan_dives', { planned: 'load-user', tier: 'user', held_by: ['user'] }),
      step('query_ncei', { planned: 'load-survey', tier: 'survey', held_by: ['survey'] }),
      step('scan_raw_config', { planned: 'pruned', held_by: ['survey'] }),
      step('build_cal_mapping', { planned: 'load-survey', tier: 'survey', held_by: ['survey'] }),
      step('read_raw', { planned: 'skipped', fanned_out: true, instances: 3 }),
      step('dive_sv', { planned: 'skipped', held_by: ['user'], fanned_out: true, instances: 3 }),
      step('dive_file_mvbs', { planned: 'load-user', tier: 'user', held_by: ['user'], fanned_out: true, instances: 3 }),
    ],
  };
  // The plan event cannot resolve a chain per instance, so it says compute for each member.
  const run = (count: number) => reduceEvents(initialRunState('r1'), cached.slice(0, count));
  const live = cached.length - 1;

  it('keeps the start preview, not the plan, though the plan says compute', () => {
    expect(CHAIN_STEPS.map((id) => run(live).steps[id].planned)).toEqual(['compute', 'compute', 'compute']);
    for (let count = 2; count <= live; count += 1) {
      const badges = stepBadges(uc1Graph, null, runMarks(run(count), started));
      expect(CHAIN_STEPS.map((id) => badges[id].predicted)).toEqual(['skipped', 'skipped', 'user']);
      expect(Object.values(badges).some((badge) => badge.predicted === 'compute')).toBe(false);
    }
    const badges = stepBadges(uc1Graph, null, runMarks(run(live), started));
    expect(badges.read_raw).toMatchObject({ predicted: 'skipped', actual: 'skipped' });
    expect(badges.dive_file_mvbs).toMatchObject({ predicted: 'user', actual: 'hit-user-cache' });
    expect(badges.scan_raw_config.predicted).toBe('pruned');
    expect(notNeeded(badges.dive_sv)).toBe(true);
    expect(notNeeded(badges.dive_file_mvbs)).toBe(false);
  });

  it('uses the plan for a run without a start preview, as one replayed after a reload', () => {
    const badges = stepBadges(uc1Graph, started, runMarks(run(live)));
    expect(CHAIN_STEPS.map((id) => badges[id].predicted)).toEqual(['compute', 'compute', 'compute']);
    expect(badges.plan_dives.predicted).toBe('user');
  });

  it('outlines a finished run from the current preview', () => {
    const now: Preview = { ...started, steps: started.steps.map((item) => ({ ...item, planned: 'load-user' })) };
    const badges = stepBadges(uc1Graph, now, runMarks(run(cached.length), started));
    expect(CHAIN_STEPS.map((id) => badges[id].predicted)).toEqual(['user', 'user', 'user']);
  });

  it('words pruned and skipped as not needed', () => {
    expect(statusText('skipped')).toBe(NOT_NEEDED);
    expect(statusText('pruned')).toBe(NOT_NEEDED);
    expect(statusText('load-user')).toBe('load-user');
  });
});
