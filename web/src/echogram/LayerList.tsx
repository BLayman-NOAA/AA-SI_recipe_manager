import { describeFound } from 'aa-si-echogram-gl';
import { foundText, tierLetters, type EchogramLayer } from './layers';

interface LayerListProps {
  layers: EchogramLayer[];
  onSelect: (layer: EchogramLayer) => void;
  onRefresh: (layer: EchogramLayer) => void;
  onRemove: (layer: EchogramLayer) => void;
}

const STATE_TEXT: Record<EchogramLayer['state'], string> = {
  loading: 'loading',
  shown: '',
  error: 'error',
  refreshing: 'refreshing',
};

/** The steps drawn in the echogram, one row per layer. */
export function LayerList({ layers, onSelect, onRefresh, onRemove }: LayerListProps) {
  if (layers.length === 0) return null;
  return (
    <ul className="rc-list rc-echogram-layers" aria-label="Echogram layers">
      {layers.map((layer) => {
        const tiers = tierLetters(layer.resolution);
        const state = STATE_TEXT[layer.state];
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
              {layer.error && <span className="rc-field-error">{layer.error}</span>}
            </button>
            <div className="rc-row-actions">
              {layer.outOfDate && (
                <span className="rc-status-warning" title="The step's hash changed, or a run computed it again">
                  out of date
                </span>
              )}
              {(layer.outOfDate || layer.state === 'error') && (
                <button
                  type="button"
                  className="rc-button rc-small"
                  disabled={layer.state === 'refreshing'}
                  onClick={() => onRefresh(layer)}
                >
                  Refresh
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
