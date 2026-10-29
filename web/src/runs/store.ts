/** The runs the client follows: the current run, the last finished one, and one replayed to show. */
import { create } from 'zustand';
import type { LogEntry, Preview } from '../api/models';
import { showPane } from '../shell/dock';
import type { RunEvent } from './events';
import type { RunIntent } from './intent';
import { PROGRESS_STREAM } from './progress';
import { initialRunState, reduceEvents, type RunState } from './reducer';

/** The FR-57 hint after a run to a step the recipe does not checkpoint. */
export interface RunHint {
  step: string;
  /** The nearest checkpointed step downstream, and how many more steps a run to it takes. */
  alternative: { step: string; extra: number } | null;
  /**
   * The run saves the step: its start preview pins it. False when the pin is
   * refused, as for a step the recipe marks checkpoint: never. Absent means saved.
   */
  saved?: boolean;
  /** The recipe marks the step checkpoint: never. */
  never?: boolean;
}

export interface RunStore {
  /** The session the runs belong to. */
  sid: string | null;
  /** The run being followed, until its run_finished. */
  current: RunState | null;
  /** The newest finished run, for actual marks and stale marking. */
  last: RunState | null;
  /** Id of the run whose marks the diagram shows; null means current, else last. */
  shown: string | null;
  /** An older run replayed for "Show on diagram". */
  replay: RunState | null;
  /** The preview each run was started with, by run id, for the current and last runs started here. */
  previews: Record<string, Preview>;
  /** Id of a run asked to cancel, shown as "cancelling" until it finishes. */
  cancelling: string | null;
  /** A run to start once no run is active. */
  queued: RunIntent | null;
  hint: RunHint | null;
  /** Why the last start or cancel failed. */
  error: string | null;
  /** The Logs pane's run and step filter, and a counter asking it to show the traceback. */
  logRun: string | null;
  logStep: string;
  logFocus: number;

  /** Follow a new run, with the preview of its intent when the client started it. */
  begin: (runId: string, preview?: Preview | null) => void;
  apply: (runId: string, events: RunEvent[]) => void;
  /** Add a progress line of the client's own to the current run's log, such as a cancel request. */
  note: (runId: string, text: string) => void;
  /** Move the current run to last once it has finished. */
  finish: (runId: string) => void;
  resetFor: (sid: string | null) => void;
  show: (runId: string | null) => void;
  setCancelling: (runId: string | null) => void;
  setQueued: (intent: RunIntent | null) => void;
  setHint: (hint: RunHint | null) => void;
  setError: (error: string | null) => void;
  setLogRun: (runId: string | null) => void;
  setLogStep: (step: string) => void;
}

const EMPTY = {
  current: null,
  last: null,
  shown: null,
  replay: null,
  previews: {},
  cancelling: null,
  queued: null,
  hint: null,
  error: null,
  logRun: null,
  logStep: '',
};

export const useRunStore = create<RunStore>((set) => ({
  sid: null,
  ...EMPTY,
  logFocus: 0,

  begin: (runId, preview = null) =>
    set((state) => {
      const last = state.current?.finished ? state.current : state.last;
      const previews: Record<string, Preview> = {};
      if (last && state.previews[last.runId]) previews[last.runId] = state.previews[last.runId];
      if (preview) previews[runId] = preview;
      return {
        current: initialRunState(runId),
        last,
        previews,
        shown: null,
        replay: null,
        error: null,
        logRun: null,
      };
    }),
  apply: (runId, events) =>
    set((state) => {
      if (state.current?.runId === runId) return { current: reduceEvents(state.current, events) };
      if (state.replay?.runId === runId) return { replay: reduceEvents(state.replay, events) };
      return {};
    }),
  note: (runId, text) =>
    set((state) => {
      const run = state.current;
      if (run?.runId !== runId) return {};
      // Between the last event and the next; the Logs pane keys rows by seq, stream and time.
      const entry: LogEntry = {
        seq: run.lastSeq + 0.5,
        time: Date.now() / 1000,
        step: null,
        stream: PROGRESS_STREAM,
        text,
        buffered: false,
        instance: null,
      };
      return { current: { ...run, logs: [...run.logs, entry] } };
    }),
  finish: (runId) =>
    set((state) => {
      const current = state.current;
      if (current?.runId !== runId || !current.finished) return {};
      return {
        current: null,
        last: current,
        cancelling: state.cancelling === runId ? null : state.cancelling,
      };
    }),
  resetFor: (sid) => set({ sid, ...EMPTY }),
  show: (runId) =>
    set((state) => {
      const known = [state.current, state.last].some((run) => run?.runId === runId);
      if (runId === null || known) return { shown: runId, replay: null };
      return { shown: runId, replay: state.replay?.runId === runId ? state.replay : initialRunState(runId) };
    }),
  setCancelling: (cancelling) => set({ cancelling }),
  setQueued: (queued) => set({ queued }),
  setHint: (hint) => set({ hint }),
  setError: (error) => set({ error }),
  setLogRun: (logRun) => set({ logRun }),
  setLogStep: (logStep) => set({ logStep }),
}));

/** The run whose marks the diagram shows: the one chosen, else the current, else the last. */
export function shownRun(state: RunStore): RunState | null {
  const runs = [state.current, state.last, state.replay];
  if (state.shown) {
    const chosen = runs.find((run) => run?.runId === state.shown);
    if (chosen) return chosen;
  }
  return state.current ?? state.last;
}

export function useShownRun(): RunState | null {
  return useRunStore(shownRun);
}

/** The preview the shown run was started with, or null when the client did not start it. */
export function shownRunPreview(state: RunStore): Preview | null {
  const run = shownRun(state);
  return (run && state.previews[run.runId]) ?? null;
}

/** The run being followed has not finished. */
export function isRunActive(state: RunStore): boolean {
  return state.current !== null && state.current.finished === null;
}

/** The run a run id names, if the store holds it. */
export function heldRun(state: RunStore, runId: string | null): RunState | null {
  if (!runId) return null;
  return [state.current, state.last, state.replay].find((run) => run?.runId === runId) ?? null;
}

/** Show a step's log: open the Logs pane on the shown run, filtered to the step, at its traceback. */
export function openStepLog(stepId: string): void {
  const state = useRunStore.getState();
  useRunStore.setState({
    logRun: shownRun(state)?.runId ?? state.logRun,
    logStep: stepId,
    logFocus: state.logFocus + 1,
  });
  showPane('logs');
}
