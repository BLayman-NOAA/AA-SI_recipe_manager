/**
 * The echogram pane: the client's list of steps drawn, and EchogramGL's panel
 * drawing them. The eye icon on a step (diagram, inspector, Cache menu) adds
 * a layer through the store; this pane owns the panel while it is open.
 */
import { EchogramPanel, GpuUnavailableError } from 'aa-si-echogram-gl';
import { useEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { findStep } from '../api/graph';
import { fileEditable } from '../api/models';
import { useCurrentSession } from '../api/queries';
import { selectStep } from '../diagram/locate';
import { useSetCheckpoint } from '../editing/useSetCheckpoint';
import { useRunControlsContext } from '../runs/useRunControls';
import { useDock } from '../shell/dock';
import { useSessionStore } from '../state/session';
import { readStored, writeStored } from '../state/storage';
import { useView } from '../state/view';
import { Chooser } from './Chooser';
import './echogram.css';
import { getGpuContext } from './gpu';
import { LayerList } from './LayerList';
import { useEchogramStore } from './store';
import { spawnDecodeWorker } from './worker';

/** Where the panel remembers which of its sections are open. */
export const SECTIONS_KEY = 'rc.echogram.sections';

/** Whether the pane's own list of sources is open. */
export const SOURCES_KEY = 'rc.echogram.sources';

type GpuState =
  | { kind: 'starting' }
  | { kind: 'ready' }
  | { kind: 'unavailable'; message: string; advice: string };

/**
 * What the pane says without WebGPU. The advice is left out where the message
 * already gives it: with no secure context both say to use https or localhost.
 */
function unavailable(error: unknown): GpuState {
  if (error instanceof GpuUnavailableError) {
    const advice = error.reason === 'insecure-context' ? '' : error.advice;
    return { kind: 'unavailable', message: error.message, advice };
  }
  return { kind: 'unavailable', message: error instanceof Error ? error.message : String(error), advice: '' };
}

/**
 * Count the moves of an element into another document, as when dockview puts
 * its pane in a popout window or back. Dockview reports every move, popout
 * and tab change as a layout change once the page has settled, so the
 * element's document is compared there with the last one seen.
 */
function useDocumentMoves(element: RefObject<HTMLElement | null>): number {
  const api = useDock((state) => state.api);
  const [moves, setMoves] = useState(0);
  useEffect(() => {
    const node = element.current;
    if (!node || !api) return;
    let seen = node.ownerDocument;
    const listener = api.onDidLayoutChange(() => {
      if (!node.isConnected || node.ownerDocument === seen) return;
      seen = node.ownerDocument;
      setMoves((count) => count + 1);
    });
    return () => listener.dispose();
  }, [element, api]);
  return moves;
}

/** Copy the styles EchogramGL adds to the main page into another document lacking them. */
function shareStyles(target: Document): void {
  if (target === document) return;
  for (const style of document.head.querySelectorAll('style[id]')) {
    if (!target.getElementById(style.id)) target.head.append(target.importNode(style, true));
  }
}

/**
 * Create the panel in the host element once WebGPU answers, and hand it to the
 * store. A WebGPU canvas may not survive a move to another window, so when the
 * host moves to another document the panel is made again there.
 */
function useEchogramPanel(host: RefObject<HTMLDivElement | null>): GpuState {
  const [gpu, setGpu] = useState<GpuState>({ kind: 'starting' });
  const moves = useDocumentMoves(host);
  useEffect(() => {
    const container = host.current;
    if (!container) return;
    const store = useEchogramStore.getState();
    let panel: EchogramPanel | null = null;
    let live = true;
    getGpuContext()
      .then((context) => {
        if (!live) return;
        panel = new EchogramPanel({
          container,
          context,
          spawnWorker: spawnDecodeWorker,
          storageKey: SECTIONS_KEY,
          onError: (error) => {
            console.error(error);
            store.onPanelError(error);
          },
          onLayersChange: store.onPanelLayersChange,
          onViewChange: store.settingsChanged,
        });
        shareStyles(container.ownerDocument);
        setGpu({ kind: 'ready' });
        void store.attach(panel);
      })
      .catch((error: unknown) => {
        if (!live) return;
        setGpu(unavailable(error));
        store.gpuUnavailable();
      });
    // A reload does not unmount the pane, so the settings are kept as the page goes.
    window.addEventListener('pagehide', store.captureSettings);
    return () => {
      live = false;
      window.removeEventListener('pagehide', store.captureSettings);
      if (panel) {
        store.detach();
        panel.destroy();
      }
    };
  }, [host, moves]);
  return gpu;
}

interface SourcesProps {
  count: number;
  /** The step whose chooser is waiting; the block opens when it changes, so the chooser is seen. */
  choosing: string | null;
  children: ReactNode;
}

/** The steps drawn and the chooser, in a details block that remembers whether it is open. */
function Sources({ count, choosing, children }: SourcesProps) {
  const [open, setOpen] = useState(() => readStored(SOURCES_KEY) !== 'false');
  // The toggle event follows an open set here too; only one the state does
  // not know of yet is the user's, and only that one is remembered.
  const toggle = (next: boolean) => {
    if (next === open) return;
    setOpen(next);
    writeStored(SOURCES_KEY, String(next));
  };
  useEffect(() => {
    if (choosing) setOpen(true);
  }, [choosing]);
  return (
    <details className="rc-echogram-sources" open={open} onToggle={(event) => toggle(event.currentTarget.open)}>
      <summary>Sources ({count})</summary>
      {children}
    </details>
  );
}

/** Set `checkpoint: always` on a step that is not a checkpoint, when its file can be edited. */
function MakeCheckpoint({ step }: { step: string }) {
  const session = useCurrentSession().data;
  const set = useSetCheckpoint(step);
  const node = session ? findStep(session.graph, step) : undefined;
  if (!session || !node || !fileEditable(session, node.document)) return null;
  const make = async () => {
    if (await set(true)) useEchogramStore.getState().checkpointMade(step);
  };
  return (
    <button type="button" className="rc-button rc-small" onClick={() => void make()}>
      Make it a checkpoint
    </button>
  );
}

/** The offer to run to a step that is not saved, and draw it when the run finishes. */
function RunOffer({ step, memoryOnly }: { step: string; memoryOnly: boolean }) {
  const { canRun, runToHere } = useRunControlsContext();
  const store = useEchogramStore.getState();
  return (
    <div className="rc-echogram-offer">
      <button
        type="button"
        className="rc-button rc-small rc-primary"
        disabled={!canRun}
        title={canRun ? undefined : 'A run can start once the recipe is valid and no run is going'}
        onClick={() => runToHere(step, (runId) => store.runStarted(step, runId))}
      >
        Run to {step} and show
      </button>
      {memoryOnly && <MakeCheckpoint step={step} />}
      <button type="button" className="rc-button rc-small" onClick={store.cancelRun}>
        Cancel
      </button>
    </div>
  );
}

export function EchogramPane() {
  const host = useRef<HTMLDivElement>(null);
  const gpu = useEchogramPanel(host);
  const sid = useSessionStore((state) => state.sid);
  const graph = useCurrentSession().data?.graph;
  const layers = useEchogramStore((state) => state.layers);
  const choice = useEchogramStore((state) => state.choice);
  const pendingRun = useEchogramStore((state) => state.pendingRun);
  const message = useEchogramStore((state) => state.message);
  const busy = useEchogramStore((state) => state.busy);
  const store = useEchogramStore.getState();
  const { canRun, runToHere } = useRunControlsContext();
  const idle = layers.length === 0 && !choice && !message && !busy;

  return (
    <div className="rc-echogram">
      <div className="rc-echogram-bar">
        {(layers.length > 0 || choice) && (
          <Sources count={layers.length} choosing={choice?.step ?? null}>
            <LayerList
              layers={layers}
              onSelect={(layer) => selectStep(useView, graph, layer.step)}
              onRefresh={(layer) => void store.refresh(layer.id)}
              onRemove={(layer) => store.remove(layer.id)}
              onRun={(layer) => runToHere(layer.step, (runId) => store.runStarted(layer.step, runId, layer.id))}
              canRun={canRun}
            />
            {choice && (
              <Chooser
                key={choice.step}
                step={choice.step}
                choices={choice.choices}
                selected={choice.selected}
                onAdd={(picked) => void store.addChoice(picked)}
                onDone={store.cancelChoice}
              />
            )}
          </Sources>
        )}
        {busy && <div className="rc-muted">Looking up {busy}...</div>}
        {message && <div className="rc-echogram-message">{message}</div>}
        {pendingRun && pendingRun.runId === null && (
          <RunOffer step={pendingRun.step} memoryOnly={pendingRun.memoryOnly ?? false} />
        )}
        {idle && gpu.kind !== 'unavailable' && (
          <div className="rc-empty">
            {sid
              ? 'Click the eye icon on a step to draw its output here.'
              : 'Open a recipe, then click the eye icon on a step to draw its output here.'}
          </div>
        )}
      </div>
      {gpu.kind === 'starting' && <div className="rc-echogram-message rc-echogram-starting">Starting WebGPU...</div>}
      {gpu.kind === 'unavailable' && (
        <div className="rc-echogram-unavailable" role="alert">
          <h3>The echogram pane needs WebGPU</h3>
          <p>{gpu.message}</p>
          {gpu.advice && <p>{gpu.advice}</p>}
        </div>
      )}
      <div ref={host} className="rc-echogram-host" />
    </div>
  );
}
