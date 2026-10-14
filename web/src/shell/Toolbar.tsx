import { useView } from '../state/view';
import { milestoneTooltip } from './menus';

const RUN_BUTTONS = ['Dry run', 'Run all', 'Run to selected', 'Run selected', 'Force', 'Cancel'];

interface ToolbarProps {
  onFit: () => void;
  onToggleDirection: () => void;
}

/** Run controls (disabled until runs arrive) and diagram view controls. */
export function Toolbar({ onFit, onToggleDirection }: ToolbarProps) {
  const direction = useView((state) => state.direction);
  return (
    <div className="rc-toolbar" role="toolbar" aria-label="Run and view">
      {RUN_BUTTONS.map((label) => (
        <button key={label} type="button" className="rc-button" disabled title={milestoneTooltip(3)}>
          {label}
        </button>
      ))}
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
