/** What the statistics pane shows for the selection, and the message when it is not a table. */
import { ApiError } from '../api/client';
import { findStep, tableCandidate, type Graph, type StepNode } from '../api/graph';
import type { ArtifactJson, StepArtifacts } from '../api/models';
import type { Selection } from '../state/view';
import type { StatsChoice } from './store';
import { parseTable, type Table } from './table';

export type PaneState =
  | { kind: 'no-session' }
  | { kind: 'select' }
  | { kind: 'mapped'; step: string }
  | { kind: 'not-table'; step: string }
  | { kind: 'looking'; step: string }
  | { kind: 'unsaved'; step: string; note: string | null }
  | { kind: 'error'; step: string; message: string }
  | { kind: 'not-a-table'; step: string; output: string; reason: string }
  | { kind: 'table'; step: string; output: string; table: Table };

/** The selected step, if a step is selected. */
export function selectedStep(graph: Graph | null | undefined, selected: Selection | null): StepNode | undefined {
  if (!graph || selected?.kind !== 'step') return undefined;
  return findStep(graph, selected.id);
}

/** A step that runs once, so it saves one table per output. */
function runsOnce(step: StepNode): boolean {
  return !step.mapped && step.sweep === null;
}

/** The step may hold a table: it has a dict output, or Show table chose one of its outputs. */
function mayHoldTable(step: StepNode, choice: StatsChoice | null): boolean {
  return tableCandidate(step) || choice?.step === step.id;
}

/** The step's artifacts are worth asking for: it runs once and may hold a table. */
export function wantsArtifacts(step: StepNode | undefined, choice: StatsChoice | null): step is StepNode {
  return step !== undefined && runsOnce(step) && mayHoldTable(step, choice);
}

/** The step's saved json outputs, in the server's order. */
export function jsonOutputs(artifacts: StepArtifacts | undefined): string[] {
  const records = artifacts?.artifacts ?? [];
  const outputs = records.filter((record) => record.format === 'json' && record.instance_index === null);
  return [...new Set(outputs.map((record) => record.output))];
}

/** The output to show: the chosen one when it is saved for this step, else the first saved. */
export function chooseOutput(step: string, outputs: string[], choice: StatsChoice | null): string | null {
  if (choice && choice.step === step && outputs.includes(choice.output)) return choice.output;
  return outputs[0] ?? null;
}

/** A byte count in bytes, KB or MB. */
export function sizeText(bytes: number): string {
  const round = (value: number) => Number(value.toFixed(1));
  if (bytes < 1024) return `${bytes} bytes`;
  if (bytes < 1024 * 1024) return `${round(bytes / 1024)} KB`;
  return `${round(bytes / (1024 * 1024))} MB`;
}

/** An error as the pane says it; too large says the limit and where to raise it. */
export function errorText(error: Error, step: string, output: string | null): string {
  if (error instanceof ApiError && error.error === 'ArtifactTooLarge') {
    const limit = (error.details as { limit?: unknown } | null)?.limit;
    const size = typeof limit === 'number' ? `the ${sizeText(limit)}` : 'what';
    const name = output === null ? step : `${step}.${output}`;
    return (
      `${name} is larger than ${size} the server reads. Raise max_artifact_json_bytes ` +
      "in server.yaml in the client's state folder (~/.aa-recipe/client by default)."
    );
  }
  return error.message;
}

export interface PaneInputs {
  sid: string | null;
  step: StepNode | undefined;
  choice: StatsChoice | null;
  artifacts: StepArtifacts | undefined;
  artifactsError: Error | null;
  output: string | null;
  json: ArtifactJson | undefined;
  jsonError: Error | null;
}

export function paneState(inputs: PaneInputs): PaneState {
  const { sid, step } = inputs;
  if (sid === null) return { kind: 'no-session' };
  if (!step) return { kind: 'select' };
  if (!mayHoldTable(step, inputs.choice)) return { kind: 'not-table', step: step.id };
  if (!runsOnce(step)) return { kind: 'mapped', step: step.id };
  if (inputs.artifactsError) {
    return { kind: 'error', step: step.id, message: errorText(inputs.artifactsError, step.id, null) };
  }
  if (!inputs.artifacts) return { kind: 'looking', step: step.id };
  if (inputs.output === null) return { kind: 'unsaved', step: step.id, note: inputs.artifacts.note ?? null };
  if (inputs.jsonError) {
    return { kind: 'error', step: step.id, message: errorText(inputs.jsonError, step.id, inputs.output) };
  }
  // The json query is keyed by the hash it asks for, but a request sent before an
  // edit can still be answered at the hash after it.
  if (!inputs.json || inputs.json.step_hash !== inputs.artifacts.step_hash) return { kind: 'looking', step: step.id };
  const parsed = parseTable(inputs.json.data);
  if (!parsed.ok) return { kind: 'not-a-table', step: step.id, output: inputs.output, reason: parsed.reason };
  return { kind: 'table', step: step.id, output: inputs.output, table: parsed.table };
}

/** What the pane says in each state but the table. */
export function paneMessage(state: Exclude<PaneState, { kind: 'table' }>): string {
  switch (state.kind) {
    case 'no-session':
      return 'Open a recipe, then select a step with a statistics table output.';
    case 'select':
      return (
        'Select a step with a statistics table output, such as cluster_statistics, ' +
        "or use Show table in a step's Saved files."
      );
    case 'mapped':
      return `${state.step} runs once per item. The statistics pane shows the tables of steps that run once.`;
    case 'not-table':
      return (
        `${state.step} has no statistics table output. ` +
        'Select a step with a statistics table output, such as cluster_statistics.'
      );
    case 'looking':
      return `Looking up ${state.step}...`;
    case 'unsaved': {
      const why = state.note ? ` (${state.note})` : '';
      return `${state.step} has no saved table for the current settings${why}. Run to ${state.step} to compute it.`;
    }
    case 'error':
      return state.message;
    case 'not-a-table':
      return `${state.step}.${state.output} is JSON but not a table: ${state.reason}.`;
  }
}
