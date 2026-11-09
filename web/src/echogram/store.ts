/**
 * The echogram pane's layers, and the EchogramGL panel they are drawn in.
 *
 * The store is the list of what the user asked to see; the panel draws it.
 * There is one echogram for every open session tab: each layer belongs to its
 * session, and bringing another tab to the front keeps every layer drawn.
 * Layers outlive the pane: closing it detaches the panel, reopening draws the
 * layers again, and a page reload restores them from localStorage. A line
 * layer is no source: its lines are fetched per instance and handed to the
 * panel, all of them at once, whenever one changes.
 */
import {
  choices,
  draws,
  fetchLine,
  isPieceSet,
  lineUrl,
  preferred,
  sameCheckpoint,
  toSpec,
  type Choice,
  type EchogramPanel,
  type Layer,
  type LineData,
  type Resolution,
  type ViewSettings,
} from 'aa-si-echogram-gl';
import { create } from 'zustand';
import type { Preview, StepArtifacts } from '../api/models';
import { fetchStepArtifacts, type ArtifactChoice } from '../api/queries';
import { savedOnDisk } from '../runs/badges';
import type { RunState } from '../runs/reducer';
import { useSessionStore } from '../state/session';
import { readStored, readStoredJson, writeStored } from '../state/storage';
import {
  asResolution,
  defaultLineColor,
  ECHOGRAM_ROOT,
  goneText,
  isGone,
  isLineChoice,
  isLineLayer,
  layerId,
  LINE_WIDTH,
  lineErrorText,
  lineSpecsFor,
  linesOf,
  missText,
  NEEDS_VALUE,
  parseSavedLayers,
  restoredLayer,
  rowLabel,
  savedLayer,
  saveKind,
  settingsFor,
  sourcesWithoutLayers,
  spansSessions,
  unsavedText,
  withFreshness,
  withLabels,
  type ComputedSteps,
  type EchogramLayer,
  type LineStyle,
  type SavedLayer,
  type TrackingBySession,
} from './layers';

/** What the store needs of an EchogramGL panel, so tests can pass a fake. */
export type PanelLike = Pick<
  EchogramPanel,
  'addSource' | 'replaceSource' | 'removeSource' | 'sourceIds' | 'settingsObject' | 'applySettings' | 'retry' | 'setLines'
>;

/** A step whose saved output offers several things to draw, waiting for the user to pick. */
export interface PendingChoice {
  sid: string;
  step: string;
  stepHash: string;
  resolution: Resolution;
  choices: Choice[];
  /** The choice selected first. */
  selected: number;
}

/** A step not saved yet: the offer to run to it, then the run that will save it. */
export interface PendingRun {
  /** The session the run is in, the tab in front when it was offered. */
  sid: string;
  step: string;
  /** Null until the user starts the run. */
  runId: string | null;
  /** The row whose Run button started the run; it is refreshed when the run finishes. */
  layer?: string;
  /** The step is not a checkpoint, so the offer can make it one. */
  memoryOnly?: boolean;
}

/** What a caller knows of a step that an eye click alone does not. */
export interface ShowOptions {
  /** The recipe's checkpoint setting, used when the preview has no entry for the step. */
  checkpoint?: string | null;
  /** Shown after the run started to save it: draw the default, ask nothing. */
  afterRun?: boolean;
}

/** Said on an eye click when the pane cannot draw at all. */
export const NEEDS_WEBGPU = 'The echogram pane needs WebGPU; see the message above.';

export interface EchogramState {
  /** The tab in front; an eye click's answer that comes after a switch says nothing there. */
  front: string | null;
  /** Whether the pane could get a WebGPU device; rows are only added while it is not refused. */
  gpu: 'unknown' | 'ready' | 'unavailable';
  layers: EchogramLayer[];
  choice: PendingChoice | null;
  pendingRun: PendingRun | null;
  /** Why the last eye click drew nothing, or what is going on. */
  message: string | null;
  /** The step being looked up after an eye click. */
  busy: string | null;
  /** The panel's settings, kept across closing the pane and a reload. */
  settings: ViewSettings | null;
  panel: PanelLike | null;
  /** Each session's run all preview and last run, which its rows' freshness and unsaved messages read. */
  bySession: TrackingBySession;
  /** Session titles by sid, for the panel's source names of rows named with their session. */
  titles: Record<string, string>;
  /** Each line layer's fetched lines by instance id. Not saved: a reload fetches them again. */
  lines: Record<string, Record<string, LineData>>;

  /** Draw a step's saved output, after an eye click. */
  show: (sid: string, step: string, options?: ShowOptions) => Promise<void>;
  /** Add what the chooser selected; the chooser stays open for another. */
  addChoice: (choice: Choice) => Promise<void>;
  cancelChoice: () => void;
  remove: (id: string) => void;
  /** Change how a line row's line is drawn, and keep it. */
  setLineStyle: (id: string, style: LineStyle) => void;
  /** Fetch a layer's step again and draw the newer data, or retry the same. */
  refresh: (id: string) => Promise<void>;
  /** Take a session's run all preview and last run, and set its layers' freshness from them. */
  track: (sid: string, preview: Preview | null, computed: ComputedSteps | null) => void;
  setTitles: (titles: Record<string, string>) => void;
  /** The panel's layer stack changed: drop a source whose last layer the user removed there. */
  onPanelLayersChange: (layers: Layer[]) => void;
  /** An error the panel showed; a layer whose mount the server forgot is resolved again. */
  onPanelError: (error: unknown) => void;
  /** The view moved or settled: keep its settings, after a pause. */
  settingsChanged: () => void;
  /** Keep the panel's settings now. */
  captureSettings: () => void;
  /** The run to a pending step started, from the offer or from a row's Run button. */
  runStarted: (sid: string, step: string, runId: string, layer?: string) => void;
  /** A run finished; draw the pending step, or refresh its row, if it was its run and saved it. */
  runFinished: (run: RunState, start: Preview | null) => void;
  cancelRun: () => void;
  /** The offered step was made a checkpoint. */
  checkpointMade: (step: string) => void;
  attach: (panel: PanelLike) => Promise<void>;
  /** The pane has no WebGPU: drop the rows it cannot draw, keeping them saved for another browser. */
  gpuUnavailable: () => void;
  detach: () => void;
  /** Bring a session to the front: every source stays; the chooser, run offer and message go. */
  activate: (sid: string | null) => void;
  /** Drop a closed session's rows from the store and the panel. */
  removeSession: (sid: string) => void;
  /** Read the saved rows again. A test seam, used before `attach`. */
  restore: () => void;
}

/** localStorage key of every tab's layers and the panel's settings. */
export const LAYERS_KEY = 'rc.echogram.layers';

/** The key a session's layers were kept under before they shared one, imported once. */
export function legacyKey(sid: string): string {
  return `rc.echogram.${sid}`;
}

interface Saved {
  layers: SavedLayer[];
  settings: ViewSettings | null;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function parseSaved(value: unknown, fallbackSid: string | null): Saved {
  const saved = isObject(value) ? value : {};
  const settings = isObject(saved.settings) ? (saved.settings as unknown as ViewSettings) : null;
  return { layers: parseSavedLayers(saved.layers, fallbackSid), settings };
}

/**
 * The saved rows and settings. Without `rc.echogram.layers`, the front
 * session's old key is imported with its sid set, written under the new key
 * (empty or not, so the import happens once) and removed. A closed tab's
 * rows are kept: its session still exists.
 */
function loadSaved(front: string | null): Saved {
  if (readStored(LAYERS_KEY) === null && front) {
    const saved = parseSaved(readStoredJson(legacyKey(front)), front);
    writeStored(LAYERS_KEY, JSON.stringify(saved));
    writeStored(legacyKey(front), null);
    return saved;
  }
  return parseSaved(readStoredJson(LAYERS_KEY), null);
}

/** Drop a session's saved rows, leaving every other row and the settings. */
function dropSaved(sid: string): void {
  const saved = parseSaved(readStoredJson(LAYERS_KEY), null);
  const layers = saved.layers.filter((layer) => layer.sid !== sid);
  writeStored(LAYERS_KEY, JSON.stringify({ layers, settings: saved.settings }));
}

/** The saved rows, as rows waiting to be drawn, and the settings. */
function savedState(): { layers: EchogramLayer[]; settings: ViewSettings | null } {
  const saved = loadSaved(useSessionStore.getState().sid);
  return { layers: saved.layers.map(restoredLayer), settings: saved.settings };
}

/** How long the view must settle before its settings are kept. */
const SETTINGS_DELAY_MS = 1000;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

/** The theme's accent as the page has it now, which a line named neither fit nor band is drawn in. */
function themeAccent(): string {
  return getComputedStyle(document.documentElement).getPropertyValue('--rc-accent').trim();
}

/** Whether a resolution still offers a line layer's line. */
function offersLine(resolution: Resolution, layer: EchogramLayer): boolean {
  return linesOf(resolution).some((line) => line.output === layer.output && line.name === layer.variable);
}

/**
 * The resolution an artifacts answer carries, or why there is none: the
 * server cannot draw echograms, or could not read the artifacts. With
 * `drawn`, a resolution that draws nothing is a miss too, in EchogramGL's words.
 */
function readAnswer(found: StepArtifacts, drawn = false): { resolution: Resolution } | { miss: string } {
  const block = found.echogram;
  if (!block.available) return { miss: block.reason ?? 'This server cannot draw echograms.' };
  if (!block.resolution) return { miss: block.reason ?? `${found.step_id} has no saved output to draw.` };
  const resolution = asResolution(block.resolution);
  if (drawn && (resolution.status !== 'found' || !resolution.kind)) return { miss: missText(resolution) };
  return { resolution };
}

/** The mount ids a resolution is served under. */
function mountsOf(resolution: Resolution | null): string[] {
  if (!resolution) return [];
  return [resolution.mount, ...(resolution.instances ?? []).map((instance) => instance.mount)].filter(
    (mount): mount is string => Boolean(mount),
  );
}

/**
 * A layer fetched this recently that the server says it does not know is not
 * fetched again: its mounts were just made, so asking again would only loop.
 */
const FRESH_MS = 5000;

// Panel calls run one at a time, in order, so a restore and an eye click never interleave.
let queue: Promise<void> = Promise.resolve();
let working = 0;
function enqueue(task: () => Promise<void>): Promise<void> {
  const run = async () => {
    working += 1;
    try {
      await task();
    } finally {
      working -= 1;
    }
  };
  const next = queue.then(run, run);
  queue = next.catch(() => {});
  return next;
}

/** The panel's newest layer stack, as onPanelLayersChange last heard it. */
let reported: Layer[] | null = null;
let settingsTimer: ReturnType<typeof setTimeout> | undefined;

export const useEchogramStore = create<EchogramState>((set, get) => {
  // Every change to the rows goes through here, so labels stay unique and freshness current.
  const setLayers = (change: (layers: EchogramLayer[]) => EchogramLayer[]) =>
    set((state) => ({ layers: withFreshness(withLabels(change(state.layers)), state.bySession) }));
  const previewOf = (sid: string, step: string) =>
    get().bySession[sid]?.preview?.steps.find((entry) => entry.step_id === step);
  const patch = (id: string, change: Partial<EchogramLayer>) =>
    setLayers((layers) => layers.map((layer) => (layer.id === id ? { ...layer, ...change } : layer)));
  /** Set what the pane says about a call, unless another tab came to the front meanwhile. */
  const say = (sid: string, change: Partial<EchogramState>) => {
    if (get().front === sid) set(change);
  };

  /** What the panel calls a layer, as its row does; the bare label until its session's title is known. */
  const nameOf = (layer: EchogramLayer) => {
    const { titles, layers, front } = get();
    return layer.sid in titles ? rowLabel(layer, titles, spansSessions(layers), front) : layer.label;
  };
  /** What the panel opens for a layer, named as its row is. */
  const specFor = (layer: EchogramLayer, resolution: Resolution) => {
    const spec = toSpec(ECHOGRAM_ROOT, resolution);
    if (isPieceSet(spec)) spec.name = nameOf(layer);
    return spec;
  };
  const find = (id: string) => get().layers.find((layer) => layer.id === id);
  /** A session's last finished run, which a layer fetched now was drawn after. */
  const lastRun = (sid: string) => get().bySession[sid]?.computed?.runId ?? null;

  // The key stays when the rows go, so the one time import of an old key never runs again.
  const persist = () => {
    const { layers, settings } = get();
    writeStored(LAYERS_KEY, JSON.stringify({ layers: layers.map(savedLayer), settings }));
  };

  /** A layer the server forgot the mounts of, as after a restart: fetch it again, unless it is fresh. */
  const resolveAgain = (id: string, error: unknown) => {
    const layer = find(id);
    if (!layer || layer.state === 'refreshing') return;
    if (Date.now() - layer.addedAt < FRESH_MS) {
      patch(id, { state: 'error', error: errorText(error) });
      return;
    }
    void get().refresh(id);
  };

  /** Keep a line layer's fetched lines, or with null forget them. */
  const keepLines = (id: string, fetched: Record<string, LineData> | null) =>
    set((state) => {
      const lines = { ...state.lines };
      if (fetched) lines[id] = fetched;
      else delete lines[id];
      return { lines };
    });

  /** Hand the panel every line layer's lines, named as their rows are. */
  const pushLines = (panel: PanelLike) => {
    if (get().panel !== panel) return;
    const { layers, lines } = get();
    const specs = layers.filter(isLineLayer).flatMap((layer) => lineSpecsFor(layer, lines[layer.id], nameOf(layer)));
    panel.setLines(specs);
  };

  /** A drawn line row's state: shown, or saying so while no value row is left to draw it over. */
  const lineDrawn = (): Pick<EchogramLayer, 'state' | 'error'> =>
    get().layers.some((layer) => layer.kind === 'value')
      ? { state: 'shown', error: null }
      : { state: 'error', error: NEEDS_VALUE };

  /** After a value row comes or goes, set every drawn line row's state to match. */
  const markLines = () => {
    const drawn = lineDrawn();
    const marked = (layer: EchogramLayer) =>
      layer.kind === 'line' && (layer.state === 'shown' || layer.error === NEEDS_VALUE) && layer.error !== drawn.error;
    if (get().layers.some(marked)) {
      setLayers((layers) => layers.map((layer) => (marked(layer) ? { ...layer, ...drawn } : layer)));
    }
  };

  /** Fetch a line layer's line from each instance's mount, then hand the panel every line. */
  const drawLines = async (panel: PanelLike, id: string) => {
    const layer = find(id);
    if (!layer?.resolution || !layer.variable || get().panel !== panel) return;
    const { resolution, variable } = layer;
    // A row removed, or given newer data, while its lines were read says nothing of these.
    const current = () => find(id)?.resolution === resolution;
    try {
      const fetched = await Promise.all(
        (resolution.instances ?? []).map(
          async (instance) => [instance.id, await fetchLine(lineUrl(ECHOGRAM_ROOT, instance.mount, variable))] as const,
        ),
      );
      if (!current()) return;
      keepLines(id, Object.fromEntries(fetched));
      patch(id, lineDrawn());
    } catch (error) {
      if (!current()) return;
      // A line the mount lacks answers 404 or 400; only a forgotten mount is fetched again.
      if (isGone(error)) {
        patch(id, { state: 'error', error: errorText(error) });
        resolveAgain(id, error);
      } else {
        patch(id, { state: 'error', error: lineErrorText(layer.step, variable, error) });
      }
    }
    pushLines(panel);
  };

  /** Draw a layer that is not in the panel yet. */
  const draw = async (panel: PanelLike, id: string) => {
    const layer = find(id);
    if (!layer?.resolution || get().panel !== panel) return;
    try {
      await panel.addSource(id, specFor(layer, layer.resolution));
      patch(id, { state: 'shown', error: null });
    } catch (error) {
      patch(id, { state: 'error', error: errorText(error) });
      if (isGone(error, { header: true })) resolveAgain(id, error);
    }
  };

  /**
   * Fetch a layer's step for its output and variable; null after marking the
   * row with why not. A row drawn at another hash whose current hash holds
   * nothing keeps what it draws, and notes the hash, so its freshness says
   * the hash is not saved rather than offering the refresh again. A line is
   * served beside its output's default value, so only the output is asked for.
   */
  const fetchLayer = async (layer: EchogramLayer): Promise<{ found: StepArtifacts; resolution: Resolution } | null> => {
    const { sid, step } = layer;
    const line = layer.kind === 'line';
    try {
      const found = await fetchStepArtifacts(sid, step, { output: layer.output, var: line ? null : layer.variable });
      if (found.found === 0) {
        if (found.step_hash === layer.stepHash) patch(layer.id, { state: 'error', error: goneText(step) });
        else if (!layer.resolution) {
          patch(layer.id, { state: 'error', error: unsavedText(step, saveKind(previewOf(sid, step))) });
        } else {
          patch(layer.id, { missed: { stepHash: found.step_hash, after: lastRun(sid) } });
        }
        return null;
      }
      const answer = readAnswer(found, true);
      if ('miss' in answer) {
        patch(layer.id, { state: 'error', error: answer.miss });
        return null;
      }
      if (line && !offersLine(answer.resolution, layer)) {
        patch(layer.id, { state: 'error', error: `${step} has no line ${layer.variable} in ${layer.output} any more.` });
        return null;
      }
      return { found, resolution: answer.resolution };
    } catch (error) {
      patch(layer.id, { state: 'error', error: errorText(error) });
      return null;
    }
  };

  /** Set a layer to newer data and draw it in place, or retry when the data is the same. */
  const replace = (id: string, resolution: Resolution, stepHash: string) => {
    const current = find(id);
    if (!current) return Promise.resolve();
    const old = current.resolution;
    const panel = get().panel;
    patch(id, {
      resolution,
      stepHash,
      addedAt: Date.now(),
      drawnAfter: lastRun(current.sid),
      missed: undefined,
      state: panel ? 'refreshing' : 'loading',
      error: null,
    });
    persist();
    // The panel keeps drawing a line's old lines while the new ones are read; with no panel, on attach.
    if (current.kind === 'line') keepLines(id, null);
    if (!panel) return Promise.resolve();
    if (current.kind === 'line') return enqueue(() => drawLines(panel, id));
    return enqueue(async () => {
      if (get().panel !== panel) return;
      try {
        const layer = find(id)!;
        if (!panel.sourceIds.includes(id)) await panel.addSource(id, specFor(layer, resolution));
        else if (old && sameCheckpoint(old, resolution)) await panel.retry();
        else await panel.replaceSource(id, specFor(layer, resolution));
        patch(id, { state: 'shown', error: null });
      } catch (error) {
        patch(id, { state: 'error', error: errorText(error) });
      }
    });
  };

  /**
   * Add a layer for a resolution, or draw the newer data in an existing one.
   * With `line`, the layer is that line, drawn over the resolution's values.
   * A layer already drawing this very resolution is left as it is.
   */
  const add = (sid: string, step: string, stepHash: string, resolution: Resolution, line: string | null = null) => {
    if (get().gpu === 'unavailable') {
      say(sid, { message: NEEDS_WEBGPU });
      return Promise.resolve();
    }
    const variable = line ?? resolution.var ?? null;
    const id = layerId(sid, step, resolution.output ?? '', variable);
    const existing = find(id);
    if (existing?.resolution === resolution && existing.state !== 'error') return Promise.resolve();
    if (existing) return replace(id, resolution, stepHash);
    const kind = line
      ? { kind: 'line' as const, color: defaultLineColor(line, themeAccent()), width: LINE_WIDTH, visible: true }
      : { kind: 'value' as const };
    const layer: EchogramLayer = {
      id,
      sid,
      ...kind,
      step,
      output: resolution.output ?? '',
      variable,
      label: step,
      stepHash,
      resolution,
      state: 'loading',
      error: null,
      freshness: { state: 'unknown' },
      addedAt: Date.now(),
      drawnAfter: lastRun(sid),
    };
    setLayers((layers) => [...layers, layer]);
    persist();
    if (!line) markLines();
    const panel = get().panel;
    if (!panel) return Promise.resolve();
    return enqueue(() => (line ? drawLines(panel, id) : draw(panel, id)));
  };

  /** Ask for a choice's output and variable; undefined after saying why it draws nothing. */
  const lookUp = async (sid: string, step: string, choice: Choice, wanted: ArtifactChoice) => {
    say(sid, { busy: step });
    try {
      const found = await fetchStepArtifacts(sid, step, wanted);
      say(sid, { busy: null });
      const answer = readAnswer(found, true);
      if ('miss' in answer) {
        say(sid, { message: answer.miss });
        return undefined;
      }
      return { stepHash: found.step_hash, resolution: answer.resolution };
    } catch (error) {
      say(sid, { busy: null, message: `Could not draw ${choice.label}: ${errorText(error)}` });
      return undefined;
    }
  };

  /**
   * Draw a line choice over its output's default value: the resolution in
   * hand when it is that output's, else that output asked for. A step with no
   * value layer gets its default value first, so a line never draws alone.
   */
  const pickLine = async (sid: string, step: string, stepHash: string, resolution: Resolution, choice: Choice) => {
    const line = choice.var;
    if (!line) return;
    const valued = get().layers.some((layer) => layer.sid === sid && layer.step === step && layer.kind === 'value');
    if (!valued && resolution.kind) await add(sid, step, stepHash, resolution);
    if (resolution.output === choice.output) return add(sid, step, stepHash, resolution, line);
    const found = await lookUp(sid, step, choice, { output: choice.output });
    if (found) await add(sid, step, found.stepHash, found.resolution, line);
  };

  /** Draw a choice: from the resolution in hand when it draws it, else asked for again. */
  const pick = async (sid: string, step: string, stepHash: string, resolution: Resolution, choice: Choice) => {
    if (isLineChoice(choice)) return pickLine(sid, step, stepHash, resolution, choice);
    if (draws(resolution, choice)) return add(sid, step, stepHash, resolution);
    const found = await lookUp(sid, step, choice, { output: choice.output, var: choice.var ?? null });
    if (found) await add(sid, step, found.stepHash, found.resolution);
  };

  /** Draw every layer not in the panel yet, then the kept settings. Lines read before are handed over as they are. */
  const drawAll = (panel: PanelLike) =>
    enqueue(async () => {
      for (const layer of get().layers) {
        if (get().panel !== panel) return;
        if (panel.sourceIds.includes(layer.id)) continue;
        if (get().lines[layer.id]) {
          patch(layer.id, lineDrawn());
          continue;
        }
        if (!layer.resolution) {
          const fetched = await fetchLayer(layer);
          if (!fetched) continue;
          const { found, resolution } = fetched;
          const drawnAfter = lastRun(layer.sid);
          patch(layer.id, { resolution, stepHash: found.step_hash, addedAt: Date.now(), drawnAfter });
        }
        await (layer.kind === 'line' ? drawLines(panel, layer.id) : draw(panel, layer.id));
      }
      if (get().layers.some((layer) => layer.kind === 'line')) pushLines(panel);
      const settings = get().settings;
      if (!settings || get().panel !== panel || panel.sourceIds.length === 0) return;
      try {
        await panel.applySettings(settingsFor(settings, panel.settingsObject, panel.sourceIds));
      } catch {
        // Settings from an older EchogramGL, or for data that changed shape: start afresh.
        set({ settings: null });
        persist();
      }
    });

  const initial = savedState();

  return {
    front: null,
    gpu: 'unknown',
    layers: initial.layers,
    choice: null,
    pendingRun: null,
    message: null,
    busy: null,
    settings: initial.settings,
    panel: null,
    bySession: {},
    titles: {},
    lines: {},

    // An answer that comes after another tab came to the front still draws its
    // layer, which belongs to its own tab, but says and offers nothing there.
    show: async (sid, step, options = {}) => {
      if (get().gpu === 'unavailable') {
        say(sid, { message: NEEDS_WEBGPU, choice: null });
        return;
      }
      const pending = get().pendingRun;
      say(sid, {
        busy: step,
        message: null,
        choice: null,
        pendingRun: pending?.runId ? pending : null,
      });
      let found: StepArtifacts;
      try {
        found = await fetchStepArtifacts(sid, step);
      } catch (error) {
        say(sid, { busy: null, message: `Could not look up ${step}: ${errorText(error)}` });
        return;
      }
      say(sid, { busy: null });
      // A server that cannot draw says so before any offer to save something it could not draw.
      if (!found.echogram.available) {
        say(sid, { message: found.echogram.reason ?? 'This server cannot draw echograms.' });
        return;
      }
      if (found.found === 0) {
        if (options.afterRun) {
          say(sid, { message: `The run to ${step} finished, but ${step} was not saved.` });
          return;
        }
        const kind = saveKind(previewOf(sid, step), options.checkpoint ?? null);
        if (kind === 'never') {
          say(sid, { message: unsavedText(step, kind) });
          return;
        }
        const running = get().pendingRun;
        if (running?.runId) {
          say(sid, { message: `A run to ${running.step} is going. Click the eye on ${step} again when it finishes.` });
          return;
        }
        const gone = get().layers.some(
          (layer) => layer.sid === sid && layer.step === step && layer.stepHash === found.step_hash,
        );
        say(sid, {
          pendingRun: { sid, step, runId: null, memoryOnly: kind === 'memory' },
          message: gone ? goneText(step) : unsavedText(step, kind, found.note),
        });
        return;
      }
      let answer: ReturnType<typeof readAnswer>;
      try {
        answer = readAnswer(found);
      } catch (error) {
        say(sid, { message: errorText(error) });
        return;
      }
      if ('miss' in answer) {
        say(sid, { message: answer.miss });
        return;
      }
      const { resolution } = answer;
      const offered = choices(resolution);
      if (offered.length === 0) {
        say(sid, { message: missText(resolution) });
        return;
      }
      const selected = preferred(offered, resolution);
      // After a run the user asked to see the step, not to choose: draw the default.
      if (offered.length === 1 || options.afterRun) {
        await pick(sid, step, found.step_hash, resolution, offered[selected]);
        return;
      }
      say(sid, { choice: { sid, step, stepHash: found.step_hash, resolution, choices: offered, selected } });
    },

    addChoice: async (choice) => {
      const pending = get().choice;
      if (!pending) return;
      // The chooser stays open, so labels and the MVBS under them take two clicks, not two eye clicks.
      await pick(pending.sid, pending.step, pending.stepHash, pending.resolution, choice);
    },

    cancelChoice: () => set({ choice: null }),

    remove: (id) => {
      const line = find(id)?.kind === 'line';
      setLayers((layers) => layers.filter((layer) => layer.id !== id));
      persist();
      const panel = get().panel;
      if (line) {
        keepLines(id, null);
        if (panel) pushLines(panel);
        return;
      }
      markLines();
      if (panel) {
        void enqueue(async () => {
          if (panel.sourceIds.includes(id)) await panel.removeSource(id).catch(() => {});
        });
      }
    },

    setLineStyle: (id, style) => {
      if (find(id)?.kind !== 'line') return;
      patch(id, style);
      persist();
      const panel = get().panel;
      if (panel) pushLines(panel);
    },

    refresh: async (id) => {
      const layer = find(id);
      if (!layer) return;
      patch(id, { state: 'refreshing', error: null });
      const fetched = await fetchLayer(layer);
      if (fetched) {
        await replace(id, fetched.resolution, fetched.found.step_hash);
        return;
      }
      // A row fetchLayer did not mark with an error keeps drawing what it drew.
      if (find(id)?.state === 'refreshing') patch(id, { state: layer.state, error: layer.error });
    },

    track: (sid, preview, computed) => {
      const bySession = { ...get().bySession, [sid]: { preview, computed } };
      set({ bySession });
      const layers = get().layers;
      const next = withFreshness(layers, bySession);
      if (next.some((layer, index) => layer !== layers[index])) set({ layers: next });
    },

    setTitles: (titles) => set({ titles }),

    onPanelLayersChange: (layers) => {
      reported = layers;
      const panel = get().panel;
      if (!panel) return;
      // A report in the middle of an add or a restore may lack a source being added:
      // look again, at the newest report, once the store's own panel calls are done.
      if (working > 0) {
        void queue.then(() => {
          if (reported === layers) get().onPanelLayersChange(layers);
        });
        return;
      }
      for (const id of sourcesWithoutLayers(panel.sourceIds, layers)) {
        if (find(id)?.state === 'shown') get().remove(id);
      }
    },

    onPanelError: (error) => {
      if (!isGone(error, { header: true })) return;
      // A chunk's error names its URL, so its mount and layer. A header's carries only
      // the server's code ("gone", or "not_found" from an older server), no URL, so
      // every layer is resolved again; fresh ones and ones refreshing are left alone.
      const text = errorText(error);
      const layers = get().layers;
      const named = layers.filter((layer) => mountsOf(layer.resolution).some((mount) => text.includes(mount)));
      for (const layer of named.length ? named : layers) resolveAgain(layer.id, error);
    },

    settingsChanged: () => {
      clearTimeout(settingsTimer);
      settingsTimer = setTimeout(() => get().captureSettings(), SETTINGS_DELAY_MS);
    },

    captureSettings: () => {
      clearTimeout(settingsTimer);
      const settings = get().panel?.settingsObject;
      if (!settings || get().layers.length === 0) return;
      set({ settings });
      persist();
    },

    runStarted: (sid, step, runId, layer) =>
      set({
        pendingRun: layer ? { sid, step, runId, layer } : { sid, step, runId },
        message: `Running to ${step}; it is drawn here when the run finishes.`,
      }),

    runFinished: (run, start) => {
      const pending = get().pendingRun;
      if (!pending?.runId || pending.runId !== run.runId || !run.finished) return;
      set({ pendingRun: null, message: null });
      const atStart = start?.steps.find((step) => step.step_id === pending.step);
      const targeted = run.started?.targets?.includes(pending.step) ?? false;
      if (!savedOnDisk(undefined, run.steps[pending.step], atStart, targeted)) {
        set({ message: `The run to ${pending.step} ended (${run.finished.status}) without saving it.` });
      } else if (pending.layer && find(pending.layer)) {
        void get().refresh(pending.layer);
      } else {
        void get().show(pending.sid, pending.step, { afterRun: true });
      }
    },

    cancelRun: () => set({ pendingRun: null, message: null }),

    checkpointMade: (step) => {
      const pending = get().pendingRun;
      if (pending?.step !== step || pending.runId) return;
      set({ pendingRun: { ...pending, memoryOnly: false }, message: unsavedText(step, 'checkpoint') });
    },

    gpuUnavailable: () => {
      const { layers, busy } = get();
      // Not persisted: the saved rows come back in a browser that has WebGPU.
      set({
        gpu: 'unavailable',
        layers: [],
        lines: {},
        choice: null,
        message: layers.length || busy ? NEEDS_WEBGPU : null,
      });
    },

    attach: (panel) => {
      set({ panel, gpu: 'ready' });
      return drawAll(panel);
    },

    detach: () => {
      get().captureSettings();
      set((state) => ({
        panel: null,
        layers: state.layers.map((layer) => ({ ...layer, state: 'loading' as const, error: null })),
      }));
    },

    activate: (sid) => {
      if (get().front === sid) return;
      set({ front: sid, choice: null, pendingRun: null, message: null, busy: null });
    },

    removeSession: (sid) => {
      const ids = get().layers.filter((layer) => layer.sid === sid).map((layer) => layer.id);
      const bySession = { ...get().bySession };
      delete bySession[sid];
      set({ bySession });
      const drawn = ids.filter((id) => id in get().lines);
      setLayers((layers) => layers.filter((layer) => layer.sid !== sid));
      markLines();
      // Without WebGPU the store holds no rows, so the saved ones are filtered instead.
      if (get().gpu === 'unavailable') dropSaved(sid);
      else persist();
      const panel = get().panel;
      if (drawn.length > 0) {
        for (const id of drawn) keepLines(id, null);
        if (panel) pushLines(panel);
      }
      if (panel && ids.length > 0) {
        void enqueue(async () => {
          for (const id of ids) {
            if (panel.sourceIds.includes(id)) await panel.removeSource(id).catch(() => {});
          }
        });
      }
    },

    restore: () => {
      const saved = savedState();
      set({ settings: saved.settings, lines: {} });
      setLayers(() => (get().gpu === 'unavailable' ? [] : saved.layers));
      const panel = get().panel;
      if (panel) void drawAll(panel);
    },
  };
});
