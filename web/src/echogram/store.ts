/**
 * The echogram pane's layers, and the EchogramGL panel they are drawn in.
 *
 * The store is the list of what the user asked to see; the panel draws it.
 * Layers outlive the pane: closing it detaches the panel, reopening draws the
 * layers again, and a page reload restores them from localStorage.
 */
import {
  choices,
  draws,
  isPieceSet,
  preferred,
  sameCheckpoint,
  toSpec,
  type Choice,
  type EchogramPanel,
  type Layer,
  type Resolution,
  type ViewSettings,
} from 'aa-si-echogram-gl';
import { create } from 'zustand';
import type { Preview, StepArtifacts } from '../api/models';
import { fetchStepArtifacts, type ArtifactChoice } from '../api/queries';
import { savedOnDisk } from '../runs/badges';
import type { RunState } from '../runs/reducer';
import { readStoredJson, writeStored } from '../state/storage';
import {
  asResolution,
  ECHOGRAM_ROOT,
  goneText,
  isGone,
  layerId,
  missText,
  restoredLayer,
  savedLayer,
  saveKind,
  settingsFor,
  sourcesWithoutLayers,
  unsavedText,
  withFreshness,
  withLabels,
  type ComputedSteps,
  type EchogramLayer,
  type SavedLayer,
} from './layers';

/** What the store needs of an EchogramGL panel, so tests can pass a fake. */
export type PanelLike = Pick<
  EchogramPanel,
  'addSource' | 'replaceSource' | 'removeSource' | 'sourceIds' | 'settingsObject' | 'applySettings' | 'retry'
>;

/** A step whose saved output offers several things to draw, waiting for the user to pick. */
export interface PendingChoice {
  step: string;
  stepHash: string;
  resolution: Resolution;
  choices: Choice[];
  /** The choice selected first. */
  selected: number;
}

/** A step not saved yet: the offer to run to it, then the run that will save it. */
export interface PendingRun {
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
  sid: string | null;
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
  /** The run all preview, which the rows' freshness and the unsaved messages read. */
  preview: Preview | null;
  /** What the last finished run computed and saved. */
  computed: ComputedSteps | null;

  /** Draw a step's saved output, after an eye click. */
  show: (sid: string, step: string, options?: ShowOptions) => Promise<void>;
  /** Add what the chooser selected; the chooser stays open for another. */
  addChoice: (choice: Choice) => Promise<void>;
  cancelChoice: () => void;
  remove: (id: string) => void;
  /** Fetch a layer's step again and draw the newer data, or retry the same. */
  refresh: (id: string) => Promise<void>;
  /** Take the run all preview and the last run, and set each layer's freshness from them. */
  track: (preview: Preview | null, computed: ComputedSteps | null) => void;
  /** The panel's layer stack changed: drop a source whose last layer the user removed there. */
  onPanelLayersChange: (layers: Layer[]) => void;
  /** An error the panel showed; a layer whose mount the server forgot is resolved again. */
  onPanelError: (error: unknown) => void;
  /** The view moved or settled: keep its settings, after a pause. */
  settingsChanged: () => void;
  /** Keep the panel's settings now. */
  captureSettings: () => void;
  /** The run to a pending step started, from the offer or from a row's Run button. */
  runStarted: (step: string, runId: string, layer?: string) => void;
  /** A run finished; draw the pending step, or refresh its row, if it was its run and saved it. */
  runFinished: (run: RunState, start: Preview | null) => void;
  cancelRun: () => void;
  /** The offered step was made a checkpoint. */
  checkpointMade: (step: string) => void;
  attach: (panel: PanelLike) => Promise<void>;
  /** The pane has no WebGPU: drop the rows it cannot draw, keeping them saved for another browser. */
  gpuUnavailable: () => void;
  detach: () => void;
  /** Point the store at a session: its saved layers, nothing of another's. */
  resetFor: (sid: string | null) => void;
}

/** localStorage key of a session's layers and panel settings. */
export function storageKey(sid: string): string {
  return `rc.echogram.${sid}`;
}

interface Saved {
  layers: SavedLayer[];
  settings: ViewSettings | null;
}

function loadSaved(sid: string): Saved {
  const saved = readStoredJson<Partial<Saved>>(storageKey(sid));
  const layers = Array.isArray(saved?.layers) ? saved.layers.filter((layer) => layer?.step && layer.output) : [];
  return { layers, settings: saved?.settings ?? null };
}

/** How long the view must settle before its settings are kept. */
const SETTINGS_DELAY_MS = 1000;

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
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
    set((state) => ({ layers: withFreshness(withLabels(change(state.layers)), state.preview, state.computed) }));
  const previewOf = (step: string) => get().preview?.steps.find((entry) => entry.step_id === step);
  const patch = (id: string, change: Partial<EchogramLayer>) =>
    setLayers((layers) => layers.map((layer) => (layer.id === id ? { ...layer, ...change } : layer)));

  /** What the panel opens for a layer, named as its row is. */
  const specFor = (layer: EchogramLayer, resolution: Resolution) => {
    const spec = toSpec(ECHOGRAM_ROOT, resolution);
    if (isPieceSet(spec)) spec.name = layer.label;
    return spec;
  };
  const find = (id: string) => get().layers.find((layer) => layer.id === id);
  /** The last finished run, which a layer fetched now was drawn after. */
  const lastRun = () => get().computed?.runId ?? null;

  const persist = () => {
    const { sid, layers, settings } = get();
    if (!sid) return;
    const empty = layers.length === 0 && !settings;
    writeStored(storageKey(sid), empty ? null : JSON.stringify({ layers: layers.map(savedLayer), settings }));
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

  /** Draw a layer that is not in the panel yet. */
  const draw = async (panel: PanelLike, id: string) => {
    const layer = find(id);
    if (!layer?.resolution || get().panel !== panel) return;
    try {
      await panel.addSource(id, specFor(layer, layer.resolution));
      patch(id, { state: 'shown', error: null });
    } catch (error) {
      patch(id, { state: 'error', error: errorText(error) });
      if (isGone(error)) resolveAgain(id, error);
    }
  };

  /**
   * Fetch a layer's step for its output and variable; null after marking the
   * row with why not. A row drawn at another hash whose current hash holds
   * nothing keeps what it draws, and notes the hash, so its freshness says
   * the hash is not saved rather than offering the refresh again.
   */
  const fetchLayer = async (layer: EchogramLayer): Promise<{ found: StepArtifacts; resolution: Resolution } | null> => {
    const sid = get().sid;
    if (!sid) return null;
    try {
      const found = await fetchStepArtifacts(sid, layer.step, { output: layer.output, var: layer.variable });
      if (get().sid !== sid) return null;
      if (found.found === 0) {
        if (found.step_hash === layer.stepHash) patch(layer.id, { state: 'error', error: goneText(layer.step) });
        else if (!layer.resolution) {
          patch(layer.id, { state: 'error', error: unsavedText(layer.step, saveKind(previewOf(layer.step))) });
        } else {
          patch(layer.id, { missed: { stepHash: found.step_hash, after: lastRun() } });
        }
        return null;
      }
      const answer = readAnswer(found, true);
      if ('miss' in answer) {
        patch(layer.id, { state: 'error', error: answer.miss });
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
    const old = find(id)?.resolution ?? null;
    const panel = get().panel;
    patch(id, {
      resolution,
      stepHash,
      addedAt: Date.now(),
      drawnAfter: lastRun(),
      missed: undefined,
      state: panel ? 'refreshing' : 'loading',
      error: null,
    });
    persist();
    if (!panel) return Promise.resolve();
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

  /** Add a layer for a resolution, or draw the newer data in an existing one. */
  const add = (step: string, stepHash: string, resolution: Resolution) => {
    if (get().gpu === 'unavailable') {
      set({ message: NEEDS_WEBGPU });
      return Promise.resolve();
    }
    const id = layerId(step, resolution.output ?? '', resolution.var ?? null);
    if (find(id)) return replace(id, resolution, stepHash);
    const layer: EchogramLayer = {
      id,
      step,
      output: resolution.output ?? '',
      variable: resolution.var ?? null,
      label: step,
      stepHash,
      resolution,
      state: 'loading',
      error: null,
      freshness: { state: 'unknown' },
      addedAt: Date.now(),
      drawnAfter: lastRun(),
    };
    setLayers((layers) => [...layers, layer]);
    persist();
    const panel = get().panel;
    return panel ? enqueue(() => draw(panel, id)) : Promise.resolve();
  };

  /** Draw a choice: from the resolution in hand when it draws it, else asked for again. */
  const pick = async (step: string, stepHash: string, resolution: Resolution, choice: Choice) => {
    const sid = get().sid;
    if (!sid) return;
    if (draws(resolution, choice)) return add(step, stepHash, resolution);
    set({ busy: step });
    try {
      const wanted: ArtifactChoice = { output: choice.output, var: choice.var ?? null };
      const found = await fetchStepArtifacts(sid, step, wanted);
      if (get().sid !== sid) return;
      set({ busy: null });
      const answer = readAnswer(found, true);
      if ('miss' in answer) {
        set({ message: answer.miss });
        return;
      }
      return add(step, found.step_hash, answer.resolution);
    } catch (error) {
      set({ busy: null, message: `Could not draw ${choice.label}: ${errorText(error)}` });
    }
  };

  /** Draw every layer not in the panel yet, then the kept settings. */
  const drawAll = (panel: PanelLike) =>
    enqueue(async () => {
      for (const layer of get().layers) {
        if (get().panel !== panel) return;
        if (panel.sourceIds.includes(layer.id)) continue;
        if (!layer.resolution) {
          const fetched = await fetchLayer(layer);
          if (!fetched) continue;
          const { found, resolution } = fetched;
          patch(layer.id, { resolution, stepHash: found.step_hash, addedAt: Date.now(), drawnAfter: lastRun() });
        }
        await draw(panel, layer.id);
      }
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

  return {
    sid: null,
    gpu: 'unknown',
    layers: [],
    choice: null,
    pendingRun: null,
    message: null,
    busy: null,
    settings: null,
    panel: null,
    preview: null,
    computed: null,

    show: async (sid, step, options = {}) => {
      if (get().sid !== sid) return;
      if (get().gpu === 'unavailable') {
        set({ message: NEEDS_WEBGPU, choice: null });
        return;
      }
      const pending = get().pendingRun;
      set({
        busy: step,
        message: null,
        choice: null,
        pendingRun: pending?.runId ? pending : null,
      });
      let found: StepArtifacts;
      try {
        found = await fetchStepArtifacts(sid, step);
      } catch (error) {
        if (get().sid === sid) set({ busy: null, message: `Could not look up ${step}: ${errorText(error)}` });
        return;
      }
      if (get().sid !== sid) return;
      set({ busy: null });
      // A server that cannot draw says so before any offer to save something it could not draw.
      if (!found.echogram.available) {
        set({ message: found.echogram.reason ?? 'This server cannot draw echograms.' });
        return;
      }
      if (found.found === 0) {
        if (options.afterRun) {
          set({ message: `The run to ${step} finished, but ${step} was not saved.` });
          return;
        }
        const kind = saveKind(previewOf(step), options.checkpoint ?? null);
        if (kind === 'never') {
          set({ message: unsavedText(step, kind) });
          return;
        }
        const running = get().pendingRun;
        if (running?.runId) {
          set({ message: `A run to ${running.step} is going. Click the eye on ${step} again when it finishes.` });
          return;
        }
        const gone = get().layers.some((layer) => layer.step === step && layer.stepHash === found.step_hash);
        set({
          pendingRun: { step, runId: null, memoryOnly: kind === 'memory' },
          message: gone ? goneText(step) : unsavedText(step, kind, found.note),
        });
        return;
      }
      let answer: ReturnType<typeof readAnswer>;
      try {
        answer = readAnswer(found);
      } catch (error) {
        set({ message: errorText(error) });
        return;
      }
      if ('miss' in answer) {
        set({ message: answer.miss });
        return;
      }
      const { resolution } = answer;
      const offered = choices(resolution);
      if (offered.length === 0) {
        set({ message: missText(resolution) });
        return;
      }
      const selected = preferred(offered, resolution);
      // After a run the user asked to see the step, not to choose: draw the default.
      if (offered.length === 1 || options.afterRun) {
        await pick(step, found.step_hash, resolution, offered[selected]);
        return;
      }
      set({ choice: { step, stepHash: found.step_hash, resolution, choices: offered, selected } });
    },

    addChoice: async (choice) => {
      const pending = get().choice;
      if (!pending) return;
      // The chooser stays open, so labels and the MVBS under them take two clicks, not two eye clicks.
      await pick(pending.step, pending.stepHash, pending.resolution, choice);
    },

    cancelChoice: () => set({ choice: null }),

    remove: (id) => {
      setLayers((layers) => layers.filter((layer) => layer.id !== id));
      persist();
      const panel = get().panel;
      if (panel) {
        void enqueue(async () => {
          if (panel.sourceIds.includes(id)) await panel.removeSource(id).catch(() => {});
        });
      }
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

    track: (preview, computed) => {
      set({ preview, computed });
      const layers = get().layers;
      const next = withFreshness(layers, preview, computed);
      if (next.some((layer, index) => layer !== layers[index])) set({ layers: next });
    },

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
      if (!isGone(error)) return;
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

    runStarted: (step, runId, layer) =>
      set({
        pendingRun: layer ? { step, runId, layer } : { step, runId },
        message: `Running to ${step}; it is drawn here when the run finishes.`,
      }),

    runFinished: (run, start) => {
      const pending = get().pendingRun;
      const sid = get().sid;
      if (!pending?.runId || pending.runId !== run.runId || !run.finished || !sid) return;
      set({ pendingRun: null, message: null });
      const atStart = start?.steps.find((step) => step.step_id === pending.step);
      const targeted = run.started?.targets?.includes(pending.step) ?? false;
      if (!savedOnDisk(undefined, run.steps[pending.step], atStart, targeted)) {
        set({ message: `The run to ${pending.step} ended (${run.finished.status}) without saving it.` });
      } else if (pending.layer && find(pending.layer)) {
        void get().refresh(pending.layer);
      } else {
        void get().show(sid, pending.step, { afterRun: true });
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

    resetFor: (sid) => {
      if (get().sid === sid) return;
      const panel = get().panel;
      if (panel) {
        get().captureSettings();
        const ids = [...panel.sourceIds];
        void enqueue(async () => {
          for (const id of ids) await panel.removeSource(id).catch(() => {});
        });
      }
      const saved = sid ? loadSaved(sid) : { layers: [], settings: null };
      set({
        sid,
        layers: get().gpu === 'unavailable' ? [] : saved.layers.map(restoredLayer),
        settings: saved.settings,
        choice: null,
        pendingRun: null,
        message: null,
        busy: null,
        preview: null,
        computed: null,
      });
      if (panel) void drawAll(panel);
    },
  };
});
