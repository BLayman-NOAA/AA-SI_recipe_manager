/**
 * The echogram pane's layer rules, kept pure so the store and the tests share them.
 *
 * A layer is one output variable of one step, drawn as one EchogramGL source.
 */
import { describeMiss, type Layer, type Resolution, type ViewSettings } from 'aa-si-echogram-gl';
import type { Preview, StepPreview } from '../api/models';
import { partlyHeld } from '../runs/badges';
import type { RunState, StepRunState } from '../runs/reducer';

/** Where the client server mounts EchogramGL's routes, relative to the page. */
export const ECHOGRAM_ROOT = '/echogram/';

export interface EchogramLayer {
  /** The panel's source id: `step/output/var`. */
  id: string;
  step: string;
  output: string;
  variable: string | null;
  /** What the layer is called: the step, and the variable when it is not the default. */
  label: string;
  /** The step's hash when the layer was added or last refreshed. */
  stepHash: string;
  /** What the server resolved and mounted; null until fetched after a reload. */
  resolution: Resolution | null;
  state: 'loading' | 'shown' | 'error' | 'refreshing';
  error: string | null;
  /** How current the drawn data is, from the run all preview and the last run (layerFreshness). */
  freshness: Freshness;
  /** When the layer was added or refreshed, ms since 1970. */
  addedAt: number;
  /** The id of the last finished run when the layer was fetched; null when there was none. */
  drawnAfter: string | null;
  /**
   * A hash other than the drawn one that the preview said was saved and a
   * refresh found nothing at, with the last finished run then.
   */
  missed?: { stepHash: string; after: string | null };
}

/**
 * How current a layer's data is.
 *
 * `current`: drawn at the step's current hash, and no later run saved it.
 * `available`: a saved result for the current hash is not the one drawn.
 * `not_computed`: nothing holds the current hash. `not_saved`: the preview
 * says the current hash is held, but a refresh since the last finished run
 * found nothing saved at it. `partial`: some instances of a fanned out step
 * hold it, `held` of `of`. `unknown`: no preview yet, or none for the step.
 */
export type Freshness =
  | { state: 'current' | 'available' | 'not_computed' | 'not_saved' | 'unknown' }
  | { state: 'partial'; held: number; of: number | null };

/** What is kept of a layer across a page reload. */
export type SavedLayer = Pick<EchogramLayer, 'step' | 'output' | 'variable' | 'label' | 'stepHash' | 'addedAt'>;

/** A layer's source id. Layers belong to one session, so the session is not part of it. */
export function layerId(step: string, output: string, variable: string | null): string {
  return variable ? `${step}/${output}/${variable}` : `${step}/${output}`;
}

/** The generated type leaves the resolution open; this is the one place it becomes EchogramGL's. */
export function asResolution(value: unknown): Resolution {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error('The server sent no echogram resolution.');
  }
  const resolution = value as Partial<Resolution>;
  if (typeof resolution.status !== 'string' || typeof resolution.step !== 'string') {
    throw new Error('The echogram resolution has no status or step.');
  }
  return value as Resolution;
}

function computedStep(step: StepRunState): boolean {
  return step.status === 'computed' || step.instances.computed > 0;
}

/** The steps a finished run computed and saved, and which run it was. */
export interface ComputedSteps {
  steps: Set<string>;
  runId: string;
}

/**
 * What a finished run computed and saved.
 *
 * Args:
 *   run: The last run.
 *   start: The preview the run was started with. A step it says the run kept
 *     in memory only is left out; without it every computed step counts.
 */
export function computedSteps(run: RunState | null, start: Preview | null = null): ComputedSteps | null {
  if (!run?.finished) return null;
  const atStart = new Map((start?.steps ?? []).map((step) => [step.step_id, step]));
  const written = (id: string) => {
    const step = atStart.get(id);
    return !step || step.pinned || step.checkpoint !== 'memory-only';
  };
  const steps = new Set(
    Object.entries(run.steps)
      .filter(([id, step]) => computedStep(step) && written(id))
      .map(([id]) => id),
  );
  return { steps, runId: run.runId };
}

/**
 * How current a layer's data is (see Freshness).
 *
 * Args:
 *   layer: The layer.
 *   step: The layer's step in the run all preview.
 *   computed: What the last finished run computed and saved.
 */
export function layerFreshness(
  layer: EchogramLayer,
  step: StepPreview | undefined,
  computed: ComputedSteps | null,
): Freshness {
  if (!step?.step_hash) return { state: 'unknown' };
  const partly = step.fanned_out ? partlyHeld(step) : null;
  if (partly) return { state: 'partial', held: partly.count, of: partly.of };
  if (step.step_hash === layer.stepHash) {
    const rerun = Boolean(computed?.steps.has(layer.step)) && computed!.runId !== layer.drawnAfter;
    return { state: rerun ? 'available' : 'current' };
  }
  if (step.held_by.length > 0) {
    const missed = layer.missed?.stepHash === step.step_hash && layer.missed.after === (computed?.runId ?? null);
    return { state: missed ? 'not_saved' : 'available' };
  }
  return { state: 'not_computed' };
}

function sameFreshness(a: Freshness, b: Freshness): boolean {
  if (a.state !== 'partial' || b.state !== 'partial') return a.state === b.state;
  return a.held === b.held && a.of === b.of;
}

/**
 * The layers with their freshness set from a preview and the last run; a
 * layer whose freshness is unchanged is the same object.
 */
export function withFreshness(
  layers: EchogramLayer[],
  preview: Preview | null,
  computed: ComputedSteps | null,
): EchogramLayer[] {
  const steps = new Map((preview?.steps ?? []).map((step) => [step.step_id, step]));
  return layers.map((layer) => {
    const freshness = layerFreshness(layer, steps.get(layer.step), computed);
    return sameFreshness(freshness, layer.freshness) ? layer : { ...layer, freshness };
  });
}

/** What becomes of a step's result: kept by every run, only by a run to it, or never. */
export type SaveKind = 'checkpoint' | 'memory' | 'never' | 'unknown';

/**
 * Whether a step's result is kept: from the recipe's checkpoint setting when
 * it says always or never, else from the step's preview entry.
 *
 * The preview says memory-only for a checkpoint this run leaves out (pruned,
 * skipped, untargeted), so the recipe decides first. A step the preview pins
 * is a run target saved only because it is one, so it is not a checkpoint.
 *
 * Args:
 *   step: The step in the current preview.
 *   recipeCheckpoint: The recipe's checkpoint setting, for a step the preview lacks.
 */
export function saveKind(step: StepPreview | undefined, recipeCheckpoint: string | null = null): SaveKind {
  const recipe = step ? step.recipe_checkpoint : recipeCheckpoint;
  if (recipe === 'never') return 'never';
  if (recipe === 'always') return 'checkpoint';
  if (!step) return 'unknown';
  return step.pinned || step.checkpoint !== 'will-save' ? 'memory' : 'checkpoint';
}

/**
 * Why a step that holds no result for its current hash draws nothing.
 *
 * Args:
 *   step: The step id.
 *   kind: What becomes of its result (saveKind).
 *   note: The server's note on the lookup, put after the first sentence.
 */
export function unsavedText(step: string, kind: SaveKind, note: string | null = null): string {
  const why = note ? ` (${note})` : '';
  if (kind === 'never') return `${step} is never saved (checkpoint: never in the recipe), so it can't be drawn.`;
  if (kind === 'memory') {
    return (
      `${step} isn't a checkpoint, so only a run to it saves it${why}. ` +
      `Run to ${step} to draw it, or make it a checkpoint so every run saves it.`
    );
  }
  return `${step} has no saved result yet${why}. Run to ${step} to draw it.`;
}

/** Said when the result a layer drew, at the same hash, is gone. */
export function goneText(step: string): string {
  return `${step} is not saved any more.`;
}

/** Sources the panel holds that no layer of its stack reads any more. */
export function sourcesWithoutLayers(sourceIds: string[], layers: Pick<Layer, 'source'>[]): string[] {
  const read = new Set(layers.map((layer) => layer.source));
  return sourceIds.filter((id) => !read.has(id));
}

/**
 * Saved panel settings fitted to the sources open now.
 *
 * Sources the panel lacks are left out, since applying them would open them
 * again from their old URLs behind the store's back. A source open now that
 * the saved stack does not read (added after the settings were saved) keeps
 * the layers it has now, so applying the settings does not drop it.
 */
export function settingsFor(saved: ViewSettings, current: ViewSettings | undefined, open: string[]): ViewSettings {
  // A layer naming no source reads the first.
  const source = (layer: { source?: string }) => layer.source ?? open[0];
  const kept = new Set(open);
  const layers = saved.layers.filter((layer) => kept.has(source(layer)));
  const covered = new Set(layers.map(source));
  const added = (current?.layers ?? []).filter((layer) => kept.has(source(layer)) && !covered.has(source(layer)));
  return {
    ...saved,
    store: undefined,
    sources: current?.sources?.filter((source) => kept.has(source.id)),
    layers: [...layers, ...added],
  };
}

/** A layer as it is saved across a reload. */
export function savedLayer(layer: EchogramLayer): SavedLayer {
  const { step, output, variable, label, stepHash, addedAt } = layer;
  return { step, output, variable, label, stepHash, addedAt };
}

/** A saved layer, back as a row waiting to be drawn. */
export function restoredLayer(saved: SavedLayer): EchogramLayer {
  return {
    ...saved,
    id: layerId(saved.step, saved.output, saved.variable),
    resolution: null,
    state: 'loading',
    error: null,
    freshness: { state: 'unknown' },
    drawnAfter: null,
  };
}

/**
 * Whether an error from the panel says a mount is unknown, as after a server restart.
 *
 * A chunk read names its URL and status (`410 Gone for .../mount/<m>/...`). A
 * header read says only the server's error code: `gone`, or `not_found` from
 * a server older than EchogramGL's 410 for an unknown describe mount (the
 * panel reads no other JSON, so `not_found` there is always a header). A
 * header that is not JSON names its URL with the status.
 */
export function isGone(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  if (message === 'gone' || message === 'not_found') return true;
  if (/\b410\b/.test(message) || message.includes('unknown mount')) return true;
  return /\b404\b/.test(message) && message.includes('/echogram/api/describe/');
}

/**
 * Why a step's resolution draws nothing, in a sentence: each output's reason,
 * as EchogramGL gives it, or EchogramGL's own words when nothing was found.
 */
export function missText(resolution: Resolution): string {
  if (resolution.status !== 'found') return describeMiss(resolution);
  const reasons = (resolution.outputs ?? [])
    .filter((output) => !output.kind)
    .map((output) => `${output.name}: ${output.reason ?? 'not drawable'}`);
  return `${resolution.step} has no echogram to draw${reasons.length ? ` (${reasons.join('; ')})` : ''}.`;
}

/** What tells a layer from the other layers of its step: a non first output, a non default variable. */
function suffix(layer: EchogramLayer): string {
  const outputs = layer.resolution?.outputs ?? [];
  const output = outputs.find((entry) => entry.name === layer.output);
  const parts: string[] = [];
  if (outputs.length && outputs[0].name !== layer.output) parts.push(layer.output);
  if (layer.variable && output?.default && layer.variable !== output.default) parts.push(layer.variable);
  return parts.join(':');
}

/**
 * Each layer's label, unique among the layers: the step alone for its first
 * output's default variable when it is the step's only layer, else the step
 * with what tells the layer apart (`embed_results:gridded_results`,
 * `embed_results:Sv`). A layer not yet resolved keeps the label it has.
 */
export function withLabels(layers: EchogramLayer[]): EchogramLayer[] {
  const perStep = new Map<string, number>();
  for (const layer of layers) perStep.set(layer.step, (perStep.get(layer.step) ?? 0) + 1);
  const named = layers.map((layer) => {
    if (!layer.resolution) return layer.label;
    let tail = suffix(layer);
    if (!tail && perStep.get(layer.step)! > 1) tail = layer.variable ?? layer.output;
    return tail ? `${layer.step}:${tail}` : layer.step;
  });
  const seen = new Map<string, number>();
  for (const label of named) seen.set(label, (seen.get(label) ?? 0) + 1);
  return layers.map((layer, index) => {
    const full = [layer.step, layer.output, layer.variable].filter(Boolean).join(':');
    const label = seen.get(named[index])! > 1 ? full : named[index];
    return label === layer.label ? layer : { ...layer, label };
  });
}

/** "3 datasets, 1 empty", "a pyramid": what a resolution draws, for a layer row. */
export function foundText(resolution: Resolution | null): string {
  if (!resolution) return '';
  if (resolution.kind === 'pyramid') return 'pyramid';
  const count = resolution.instances?.length ?? 0;
  const empty = resolution.empty ? `, ${resolution.empty} empty` : '';
  return `${count} dataset${count === 1 ? '' : 's'}${empty}`;
}

/** The tiers a resolution read, as the diagram's letters. */
export function tierLetters(resolution: Resolution | null): string {
  const tiers = new Set((resolution?.instances ?? []).map((instance) => instance.tier).filter(Boolean));
  return [...tiers].map((tier) => (tier === 'survey' ? 'S' : 'U')).join('');
}
