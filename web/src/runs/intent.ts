/** What a run or a preview is asked to do, from a toolbar action and the selection. */
import type { StartRunRequest } from '../api/models';
import type { Selection } from '../state/view';

export type RunMode = 'all' | 'to' | 'only';

export interface RunIntent {
  mode: RunMode;
  targets: string[];
  force: boolean;
  forceSteps: string[];
}

export type RunAction = 'all' | 'to' | 'only' | 'forceSelected' | 'forceAll';

/** Run everything. */
export const RUN_ALL: RunIntent = { mode: 'all', targets: [], force: false, forceSteps: [] };

/** The selected step id, or null when the selection is not a step. */
export function selectedStep(selection: Selection | null): string | null {
  return selection?.kind === 'step' ? selection.id : null;
}

/** Whether an action needs a selected step. */
export function needsStep(action: RunAction): boolean {
  return action === 'to' || action === 'only' || action === 'forceSelected';
}

/** The intent of an action on the selection, or null when it needs a step and none is selected. */
export function intentFor(action: RunAction, selection: Selection | null): RunIntent | null {
  const step = selectedStep(selection);
  if (needsStep(action) && step === null) return null;
  switch (action) {
    case 'all':
      return RUN_ALL;
    case 'forceAll':
      return { ...RUN_ALL, force: true };
    case 'to':
      return { ...RUN_ALL, mode: 'to', targets: [step!] };
    case 'only':
      return { ...RUN_ALL, mode: 'only', targets: [step!] };
    case 'forceSelected':
      return { ...RUN_ALL, mode: 'to', targets: [step!], forceSteps: [step!] };
  }
}

/** The preview shown for a selection: run to the selected step, else run all. */
export function previewIntent(selection: Selection | null): RunIntent {
  return intentFor('to', selection) ?? RUN_ALL;
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
