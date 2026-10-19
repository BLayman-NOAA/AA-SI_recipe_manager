import { useCancelRun } from '../api/queries';
import { writeStored } from '../state/storage';
import { plural } from '../text';
import { RUN_ALL } from './intent';
import { HINT_KEY } from './useRunControls';
import { isRunActive, useRunStore } from './store';

/**
 * The FR-57 hint above the diagram after a run to a step the recipe does not
 * checkpoint. It never blocks the run.
 */
export function RunHint() {
  const hint = useRunStore((state) => state.hint);
  const { mutate: cancel } = useCancelRun();
  if (!hint) return null;
  const { setHint, setQueued, setCancelling } = useRunStore.getState();
  const alternative = hint.alternative;

  const runInstead = () => {
    if (!alternative) return;
    const store = useRunStore.getState();
    setHint(null);
    setQueued({ ...RUN_ALL, mode: 'to', targets: [alternative.step] });
    if (isRunActive(store) && store.current) {
      setCancelling(store.current.runId);
      cancel(store.current.runId);
    }
  };

  return (
    <div className="rc-run-hint" role="status">
      <span className="rc-row-main">
        <span className="rc-mono">{hint.step}</span> is not a checkpoint in the recipe. It is saved now because it
        is this run's target, but a later run that recomputes it will not save it again.
      </span>
      {alternative && (
        <button type="button" className="rc-button rc-small" onClick={runInstead}>
          Run to <span className="rc-mono">{alternative.step}</span> instead ({plural(alternative.extra, 'more step')})
        </button>
      )}
      <button
        type="button"
        className="rc-button rc-small"
        onClick={() => {
          writeStored(HINT_KEY, 'off');
          setHint(null);
        }}
      >
        Don't show again
      </button>
      <button type="button" className="rc-link-button" aria-label="Close the hint" onClick={() => setHint(null)}>
        Close
      </button>
    </div>
  );
}
