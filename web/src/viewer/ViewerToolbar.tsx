/** The viewer's toolbar: the view controls of the application's toolbar and View menu. */
import { TEXT_SIZES, useViewStore } from '../state/view';

/** What the main area shows. */
export type MainView = 'diagram' | 'yaml';

export interface ViewerToolbarProps {
  main: MainView;
  onMain: (view: MainView) => void;
  inspector: boolean;
  onInspector: () => void;
  fullscreen: boolean;
  onFullscreen: () => void;
}

export function ViewerToolbar({ main, onMain, inspector, onInspector, fullscreen, onFullscreen }: ViewerToolbarProps) {
  const store = useViewStore();
  const direction = store((state) => state.direction);
  const textSize = store((state) => state.textSize);
  const { requestFit, setDirection, setTextSize } = store.getState();
  const diagram = main === 'diagram';
  return (
    <div className="rc-toolbar rc-viewer-toolbar" role="toolbar" aria-label="Diagram view">
      <div className="rc-viewer-switch" role="group" aria-label="Show">
        <button
          type="button"
          className="rc-button"
          aria-pressed={diagram}
          title="Show the flow diagram"
          onClick={() => onMain('diagram')}
        >
          Diagram
        </button>
        <button
          type="button"
          className="rc-button"
          aria-pressed={!diagram}
          title="Show the recipe YAML"
          onClick={() => onMain('yaml')}
        >
          YAML
        </button>
      </div>
      <button type="button" className="rc-button" title="Fit the diagram (F)" disabled={!diagram} onClick={requestFit}>
        Fit
      </button>
      <button
        type="button"
        className="rc-button"
        disabled={!diagram}
        title={
          direction === 'RIGHT'
            ? 'Layout is left to right. Click for top to bottom.'
            : 'Layout is top to bottom. Click for left to right.'
        }
        onClick={() => setDirection(direction === 'RIGHT' ? 'DOWN' : 'RIGHT')}
      >
        Toggle Layout Direction{' '}
        <span className="rc-muted">({direction === 'RIGHT' ? 'left to right' : 'top to bottom'})</span>
      </button>
      <label className="rc-viewer-text-size">
        Text size{' '}
        <select
          className="rc-input"
          value={textSize}
          disabled={!diagram}
          onChange={(event) => setTextSize(Number(event.target.value))}
        >
          {TEXT_SIZES.map((size) => (
            <option key={size} value={size}>
              {Math.round(size * 100)}%
            </option>
          ))}
        </select>
      </label>
      <span className="rc-toolbar-spacer" />
      <button
        type="button"
        className="rc-button"
        aria-pressed={inspector}
        title="Show or hide the inspector"
        onClick={onInspector}
      >
        Inspector
      </button>
      <button
        type="button"
        className="rc-button"
        aria-pressed={fullscreen}
        title={fullscreen ? 'Leave full screen (Escape)' : 'Full screen'}
        onClick={onFullscreen}
      >
        {fullscreen ? 'Exit full screen' : 'Full screen'}
      </button>
    </div>
  );
}
