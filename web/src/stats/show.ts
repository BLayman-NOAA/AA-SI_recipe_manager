/** The Saved files list's Show table action. */
import { showPane } from '../shell/dock';
import { useStatsStore } from './store';

/** Open the statistics pane on one json output of the selected step. */
export function showTable(step: string, output: string): void {
  useStatsStore.getState().choose({ step, output });
  showPane('stats');
}
