/** Run events as the server streams them (aa_recipe_manager.events, one JSON line each). */

export type RunEventKind =
  | 'run_started'
  | 'plan'
  | 'step_started'
  | 'instance_finished'
  | 'step_finished'
  | 'step_failed'
  | 'log'
  | 'warning'
  | 'run_finished';

/** Planned dispositions of the plan event. */
export type PlannedKind = 'compute' | 'load' | 'marker' | 'pruned' | 'untargeted';

export interface RunStartedData {
  recipe: string;
  version: string | null;
  recipe_path: string | null;
  mode: string;
  targets: string[];
  only: boolean;
  force: boolean;
  force_steps: string[];
  executor: string;
  step_count: number;
  total_steps: number;
  tiers: Record<string, string>;
  write_tier: string | null;
  outputs_dir: string | null;
  cache_dir: string | null;
}

export interface PlanData {
  steps: Record<string, { hash: string | null; planned: PlannedKind | string; tier: string | null }>;
  blockers: string[];
}

export interface StepStartedData {
  step: string;
  index: number;
  total: number;
  instances: number | null;
  chain: string[] | null;
}

export interface InstanceFinishedData {
  /** The chain head. */
  step: string;
  /** Member step id to its disposition for this instance. */
  members: Record<string, string>;
  instance: unknown;
  count: number | null;
  seconds: number | null;
}

export interface InstanceCounts {
  count: number;
  computed: number;
  cached: number;
  skipped: number;
}

export interface StepFinishedData {
  step: string;
  disposition: string;
  tier: string | null;
  seconds: number | null;
  save_seconds: number | null;
  artifacts: Record<string, unknown>;
  hash: string | null;
  index: number;
  total: number;
  instances: InstanceCounts | null;
  instance_seconds: number[];
}

export interface StepFailedData {
  step: string;
  index: number;
  total: number;
  seconds: number | null;
  error_type: string | null;
  message: string | null;
  traceback: string | null;
  chain: string[] | null;
  instance: unknown;
  item: unknown;
}

export interface LogData {
  step: string | null;
  stream: string;
  text: string;
  buffered: boolean;
  instance: unknown;
}

export interface WarningData {
  step: string | null;
  message: string;
}

export type FinishedStatus = 'completed' | 'failed' | 'cancelled' | 'interrupted';

export interface RunFinishedData {
  status: FinishedStatus;
  seconds: number | null;
  manifest: string | null;
  executed: string[];
  skipped: string[];
  pruned: string[];
  untargeted: string[];
  error_type: string | null;
  message: string | null;
  /** Written by the server for a run process that ended without one. */
  synthetic?: boolean;
}

export interface RunEvent {
  seq: number;
  time: number;
  /** One of RunEventKind; the reducer ignores kinds it does not know. */
  kind: string;
  run_id: string | null;
  data: Record<string, unknown>;
}

/** Parse one event, as sent in an SSE data field or a JSON line. */
export function parseRunEvent(text: string): RunEvent {
  const event = JSON.parse(text) as RunEvent;
  if (typeof event.seq !== 'number' || typeof event.kind !== 'string') {
    throw new Error(`Not a run event: ${text.slice(0, 80)}`);
  }
  return { ...event, data: event.data ?? {} };
}

/** Parse a JSON lines file of events, skipping blank lines. */
export function parseRunEvents(text: string): RunEvent[] {
  return text
    .split('\n')
    .filter((line) => line.trim())
    .map(parseRunEvent);
}
