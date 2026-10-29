/**
 * The echogram pane's layer rules, kept pure so the store and the tests share them.
 *
 * A layer is one output variable of one step, drawn as one EchogramGL source.
 */
import { describeMiss, type Layer, type Resolution, type ViewSettings } from 'aa-si-echogram-gl';
import type { Preview } from '../api/models';
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
  /** The step's hash moved, or a run computed it again, since the layer was drawn. */
  outOfDate: boolean;
  /** When the layer was added or refreshed, ms since 1970. */
  addedAt: number;
}

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

/** Each step's hash in a preview. */
export function previewHashes(preview: Preview | null | undefined): Record<string, string> {
  return Object.fromEntries((preview?.steps ?? []).map((step) => [step.step_id, step.step_hash]));
}

function computedStep(step: StepRunState): boolean {
  return step.status === 'computed' || step.instances.computed > 0;
}

/** The steps a finished run computed, and when it finished. */
export interface ComputedSteps {
  steps: Set<string>;
  at: number;
}

/**
 * What a finished run computed, and when it finished by the server's clock:
 * its run_finished event, else the last event it logged. Null when the run
 * holds no time, since a guess would mark a layer drawn after it.
 */
export function computedSteps(run: RunState | null): ComputedSteps | null {
  if (!run?.finished) return null;
  const seconds = run.finishedAt ?? run.logs.reduce((latest, entry) => Math.max(latest, entry.time), -Infinity);
  if (!Number.isFinite(seconds)) return null;
  const steps = new Set(
    Object.entries(run.steps)
      .filter(([, step]) => computedStep(step))
      .map(([id]) => id),
  );
  return { steps, at: seconds * 1000 };
}

/**
 * Layers drawn from a result that is no longer the current one.
 *
 * Out of date when the preview's hash for the step differs from the layer's,
 * or the last finished run computed the step after the layer was drawn.
 */
export function outOfDateLayers(
  layers: EchogramLayer[],
  hashes: Record<string, string>,
  computed: ComputedSteps | null,
): Set<string> {
  const ids = new Set<string>();
  for (const layer of layers) {
    const hash = hashes[layer.step];
    const moved = Boolean(hash) && hash !== layer.stepHash;
    const rerun = Boolean(computed?.steps.has(layer.step)) && computed!.at > layer.addedAt;
    if (moved || rerun) ids.add(layer.id);
  }
  return ids;
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
    outOfDate: false,
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
