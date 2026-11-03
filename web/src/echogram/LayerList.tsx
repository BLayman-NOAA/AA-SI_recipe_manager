import { describeFound } from 'aa-si-echogram-gl';
import { foundText, tierLetters, type EchogramLayer, type Freshness } from './layers';

interface LayerListProps {
  layers: EchogramLayer[];
  onSelect: (layer: EchogramLayer) => void;
  onRefresh: (layer: EchogramLayer) => void;
  onRemove: (layer: EchogramLayer) => void;
  /** Run to a row's step and refresh the row when the run finishes. */
  onRun: (layer: EchogramLayer) => void;
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

/** The steps drawn in the echogram, one row per layer. */
export function LayerList({ layers, onSelect, onRefresh, onRemove, onRun, canRun }: LayerListProps) {
  if (layers.length === 0) return null;
  return (
    <ul className="rc-list rc-echogram-layers" aria-label="Echogram layers">
      {layers.map((layer) => {
        const tiers = tierLetters(layer.resolution);
        const state = STATE_TEXT[layer.state];
        const freshness = freshnessText(layer.freshness, Boolean(layer.resolution));
        const action = actionsFor(layer);
        return (
          <li key={layer.id} className="rc-row" data-layer={layer.id}>
            <button
              type="button"
              className="rc-row-main rc-echogram-layer"
              title={layer.resolution ? describeFound(layer.resolution) : `Select ${layer.step}`}
              onClick={() => onSelect(layer)}
            >
              <span className="rc-row-title">
                {layer.label}
                {tiers && (
                  <span className="rc-echogram-tier" title={tiers === 'S' ? 'survey cache' : 'user cache'}>
                    {' '}
                    {tiers}
                  </span>
                )}
              </span>
              <span className="rc-row-sub">
                {[layer.variable ? `${layer.output}: ${layer.variable}` : layer.output, foundText(layer.resolution), state]
                  .filter(Boolean)
                  .join(', ')}
              </span>
              {freshness && <span className="rc-echogram-freshness rc-status-warning">{freshness}</span>}
              {layer.error && <span className="rc-field-error">{layer.error}</span>}
            </button>
            <div className="rc-row-actions">
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
              {action === 'run' && (
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
              <button
                type="button"
                className="rc-button rc-small"
                aria-label={`Remove ${layer.label}`}
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
