/**
 * Run actions shared by the toolbar, the menus, the shortcuts and the
 * diagram's play icon. Shell creates them once and hands them out through
 * RunControlsContext, so there is one start mutation and one pending flag.
 */
import { useQueryClient } from '@tanstack/react-query';
import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from 'react';
import { findStep } from '../api/graph';
import { ApiError } from '../api/client';
import { cachedPreview, queryKeys, useCancelRun, useCurrentSession, useStartRun, useStopRun } from '../api/queries';
import { showPane } from '../shell/dock';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { readStored } from '../state/storage';
import { intentFor, type RunAction, type RunIntent } from './intent';
import { nearestCheckpointedDownstream } from './runToHere';
import { isRunActive, useRunStore } from './store';

/** localStorage key: the FR-57 hint is turned off. */
export const HINT_KEY = 'rc.hint.runToHere';

export interface RunControls {
  hasSession: boolean;
  /** A valid session is open and no run is active. */
  canRun: boolean;
  /** canRun, and a step is selected. */
  canRunSelected: boolean;
  isActive: boolean;
  /** Cancel was asked for and the run has not finished; Stop now is offered. */
  isCancelling: boolean;
  dryRun: () => void;
  runAll: () => void;
  runToSelected: () => void;
  runSelectedOnly: () => void;
  forceSelected: () => void;
  forceAll: () => void;
  /** Run to a step, from the diagram's play icon. */
  runToHere: (stepId: string) => void;
  /** Start an intent now (no confirmation). */
  start: (intent: RunIntent) => void;
  cancel: () => void;
  /** Kill the run now, after a confirmation. */
  stopNow: () => void;
}

/** The confirmation of Stop now. */
export const STOP_NOW_CONFIRM = 'Stop now? The step that is running is not saved and will compute again next time.';

const noop = () => {};

/** Controls that do nothing, for a tree without the Shell's controls (tests, a pane alone). */
export const NO_RUN_CONTROLS: RunControls = {
  hasSession: false,
  canRun: false,
  canRunSelected: false,
  isActive: false,
  isCancelling: false,
  dryRun: noop,
  runAll: noop,
  runToSelected: noop,
  runSelectedOnly: noop,
  forceSelected: noop,
  forceAll: noop,
  runToHere: noop,
  start: noop,
  cancel: noop,
  stopNow: noop,
};

export const RunControlsContext = createContext<RunControls | null>(null);

/** The Shell's run controls. */
export function useRunControlsContext(): RunControls {
  return useContext(RunControlsContext) ?? NO_RUN_CONTROLS;
}

/** The message offering a run to the target when Run Selected Only finds parents not cached. */
export function upstreamMessage(error: ApiError, target: string): string | null {
  if (error.status !== 409 || error.error !== 'UpstreamNotCachedError') return null;
  const details = (error.details ?? {}) as { missing?: string[] };
  const missing = details.missing ?? [];
  return `Parents of ${target} are not cached: ${missing.join(', ')}. Run to ${target} instead?`;
}

/** Create the run controls; call once, in Shell. */
export function useRunControls(): RunControls {
  const client = useQueryClient();
  const session = useCurrentSession().data;
  const sid = useSessionStore((state) => state.sid);
  const selected = useView((state) => state.selected);
  const isActive = useRunStore(isRunActive);
  const isCancelling = useRunStore((state) => isRunActive(state) && state.cancelling === state.current?.runId);
  const startRun = useStartRun(sid);
  const cancelRun = useCancelRun();
  const stopRun = useStopRun();
  const { mutate: startMutate } = startRun;
  const { mutate: cancelMutate } = cancelRun;
  const { mutate: stopMutate } = stopRun;
  const graph = session?.graph;

  const valid = Boolean(session && session.id === sid && session.validation.valid);
  const canRun = valid && !isActive && !startRun.isPending;
  const canRunSelected = canRun && selected?.kind === 'step';
  // isPending only shows after a render; this stops a second start in the same moment.
  const pending = useRef(false);

  const start = useCallback(
    (intent: RunIntent) => {
      if (pending.current) return;
      pending.current = true;
      const store = useRunStore.getState();
      store.setError(null);
      store.setHint(null);
      // Taken before the run writes anything; the diagram outlines the run from it.
      const preview = sid ? cachedPreview(client, sid, intent) : null;
      startMutate(intent, {
        onSuccess: (run) => {
          pending.current = false;
          useRunStore.getState().begin(run.id, preview);
          const target = intent.mode === 'to' && intent.forceSteps.length === 0 ? intent.targets[0] : null;
          const step = target && graph ? findStep(graph, target) : undefined;
          if (!step || step.checkpoint === 'always' || readStored(HINT_KEY) === 'off') return;
          const alternative = nearestCheckpointedDownstream(graph!, step.id);
          useRunStore.getState().setHint({
            step: step.id,
            alternative: alternative && { step: alternative.step, extra: alternative.extraSteps.length },
          });
        },
        onError: (error) => {
          pending.current = false;
          const target = intent.targets[0];
          const offer = intent.mode === 'only' && error instanceof ApiError ? upstreamMessage(error, target) : null;
          if (offer) {
            if (window.confirm(offer)) start({ ...intent, mode: 'to' });
            return;
          }
          useRunStore.getState().setError(`Run not started: ${error.message}`);
        },
      });
    },
    [startMutate, graph, client, sid],
  );

  // Shortcuts call these whatever the buttons show, so they check again.
  const act = useCallback(
    (action: RunAction) => {
      const intent = intentFor(action, useView.getState().selected);
      if (intent && canRun) start(intent);
    },
    [start, canRun],
  );

  const cancel = useCallback(() => {
    const current = useRunStore.getState().current;
    if (!current || current.finished || !window.confirm('Cancel the run?')) return;
    useRunStore.getState().setCancelling(current.runId);
    cancelMutate(current.runId, {
      onSuccess: () => useRunStore.getState().note(current.runId, 'run ... cancel requested: stops after the running step'),
      onError: (error) => {
        if (error instanceof ApiError && error.error === 'RunNotActive') return;
        useRunStore.getState().setCancelling(null);
        useRunStore.getState().setError(`Cancel failed: ${error.message}`);
      },
    });
  }, [cancelMutate]);

  const stopNow = useCallback(() => {
    const current = useRunStore.getState().current;
    if (!current || current.finished || !window.confirm(STOP_NOW_CONFIRM)) return;
    stopMutate(current.runId, {
      onSuccess: () => useRunStore.getState().note(current.runId, 'run ... stop now requested'),
      onError: (error) => {
        if (error instanceof ApiError && error.error === 'RunNotActive') return;
        useRunStore.getState().setError(`Stop failed: ${error.message}`);
      },
    });
  }, [stopMutate]);

  return useMemo<RunControls>(
    () => ({
      hasSession: Boolean(session),
      canRun,
      canRunSelected,
      isActive,
      isCancelling,
      dryRun: () => {
        if (sid) void client.refetchQueries({ queryKey: queryKeys.previews(sid), type: 'active' });
        showPane('problems');
      },
      runAll: () => act('all'),
      runToSelected: () => act('to'),
      runSelectedOnly: () => act('only'),
      forceSelected: () => act('forceSelected'),
      forceAll: () => act('forceAll'),
      runToHere: (stepId) => {
        if (canRun) start({ mode: 'to', targets: [stepId], force: false, forceSteps: [] });
      },
      start,
      cancel,
      stopNow,
    }),
    [session, canRun, canRunSelected, isActive, isCancelling, act, start, cancel, stopNow, client, sid],
  );
}

/**
 * Start the queued run once no run is active (the hint's "Run to y
 * instead" cancels the current run and queues the other).
 */
export function useStartQueuedRun(controls: RunControls) {
  const queued = useRunStore((state) => state.queued);
  const { canRun, start } = controls;
  useEffect(() => {
    if (!queued || !canRun) return;
    useRunStore.getState().setQueued(null);
    start(queued);
  }, [queued, canRun, start]);
}
