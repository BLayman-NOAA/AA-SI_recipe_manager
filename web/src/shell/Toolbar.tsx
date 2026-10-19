import { useEffect, useRef, useState } from 'react';
import type { RunControls } from '../runs/useRunControls';
import { useView } from '../state/view';

interface ToolbarProps {
  run: RunControls;
  onFit: () => void;
  onToggleDirection: () => void;
}

const SELECT_FIRST = 'Select a step in the diagram first';

/** The Force button: a two item menu. */
function ForceMenu({ run }: { run: RunControls }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onPointer = (event: MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onPointer);
    return () => document.removeEventListener('mousedown', onPointer);
  }, [open]);
  const choose = (action: () => void) => {
    setOpen(false);
    action();
  };
  return (
    <div className="rc-dropdown" ref={ref}>
      <button
        type="button"
        className="rc-button"
        aria-haspopup="menu"
        aria-expanded={open}
        disabled={!run.canRun}
        title="Recompute even when cached"
        onClick={() => setOpen(!open)}
      >
        Force {'▾'}
      </button>
      {open && (
        <ul className="rc-menu-list" role="menu" aria-label="Force">
          <li className="rc-menu-item">
            <button
              type="button"
              role="menuitem"
              aria-disabled={!run.canRunSelected}
              title={run.canRunSelected ? undefined : SELECT_FIRST}
              onClick={() => run.canRunSelected && choose(run.forceSelected)}
            >
              <span className="rc-menu-label">Force Rerun Selected</span>
            </button>
          </li>
          <li className="rc-menu-item">
            <button type="button" role="menuitem" onClick={() => choose(run.forceAll)}>
              <span className="rc-menu-label">Force Rerun All</span>
            </button>
          </li>
        </ul>
      )}
    </div>
  );
}

/** Run controls and diagram view controls. */
export function Toolbar({ run, onFit, onToggleDirection }: ToolbarProps) {
  const direction = useView((state) => state.direction);
  const selectedTitle = (title: string) => (run.canRunSelected || !run.canRun ? title : SELECT_FIRST);
  return (
    <div className="rc-toolbar" role="toolbar" aria-label="Run and view">
      <button
        type="button"
        className="rc-button"
        title="Show what a run would do, in the diagram and the Problems pane"
        disabled={!run.hasSession}
        onClick={run.dryRun}
      >
        Dry run
      </button>
      <button type="button" className="rc-button" disabled={!run.canRun} onClick={run.runAll}>
        Run all
      </button>
      <button
        type="button"
        className="rc-button"
        disabled={!run.canRunSelected}
        title={selectedTitle('Run the selected step and everything it needs (Ctrl+Enter)')}
        onClick={run.runToSelected}
      >
        Run to selected
      </button>
      <button
        type="button"
        className="rc-button"
        disabled={!run.canRunSelected}
        title={selectedTitle('Run only the selected step, loading its parents from the cache (Ctrl+Shift+Enter)')}
        onClick={run.runSelectedOnly}
      >
        Run selected
      </button>
      <ForceMenu run={run} />
      {run.isCancelling ? (
        <button
          type="button"
          className="rc-button"
          title="Kill the run now, without waiting for the step that is running"
          onClick={run.stopNow}
        >
          Stop now
        </button>
      ) : (
        <button
          type="button"
          className="rc-button"
          title="Stop the run once the step that is running finishes"
          disabled={!run.isActive}
          onClick={run.cancel}
        >
          Cancel
        </button>
      )}
      <span className="rc-toolbar-divider" />
      <button type="button" className="rc-button" title="Fit the diagram (F)" onClick={onFit}>
        Fit
      </button>
      <button
        type="button"
        className="rc-button"
        title={
          direction === 'RIGHT'
            ? 'Layout is left to right. Click for top to bottom.'
            : 'Layout is top to bottom. Click for left to right.'
        }
        onClick={onToggleDirection}
      >
        Toggle Layout Direction{' '}
        <span className="rc-muted">({direction === 'RIGHT' ? 'left to right' : 'top to bottom'})</span>
      </button>
      <span className="rc-toolbar-spacer" />
    </div>
  );
}
