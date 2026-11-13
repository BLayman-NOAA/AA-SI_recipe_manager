import { describeFound } from 'aa-si-echogram-gl';
import {
  foundText,
  isLineLayer,
  LINE_WIDTHS,
  orderedRows,
  rowLabel,
  spansSessions,
  tierLetters,
  type EchogramLayer,
  type Freshness,
  type LineLayer,
  type LineStyle,
} from './layers';

interface LayerListProps {
  layers: EchogramLayer[];
  /** The session in front; its rows come first, and only they offer a run. */
  front: string | null;
  /** Session titles by sid, which name the rows when they span sessions. */
  titles: Record<string, string>;
  /** Rows whose source the panel draws no layer of. */
  bare: string[];
  /** Select a row's step, bringing its session's tab to the front first. */
  onSelect: (layer: EchogramLayer) => void;
  onRefresh: (layer: EchogramLayer) => void;
  onRemove: (layer: EchogramLayer) => void;
  /** Draw a layer of a bare row's source in the panel. */
  onAddLayer: (layer: EchogramLayer) => void;
  /** Run to a row's step and refresh the row when the run finishes. */
  onRun: (layer: EchogramLayer) => void;
  /** Change how a line row's line is drawn. */
  onLineStyle: (layer: EchogramLayer, style: LineStyle) => void;
  canRun: boolean;
}

const STATE_TEXT: Record<EchogramLayer['state'], string> = {
  loading: 'loading',
  shown: '',
  error: 'error',
  refreshing: 'refreshing',
};

/** What a row says of how current its data is; empty when it is current or not known. */
export function freshnessText(freshness: Freshness, drawn: boolean): string {
  switch (freshness.state) {
    case 'available':
      return 'data available: refresh to display';
    case 'not_computed':
      return drawn ? 'not computed yet: showing the previous settings' : 'not computed yet';
    case 'not_saved':
      return 'not saved at the current settings: showing the previous settings';
    case 'partial':
      return `partly computed: ${freshness.held} of ${freshness.of ?? '?'} files`;
    default:
      return '';
  }
}

/**
 * The action a row offers besides Remove: Run to its step when nothing holds
 * all of its current hash, Refresh when newer data is saved or the row shows
 * an error, else none. A row a refresh found nothing for at the current hash
 * offers none until the hash or the last run changes.
 */
export function actionsFor(layer: Pick<EchogramLayer, 'freshness' | 'state'>): 'refresh' | 'run' | null {
  const fresh = layer.freshness.state;
  if (fresh === 'not_computed' || fresh === 'partial') return 'run';
  if (fresh === 'available' || layer.state === 'error') return 'refresh';
  return null;
}

/** A row's tooltip: what it draws, or that a click brings its session's tab to the front. */
function rowTitle(layer: EchogramLayer, front: string | null, titles: Record<string, string>): string {
  if (layer.sid !== front) return `Switch to ${titles[layer.sid] ?? layer.sid} and select ${layer.step}`;
  return layer.resolution ? describeFound(layer.resolution) : `Select ${layer.step}`;
}

interface LineControlsProps {
  layer: LineLayer;
  /** What the row is called, which names each control. */
  label: string;
  onChange: (style: LineStyle) => void;
}

/** A line row's colour, width and whether it is drawn. */
function LineControls({ layer, label, onChange }: LineControlsProps) {
  return (
    <span className="rc-echogram-line-style">
      <input
        type="color"
        className="rc-echogram-swatch"
        aria-label={`Colour of ${label}`}
        title="Line colour"
        value={layer.color}
        onChange={(event) => onChange({ color: event.target.value })}
      />
      <select
        className="rc-input"
        aria-label={`Width of ${label}`}
        title="Line width"
        value={layer.width}
        onChange={(event) => onChange({ width: Number(event.target.value) })}
      >
        {LINE_WIDTHS.map((width) => (
          <option key={width} value={width}>
            {width} px
          </option>
        ))}
      </select>
      <input
        type="checkbox"
        aria-label={`Show ${label}`}
        title="Draw the line"
        checked={layer.visible}
        onChange={(event) => onChange({ visible: event.target.checked })}
      />
    </span>
  );
}

/**
 * The steps drawn in the echogram, one row per layer: the front session's
 * first, named with their session's title when the rows span sessions or
 * come from a tab behind. A line row also sets its line's colour, width and
 * visibility. A row whose source the panel draws no layer of offers to add one.
 */
export function LayerList({
  layers,
  front,
  titles,
  bare,
  onSelect,
  onRefresh,
  onRemove,
  onAddLayer,
  onRun,
  onLineStyle,
  canRun,
}: LayerListProps) {
  if (layers.length === 0) return null;
  const spanning = spansSessions(layers);
  return (
    <ul className="rc-list rc-echogram-layers" aria-label="Echogram layers">
      {orderedRows(layers, front).map((layer) => {
        const text = rowLabel(layer, titles, spanning, front);
        const tiers = tierLetters(layer.resolution);
        const drawsNothing = bare.includes(layer.id);
        const state = STATE_TEXT[layer.state] || (drawsNothing ? 'no layers drawn' : '');
        const freshness = freshnessText(layer.freshness, Boolean(layer.resolution));
        const inFront = layer.sid === front;
        const action = actionsFor(layer);
        return (
          <li key={layer.id} className="rc-row" data-layer={layer.id} data-sid={layer.sid}>
            <button
              type="button"
              className="rc-row-main rc-echogram-layer"
              title={rowTitle(layer, front, titles)}
              onClick={() => onSelect(layer)}
            >
              <span className="rc-row-title">
                {text}
                {tiers && (
                  <span className="rc-echogram-tier" title={tiers === 'S' ? 'survey cache' : 'user cache'}>
                    {' '}
                    {tiers}
                  </span>
                )}
              </span>
              <span className="rc-row-sub">
                {[
                  layer.variable ? `${layer.output}: ${layer.variable}` : layer.output,
                  foundText(layer.resolution, layer.kind),
                  state,
                ]
                  .filter(Boolean)
                  .join(', ')}
              </span>
              {freshness && <span className="rc-echogram-freshness rc-status-warning">{freshness}</span>}
              {layer.error && <span className="rc-field-error">{layer.error}</span>}
            </button>
            <div className="rc-row-actions">
              {isLineLayer(layer) && (
                <LineControls layer={layer} label={text} onChange={(style) => onLineStyle(layer, style)} />
              )}
              {action === 'refresh' && (
                <button
                  type="button"
                  className="rc-button rc-small"
                  disabled={layer.state === 'refreshing'}
                  onClick={() => onRefresh(layer)}
                >
                  Refresh
                </button>
              )}
              {action === 'run' && inFront && (
                <button
                  type="button"
                  className="rc-button rc-small"
                  disabled={!canRun}
                  title={canRun ? undefined : 'A run can start once the recipe is valid and no run is going'}
                  onClick={() => onRun(layer)}
                >
                  Run to {layer.step}
                </button>
              )}
              {drawsNothing && (
                <button
                  type="button"
                  className="rc-button rc-small"
                  title="Draw a layer of this source in the echogram"
                  onClick={() => onAddLayer(layer)}
                >
                  Add layer
                </button>
              )}
              <button
                type="button"
                className="rc-button rc-small"
                aria-label={`Remove ${text}`}
                onClick={() => onRemove(layer)}
              >
                Remove
              </button>
            </div>
          </li>
        );
      })}
    </ul>
  );
}
