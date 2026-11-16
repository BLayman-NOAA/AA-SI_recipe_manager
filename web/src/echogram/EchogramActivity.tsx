/**
 * A notice over the echogram's top right corner while data is on its way:
 * the open recipe's preview checking the caches, a layer being looked up, or
 * tiles still loading.
 */
import { useIsFetching } from '@tanstack/react-query';
import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { queryKeys } from '../api/queries';
import { useSessionStore } from '../state/session';
import { useEchogramStore } from './store';

/** How long activity must last before the notice shows, so quick work does not flash it. */
export const ACTIVITY_DELAY_MS = 400;

/** How often the panel is asked whether tiles are loading. */
export const TILE_POLL_MS = 300;

export const CHECKING_TEXT = 'Checking for saved data';
export const CHECKING_TITLE =
  'The server is checking which steps are already saved in the caches. Until it finishes, ' +
  'steps may look unsaved and their eye icons dimmed.';
export const LOADING_TEXT = 'Loading echogram data';

/** What of a panel the notice reads: how many tiles are being fetched or uploaded. */
export interface TileCounter {
  view: { info?: { tiles: { loading: number; uploading: number } } };
}

/** Whether the panel has tiles on their way, asked every TILE_POLL_MS. */
export function useTilesLoading(panel: TileCounter | null): boolean {
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!panel) {
      setLoading(false);
      return;
    }
    const check = () => {
      const tiles = panel.view.info?.tiles;
      setLoading(Boolean(tiles && tiles.loading + tiles.uploading > 0));
    };
    check();
    const timer = window.setInterval(check, TILE_POLL_MS);
    return () => window.clearInterval(timer);
  }, [panel]);
  return loading;
}

/** True once `active` has held for ACTIVITY_DELAY_MS; false as soon as it ends. */
function useDelayed(active: boolean): boolean {
  const [shown, setShown] = useState(false);
  useEffect(() => {
    if (!active) {
      setShown(false);
      return;
    }
    const timer = window.setTimeout(() => setShown(true), ACTIVITY_DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [active]);
  return shown;
}

interface EchogramActivityProps {
  /** The panel's plot element, which the notice is drawn into. */
  plot: HTMLElement | null;
  panel: TileCounter | null;
}

export function EchogramActivity({ plot, panel }: EchogramActivityProps) {
  const sid = useSessionStore((state) => state.sid);
  const previewing = useIsFetching({ queryKey: queryKeys.previews(sid ?? '') }) > 0 && sid !== null;
  const lookingUp = useEchogramStore((state) => state.busy !== null);
  const tiles = useTilesLoading(panel);
  const checking = useDelayed(previewing);
  const loading = useDelayed(lookingUp || tiles);

  if (!plot || (!checking && !loading)) return null;
  return createPortal(
    <div className="rc-echogram-activity" role="status">
      {checking && (
        <div title={CHECKING_TITLE}>
          <span className="rc-activity-spinner" aria-hidden="true" />
          {CHECKING_TEXT}
        </div>
      )}
      {loading && (
        <div>
          <span className="rc-activity-spinner" aria-hidden="true" />
          {LOADING_TEXT}
        </div>
      )}
    </div>,
    plot,
  );
}
