/**
 * The echogram pane's layer rules, kept pure so the store and the tests share them.
 *
 * A layer is one output variable of one step: a value drawn as one EchogramGL
 * source, or a line drawn over the values, read from each instance's mount.
 */
import {
  describeMiss,
  lineVariables,
  type Choice,
  type Layer,
  type LineData,
  type LineSpec,
  type OutputVariable,
  type Resolution,
  type ViewSettings,
} from 'aa-si-echogram-gl';
import type { Preview, StepPreview } from '../api/models';
import { partlyHeld } from '../runs/badges';
import type { RunState, StepRunState } from '../runs/reducer';

/** Where the client server mounts EchogramGL's routes, relative to the page. */
export const ECHOGRAM_ROOT = '/echogram/';

/** A value variable drawn as a panel source, or a line drawn over the values. */
export type LayerKind = 'value' | 'line';

export interface EchogramLayer {
  /** The panel's source id: `sid/step/output/var`. A line's specs add `#<instance>`. */
  id: string;
  /** The session the layer belongs to: its tab, its artifacts route, its preview. */
  sid: string;
  kind: LayerKind;
  step: string;
  output: string;
  /** The variable drawn; for a line, the line's name. */
  variable: string | null;
  /**
   * What the layer is called in its session: the step, and the variable when
   * it is not the default. The list adds the session's title (rowLabel).
   */
  label: string;
  /** The step's hash when the layer was added or last refreshed. */
  stepHash: string;
  /**
   * What the server resolved and mounted; null until fetched after a reload.
   * For a line, the output's default value, whose instances' mounts serve the line.
   */
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
  /** A line's colour, `#rrggbb`. */
  color?: string;
  /** A line's width in device pixels. */
  width?: number;
  /** Whether a line is drawn. */
  visible?: boolean;
}

/** A line row. Its look is set whenever one is added or restored. */
export type LineLayer = EchogramLayer & { kind: 'line'; color: string; width: number; visible: boolean };

/** Whether a row is a line. */
export function isLineLayer(layer: EchogramLayer): layer is LineLayer {
  return layer.kind === 'line';
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

/** What is kept of a layer across a page reload. A row without a `kind` is a value. */
export type SavedLayer = Pick<
  EchogramLayer,
  'sid' | 'step' | 'output' | 'variable' | 'label' | 'stepHash' | 'addedAt' | 'color' | 'width' | 'visible'
> & { kind?: 'line' };

/** A layer's source id. Layers belong to their session, so it leads the id. */
export function layerId(sid: string, step: string, output: string, variable: string | null): string {
  return variable ? `${sid}/${step}/${output}/${variable}` : `${sid}/${step}/${output}`;
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

/** What one session's freshness is read from: its run all preview and its last finished run. */
export interface SessionTracking {
  preview: Preview | null;
  computed: ComputedSteps | null;
}

/** Each open session's tracking, by sid. */
export type TrackingBySession = Record<string, SessionTracking>;

/**
 * The layers with their freshness set from their own session's preview and
 * last run; a layer whose freshness is unchanged is the same object.
 */
export function withFreshness(layers: EchogramLayer[], bySession: TrackingBySession): EchogramLayer[] {
  const steps = new Map<string, Map<string, StepPreview>>();
  const stepsOf = (sid: string) => {
    let found = steps.get(sid);
    if (!found) {
      found = new Map((bySession[sid]?.preview?.steps ?? []).map((step) => [step.step_id, step]));
      steps.set(sid, found);
    }
    return found;
  };
  return layers.map((layer) => {
    const computed = bySession[layer.sid]?.computed ?? null;
    const freshness = layerFreshness(layer, stepsOf(layer.sid).get(layer.step), computed);
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
 * the saved settings do not name (added after they were saved) keeps the
 * layers it has now, so applying the settings does not drop it. A source they
 * name without a layer stays without one.
 */
export function settingsFor(saved: ViewSettings, current: ViewSettings | undefined, open: string[]): ViewSettings {
  // A layer naming no source reads the first.
  const source = (layer: { source?: string }) => layer.source ?? open[0];
  const kept = new Set(open);
  const layers = saved.layers.filter((layer) => kept.has(source(layer)));
  const covered = new Set([...layers.map(source), ...(saved.sources ?? []).map((entry) => entry.id)]);
  const added = (current?.layers ?? []).filter((layer) => kept.has(source(layer)) && !covered.has(source(layer)));
  return {
    ...saved,
    store: undefined,
    sources: current?.sources?.filter((source) => kept.has(source.id)),
    layers: [...layers, ...added],
  };
}

/** The variable a value row draws, as its resolution lists it. */
function drawnVariable(layer: EchogramLayer): OutputVariable | undefined {
  const output = layer.resolution?.outputs?.find((entry) => entry.name === layer.output);
  const name = layer.variable ?? output?.default;
  return output?.variables?.find((variable) => variable.name === name);
}

/**
 * The panel's settings with one more layer on top, reading a row's source.
 *
 * The layer reads the first channel no layer of the source reads, else the
 * first, and is coloured with the cluster palette for labels, else with the
 * view's colormap. The window is left out, so applying the result keeps the
 * view where it is rather than snapping it to whole pings.
 *
 * Args:
 *   settings: The panel's settings now.
 *   layer: The value row whose source the new layer reads.
 */
export function withLayerFor(settings: ViewSettings, layer: EchogramLayer): ViewSettings {
  const source = layer.id;
  const variable = drawnVariable(layer);
  const used = new Set(settings.layers.filter((entry) => entry.source === source).map((entry) => entry.channel));
  let channel = 0;
  while (used.has(channel)) channel += 1;
  // A variable with no channel axis has one channel.
  if (variable && channel >= (variable.channels ?? 1)) channel = 0;
  const ids = new Set(settings.layers.map((entry) => entry.id));
  let n = 1;
  while (ids.has(`${source}-layer-${n}`)) n += 1;
  const color = variable?.kind === 'labels' ? { palette: 'cluster' } : { colormap: settings.colormap };
  return {
    ...settings,
    window: undefined,
    layers: [...settings.layers, { id: `${source}-layer-${n}`, source, channel, color }],
  };
}

/** A layer as it is saved across a reload; a line keeps its kind and style. */
export function savedLayer(layer: EchogramLayer): SavedLayer {
  const { sid, step, output, variable, label, stepHash, addedAt } = layer;
  const saved: SavedLayer = { sid, step, output, variable, label, stepHash, addedAt };
  if (layer.kind !== 'line') return saved;
  return { ...saved, kind: 'line', color: layer.color, width: layer.width, visible: layer.visible };
}

const HEX_COLOR = /^#[0-9a-f]{6}$/i;

/** A saved line's style, with the default for each part missing or malformed. */
function savedStyle(entry: Record<string, unknown>, name: string): Pick<SavedLayer, 'color' | 'width' | 'visible'> {
  const { color, width, visible } = entry;
  return {
    color: typeof color === 'string' && HEX_COLOR.test(color) ? color : defaultLineColor(name),
    width: typeof width === 'number' && width > 0 && Number.isFinite(width) ? width : LINE_WIDTH,
    visible: typeof visible === 'boolean' ? visible : true,
  };
}

/**
 * Saved rows read from storage, tolerating missing fields.
 *
 * A row needs a `step`, an `output` and a `sid` (its own, else
 * `fallbackSid`); `variable` defaults to null, `label` to the step,
 * `stepHash` to '' and `addedAt` to 0. A row is a value unless its `kind`
 * says line; a line needs its variable, and its style defaults as when it
 * was added. Anything else is dropped.
 *
 * Args:
 *   value: The stored `layers` value.
 *   fallbackSid: The session of rows saved before rows carried one.
 */
export function parseSavedLayers(value: unknown, fallbackSid: string | null = null): SavedLayer[] {
  if (!Array.isArray(value)) return [];
  const text = (field: unknown) => (typeof field === 'string' && field ? field : null);
  return value.flatMap((row: unknown): SavedLayer[] => {
    if (!row || typeof row !== 'object') return [];
    const entry = row as Record<string, unknown>;
    const sid = text(entry.sid) ?? fallbackSid;
    const step = text(entry.step);
    const output = text(entry.output);
    if (!sid || !step || !output) return [];
    const line = entry.kind === 'line';
    const variable = text(entry.variable);
    if (line && !variable) return [];
    const saved: SavedLayer = {
      sid,
      step,
      output,
      variable,
      label: text(entry.label) ?? step,
      stepHash: typeof entry.stepHash === 'string' ? entry.stepHash : '',
      addedAt: typeof entry.addedAt === 'number' ? entry.addedAt : 0,
    };
    return [line ? { ...saved, kind: 'line', ...savedStyle(entry, variable!) } : saved];
  });
}

/** A saved layer, back as a row waiting to be drawn. */
export function restoredLayer(saved: SavedLayer): EchogramLayer {
  return {
    ...saved,
    kind: saved.kind ?? 'value',
    id: layerId(saved.sid, saved.step, saved.output, saved.variable),
    resolution: null,
    state: 'loading',
    error: null,
    freshness: { state: 'unknown' },
    drawnAfter: null,
  };
}

/** The HTTP status an EchogramGL error carries, when its build records one. */
function statusOf(error: unknown): number | undefined {
  const status = (error as { status?: unknown } | null)?.status;
  return typeof status === 'number' ? status : undefined;
}

/**
 * Whether an error says a mount is unknown, as after a server restart.
 *
 * A chunk read names its URL and status (`410 Gone for .../mount/<m>/...`). A
 * JSON read says the server's error code, `gone`. With `header`, the error is
 * from a describe header read, where `not_found` is a server older than
 * EchogramGL's 410 for an unknown mount, and a header that is not JSON names
 * its URL with the status. Elsewhere `not_found` is something the mount lacks,
 * such as a line route's variable.
 */
export function isGone(error: unknown, { header = false } = {}): boolean {
  const message = error instanceof Error ? error.message : String(error);
  if (message === 'gone' || statusOf(error) === 410) return true;
  if (/\b410\b/.test(message) || message.includes('unknown mount')) return true;
  if (!header) return false;
  if (message === 'not_found') return true;
  return /\b404\b/.test(message) && message.includes('/echogram/api/describe/');
}

/** Said on a line row while no value row is left to draw it over. */
export const NEEDS_VALUE = 'Lines are drawn over a value layer; add one.';

/**
 * Why a line row's line could not be read, for an error that is not a
 * forgotten mount: what is missing for a 404, else the server's message when
 * the error carries one, else the error itself or, for a 400, what it means.
 *
 * Args:
 *   step: The row's step.
 *   name: The line's name.
 *   error: What fetching one instance's line threw.
 */
export function lineErrorText(step: string, name: string, error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  if (message === 'not_found') return `${step} has no line '${name}' in one of its files.`;
  const detail = (error as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string' && detail) return `${step}, line '${name}': ${detail}`;
  if (message === 'bad_request') return `${step}'s '${name}' is not a line in one of its files.`;
  return message;
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

/** A key of a session and a name in it, since two sessions may share a step id. */
function inSession(layer: EchogramLayer, name: string): string {
  return `${layer.sid}\n${name}`;
}

/**
 * Each layer's label, unique among its session's layers: the step alone for
 * its first output's default variable when it is the step's only layer, else
 * the step with what tells the layer apart (`embed_results:gridded_results`,
 * `embed_results:Sv`). A layer not yet resolved keeps the label it has.
 */
export function withLabels(layers: EchogramLayer[]): EchogramLayer[] {
  const perStep = new Map<string, number>();
  for (const layer of layers) {
    const key = inSession(layer, layer.step);
    perStep.set(key, (perStep.get(key) ?? 0) + 1);
  }
  const named = layers.map((layer) => {
    if (!layer.resolution) return layer.label;
    let tail = suffix(layer);
    if (!tail && perStep.get(inSession(layer, layer.step))! > 1) tail = layer.variable ?? layer.output;
    return tail ? `${layer.step}:${tail}` : layer.step;
  });
  const seen = new Map<string, number>();
  layers.forEach((layer, index) => {
    const key = inSession(layer, named[index]);
    seen.set(key, (seen.get(key) ?? 0) + 1);
  });
  return layers.map((layer, index) => {
    const full = [layer.step, layer.output, layer.variable].filter(Boolean).join(':');
    const label = seen.get(inSession(layer, named[index]))! > 1 ? full : named[index];
    return label === layer.label ? layer : { ...layer, label };
  });
}

/** Whether the layers come from more than one session. */
export function spansSessions(layers: Pick<EchogramLayer, 'sid'>[]): boolean {
  return new Set(layers.map((layer) => layer.sid)).size > 1;
}

/**
 * A row's text: `<title>: <label>` when the layers span sessions or the row's
 * session is not the tab in front, else the label. A session without a known
 * title is named by its sid.
 */
export function rowLabel(
  layer: EchogramLayer,
  titles: Record<string, string>,
  spanning: boolean,
  front: string | null,
): string {
  return spanning || layer.sid !== front ? `${titles[layer.sid] ?? layer.sid}: ${layer.label}` : layer.label;
}

/**
 * The rows as the list shows them: the front session's first, then each
 * other session's in the order its first row was added, each in stored order.
 */
export function orderedRows(layers: EchogramLayer[], front: string | null): EchogramLayer[] {
  const groups = new Map<string, EchogramLayer[]>();
  if (front !== null) groups.set(front, []);
  for (const layer of layers) {
    const group = groups.get(layer.sid);
    if (group) group.push(layer);
    else groups.set(layer.sid, [layer]);
  }
  return [...groups.values()].flat();
}

/** "3 datasets, 1 empty", "a pyramid", "3 lines": what a resolution draws, for a layer row. */
export function foundText(resolution: Resolution | null, kind: LayerKind = 'value'): string {
  if (!resolution) return '';
  const count = resolution.instances?.length ?? 0;
  if (kind === 'line') return `${count} line${count === 1 ? '' : 's'}`;
  if (resolution.kind === 'pyramid') return 'pyramid';
  const empty = resolution.empty ? `, ${resolution.empty} empty` : '';
  return `${count} dataset${count === 1 ? '' : 's'}${empty}`;
}

/** The tiers a resolution read, as the diagram's letters. */
export function tierLetters(resolution: Resolution | null): string {
  const tiers = new Set((resolution?.instances ?? []).map((instance) => instance.tier).filter(Boolean));
  return [...tiers].map((tier) => (tier === 'survey' ? 'S' : 'U')).join('');
}

/** A line's width when it is added, in device pixels. */
export const LINE_WIDTH = 2;

/** The widths a line row offers. */
export const LINE_WIDTHS = [1, 2, 3, 4];

/** What a line row changes of how its line is drawn. */
export type LineStyle = Partial<Pick<EchogramLayer, 'color' | 'width' | 'visible'>>;

/** A dive's fitted depth, and the band around it. */
export const LINE_COLORS = { fit: '#e040fb', band: '#00e5ff' };

/** The theme's accent, for a line named neither, when the page's own is not known. */
export const LINE_ACCENT = '#7986cb';

/**
 * A line's colour when it is added: `*fit*` magenta, `*u99*` and `*l99*`
 * cyan, else the accent, when it is a `#rrggbb` colour.
 */
export function defaultLineColor(name: string, accent: string = LINE_ACCENT): string {
  const lower = name.toLowerCase();
  if (lower.includes('fit')) return LINE_COLORS.fit;
  if (lower.includes('u99') || lower.includes('l99')) return LINE_COLORS.band;
  return HEX_COLOR.test(accent) ? accent : LINE_ACCENT;
}

/** A line a resolution offers beside its value. */
export interface OfferedLine {
  output: string;
  name: string;
  verticalRef: 'depth' | 'range';
  units: string | null;
  longName: string | null;
}

/** The lines a resolution's outputs offer beside their values. */
export function linesOf(resolution: Resolution): OfferedLine[] {
  return lineVariables(resolution).map(({ output, variable }) => ({
    output,
    name: variable.name,
    verticalRef: variable.verticalReference ?? 'depth',
    units: variable.units ?? null,
    longName: variable.longName ?? null,
  }));
}

/** Whether a choice is a line, drawn over a value rather than as a source. */
export function isLineChoice(choice: Choice): boolean {
  return choice.kind === 'line';
}

/** What a mapped instance is called after its line's label: the last part of its item. */
function itemName(item: unknown): string {
  const text = String(item);
  return text.split('/').pop() || text;
}

/**
 * A line layer's specs for the panel, one per instance whose line was
 * fetched, ids `<layer id>#<instance>`. With several instances each label
 * names its item, so the readout says which piece it reads.
 *
 * Args:
 *   layer: The line layer.
 *   fetched: Its lines by instance id.
 *   label: What the row is called.
 */
export function lineSpecsFor(
  layer: LineLayer,
  fetched: Record<string, LineData> | undefined,
  label: string,
): LineSpec[] {
  const instances = layer.resolution?.instances ?? [];
  return instances.flatMap((instance) => {
    const line = fetched?.[instance.id];
    if (!line) return [];
    const named = instances.length > 1 && instance.item != null ? `${label} (${itemName(instance.item)})` : label;
    return [
      {
        id: `${layer.id}#${instance.id}`,
        label: named,
        pingTime: line.pingTime,
        value: line.value,
        verticalRef: line.verticalRef,
        color: layer.color,
        width: layer.width,
        opacity: 1,
        visible: layer.visible,
      },
    ];
  });
}
