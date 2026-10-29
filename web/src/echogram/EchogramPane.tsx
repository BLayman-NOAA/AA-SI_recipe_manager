/**
 * The echogram pane: the client's list of steps drawn, and EchogramGL's panel
 * drawing them. The eye icon on a step (diagram, inspector, Cache menu) adds
 * a layer through the store; this pane owns the panel while it is open.
 */
import { EchogramPanel, GpuUnavailableError } from 'aa-si-echogram-gl';
import { useEffect, useRef, useState, type RefObject } from 'react';
import { useCurrentSession } from '../api/queries';
import { selectStep } from '../diagram/locate';
import { useRunControlsContext } from '../runs/useRunControls';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { Chooser } from './Chooser';
import './echogram.css';
import { getGpuContext } from './gpu';
import { LayerList } from './LayerList';
import { useEchogramStore } from './store';
import { spawnDecodeWorker } from './worker';

/** Where the panel remembers which of its sections are open. */
export const SECTIONS_KEY = 'rc.echogram.sections';

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

/** Create the panel in the host element once WebGPU answers, and hand it to the store. */
function useEchogramPanel(host: RefObject<HTMLDivElement | null>): GpuState {
  const [gpu, setGpu] = useState<GpuState>({ kind: 'starting' });
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
  }, [host]);
  return gpu;
}

/** The offer to run to a step that is not saved, and draw it when the run finishes. */
function RunOffer({ step }: { step: string }) {
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
  const idle = layers.length === 0 && !choice && !message && !busy;

  return (
    <div className="rc-echogram">
      <div className="rc-echogram-bar">
        <LayerList
          layers={layers}
          onSelect={(layer) => selectStep(useView, graph, layer.step)}
          onRefresh={(layer) => void store.refresh(layer.id)}
          onRemove={(layer) => store.remove(layer.id)}
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
        {busy && <div className="rc-muted">Looking up {busy}...</div>}
        {message && <div className="rc-echogram-message">{message}</div>}
        {pendingRun && pendingRun.runId === null && <RunOffer step={pendingRun.step} />}
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
