/** What a run or a preview is asked to do, from a toolbar action and the selection. */
import { includeTargets, type Graph } from '../api/graph';
import type { StartRunRequest } from '../api/models';
import type { Selection } from '../state/view';

export type RunMode = 'all' | 'to' | 'only';

export interface RunIntent {
  mode: RunMode;
  targets: string[];
  force: boolean;
  forceSteps: string[];
  /** The sub-recipe run through, whose exit steps are the targets; absent for a step. */
  include?: string;
}

export type RunAction = 'all' | 'to' | 'only' | 'forceSelected' | 'forceAll';

/** Run everything. */
export const RUN_ALL: RunIntent = { mode: 'all', targets: [], force: false, forceSteps: [] };

/** The selected step id, or null when the selection is not a step. */
export function selectedStep(selection: Selection | null): string | null {
  return selection?.kind === 'step' ? selection.id : null;
}

/** The targets of Run to selected: the selected step, or a selected sub-recipe's exit steps. */
export function selectionTargets(selection: Selection | null, graph?: Graph | null): string[] {
  if (selection?.kind === 'step') return [selection.id];
  if (selection?.kind === 'include' && graph) return includeTargets(graph, selection.id);
  return [];
}

/**
 * The intent of an action on the selection, or null when it needs a step and
 * none is selected. Run to selected also takes a sub-recipe, given the graph.
 */
export function intentFor(action: RunAction, selection: Selection | null, graph?: Graph | null): RunIntent | null {
  if (action === 'to') {
    const targets = selectionTargets(selection, graph);
    if (targets.length === 0) return null;
    const intent: RunIntent = { ...RUN_ALL, mode: 'to', targets };
    return selection?.kind === 'include' ? { ...intent, include: selection.id } : intent;
  }
  const step = selectedStep(selection);
  switch (action) {
    case 'all':
      return RUN_ALL;
    case 'forceAll':
      return { ...RUN_ALL, force: true };
    case 'only':
      return step ? { ...RUN_ALL, mode: 'only', targets: [step] } : null;
    case 'forceSelected':
      return step ? { ...RUN_ALL, mode: 'to', targets: [step], forceSteps: [step] } : null;
  }
}

/** The preview shown for a selection: run to the selected step or through the sub-recipe, else run all. */
export function previewIntent(selection: Selection | null, graph?: Graph | null): RunIntent {
  return intentFor('to', selection, graph) ?? RUN_ALL;
}

export function toStartRequest(intent: RunIntent): StartRunRequest {
  return { mode: intent.mode, targets: intent.targets, force: intent.force, force_steps: intent.forceSteps };
}

/** The preview query string of an intent, with only the arguments that are set. */
export function previewQuery(intent: RunIntent): string {
  const params = new URLSearchParams();
  for (const target of intent.targets) params.append('targets', target);
  if (intent.mode === 'only') params.set('only', 'true');
  if (intent.force) params.set('force', 'true');
  for (const step of intent.forceSteps) params.append('force_steps', step);
  const text = params.toString();
  return text ? `?${text}` : '';
}
