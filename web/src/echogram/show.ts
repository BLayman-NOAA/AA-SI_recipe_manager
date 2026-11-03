/** The eye icon's action, shared by the diagram, the inspector and the Cache menu. */
import type { StepNode } from '../api/graph';
import { showPane } from '../shell/dock';
import { useEchogramStore } from './store';

/** Open the echogram pane and draw a step's saved output there. */
export function showEchogram(sid: string, step: StepNode): void {
  showPane('echogram');
  void useEchogramStore.getState().show(sid, step.id, { checkpoint: step.checkpoint });
}
