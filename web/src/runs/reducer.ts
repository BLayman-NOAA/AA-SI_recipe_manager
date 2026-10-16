/**
 * Fold a run's events into a status per step.
 *
 * Pure. Events may arrive twice (a replay after a reconnect) or out of order;
 * a seq already seen is ignored, and a step that has finished never goes
 * back to running.
 */
import type { LogEntry } from '../api/models';
import type {
  FinishedStatus,
  InstanceFinishedData,
  LogData,
  PlanData,
  RunEvent,
  RunFinishedData,
  RunStartedData,
  StepFailedData,
  StepFinishedData,
  StepStartedData,
  WarningData,
} from './events';

export type StepStatus =
  | 'queued'
  | 'running'
  | 'computed'
  | 'hit-user-cache'
  | 'hit-survey-cache'
  | 'marker'
  | 'skipped'
  | 'pruned'
  | 'untargeted'
  | 'failed'
  | 'stopped'
  | 'not-run';

export type Tier = 'user' | 'survey';

export interface InstanceProgress {
  count: number | null;
  done: number;
  computed: number;
  cached: number;
  skipped: number;
}

export interface StepRunState {
  status: StepStatus;
  tier: Tier | null;
  hash: string | null;
  planned: 'compute' | 'load' | 'marker' | 'pruned' | 'untargeted' | null;
  index: number;
  total: number;
  instances: InstanceProgress;
  seconds: number | null;
  error: StepFailedData | null;
  chain: string[] | null;
}

export type RunStateStatus = 'pending' | 'running' | FinishedStatus;

export interface RunState {
  /** The server's run id. */
  runId: string;
  status: RunStateStatus;
  lastSeq: number;
  seen: Set<number>;
  started: RunStartedData | null;
  steps: Record<string, StepRunState>;
  /** Log events, plus one `traceback` entry per failed step. */
  logs: LogEntry[];
  warnings: WarningData[];
  finished: RunFinishedData | null;
}

const TERMINAL: ReadonlySet<StepStatus> = new Set([
  'computed',
  'hit-user-cache',
  'hit-survey-cache',
  'marker',
  'skipped',
  'pruned',
  'untargeted',
  'failed',
  'stopped',
  'not-run',
]);

/** Step statuses that come from a step_finished disposition. */
const DISPOSITIONS: ReadonlySet<string> = new Set([
  'computed',
  'hit-user-cache',
  'hit-survey-cache',
  'marker',
  'skipped',
  'pruned',
  'untargeted',
]);

const FINISHED_STATUSES: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled', 'interrupted']);

export function terminal(status: StepStatus): boolean {
  return TERMINAL.has(status);
}

export function initialRunState(runId: string): RunState {
  return {
    runId,
    status: 'pending',
    lastSeq: 0,
    seen: new Set(),
    started: null,
    steps: {},
    logs: [],
    warnings: [],
    finished: null,
  };
}

function blankStep(): StepRunState {
  return {
    status: 'queued',
    tier: null,
    hash: null,
    planned: null,
    index: 0,
    total: 0,
    instances: { count: null, done: 0, computed: 0, cached: 0, skipped: 0 },
    seconds: null,
    error: null,
    chain: null,
  };
}

function asTier(tier: unknown): Tier | null {
  return tier === 'user' || tier === 'survey' ? tier : null;
}

/**
 * A batch reduction: one copy of the state, edited in place.
 *
 * The steps, logs and warnings are copied only when the batch writes them,
 * so a batch of log events keeps the steps object, and the diagram that
 * reads it does not render again.
 */
class Draft {
  readonly state: RunState;
  private copied = new Set<string>();
  private stepsCopied = false;
  private logsCopied = false;
  private warningsCopied = false;

  constructor(state: RunState) {
    this.state = { ...state, seen: new Set(state.seen) };
  }

  /** The step's state, copied once per batch so earlier states stay unchanged. */
  step(id: string): StepRunState {
    if (!this.stepsCopied) {
      this.state.steps = { ...this.state.steps };
      this.stepsCopied = true;
    }
    const steps = this.state.steps;
    if (!this.copied.has(id)) {
      const existing = steps[id];
      steps[id] = existing ? { ...existing, instances: { ...existing.instances } } : blankStep();
      this.copied.add(id);
    }
    return steps[id];
  }

  private addLog(entry: LogEntry): void {
    if (!this.logsCopied) {
      this.state.logs = this.state.logs.slice();
      this.logsCopied = true;
    }
    this.state.logs.push(entry);
  }

  private addWarning(warning: WarningData): void {
    if (!this.warningsCopied) {
      this.state.warnings = this.state.warnings.slice();
      this.warningsCopied = true;
    }
    this.state.warnings.push(warning);
  }

  get finished(): boolean {
    return this.state.finished !== null;
  }

  apply(event: RunEvent): void {
    const state = this.state;
    if (state.seen.has(event.seq)) return;
    state.seen.add(event.seq);
    state.lastSeq = Math.max(state.lastSeq, event.seq);
    switch (event.kind) {
      case 'run_started':
        state.started = event.data as unknown as RunStartedData;
        if (!this.finished) state.status = 'running';
        break;
      case 'plan':
        this.plan(event.data as unknown as PlanData);
        break;
      case 'step_started':
        this.stepStarted(event.data as unknown as StepStartedData);
        break;
      case 'instance_finished':
        this.instanceFinished(event.data as unknown as InstanceFinishedData);
        break;
      case 'step_finished':
        this.stepFinished(event.data as unknown as StepFinishedData);
        break;
      case 'step_failed':
        this.stepFailed(event, event.data as unknown as StepFailedData);
        break;
      case 'log': {
        const data = event.data as unknown as LogData;
        this.addLog({
          seq: event.seq,
          time: event.time,
          step: data.step ?? null,
          stream: data.stream,
          text: data.text,
          buffered: Boolean(data.buffered),
          instance: data.instance ?? null,
        });
        break;
      }
      case 'warning':
        this.addWarning(event.data as unknown as WarningData);
        break;
      case 'run_finished':
        this.runFinished(event.data as unknown as RunFinishedData);
        break;
    }
  }

  private plan(data: PlanData): void {
    for (const [id, entry] of Object.entries(data.steps ?? {})) {
      const step = this.step(id);
      const planned = entry.planned;
      step.planned =
        planned === 'compute' || planned === 'load' || planned === 'marker' || planned === 'pruned' ||
        planned === 'untargeted'
          ? planned
          : null;
      step.hash = step.hash ?? entry.hash ?? null;
      step.tier = step.tier ?? asTier(entry.tier);
      if (step.status !== 'queued') continue;
      if (planned === 'pruned' || planned === 'untargeted') step.status = planned;
      else if (this.finished) step.status = 'not-run';
    }
  }

  private stepStarted(data: StepStartedData): void {
    const step = this.step(data.step);
    if (!terminal(step.status)) step.status = this.finished ? 'stopped' : 'running';
    step.index = data.index;
    step.total = data.total;
    if (data.instances !== null && data.instances !== undefined) step.instances.count = data.instances;
    if (data.chain) step.chain = data.chain;
  }

  private instanceFinished(data: InstanceFinishedData): void {
    for (const [id, disposition] of Object.entries(data.members ?? {})) {
      const step = this.step(id);
      const counts = step.instances;
      counts.done += 1;
      if (disposition === 'computed') counts.computed += 1;
      // Core reports an instance cache hit as "hit"; accept the step form ("hit-user-cache") too.
      else if (disposition === 'hit' || disposition.startsWith('hit-')) counts.cached += 1;
      else if (disposition === 'skipped') counts.skipped += 1;
      if (counts.count === null && typeof data.count === 'number') counts.count = data.count;
    }
  }

  private stepFinished(data: StepFinishedData): void {
    const step = this.step(data.step);
    step.status = DISPOSITIONS.has(data.disposition) ? (data.disposition as StepStatus) : 'computed';
    step.tier = asTier(data.tier) ?? step.tier;
    step.hash = data.hash ?? step.hash;
    step.seconds = data.seconds ?? null;
    step.index = data.index;
    step.total = data.total;
    if (data.instances) {
      const { count, computed, cached, skipped } = data.instances;
      const counts = step.instances;
      counts.count = count;
      counts.computed = computed;
      counts.cached = cached;
      counts.skipped = skipped;
      counts.done = Math.max(counts.done, computed + cached + skipped);
    }
  }

  private stepFailed(event: RunEvent, data: StepFailedData): void {
    const step = this.step(data.step);
    step.status = 'failed';
    step.error = data;
    step.seconds = data.seconds ?? null;
    if (data.chain) step.chain = data.chain;
    for (const id of data.chain ?? []) {
      if (id !== data.step && this.state.steps[id]?.status === 'running') this.step(id).status = 'stopped';
    }
    this.addLog({
      seq: event.seq,
      time: event.time,
      step: data.step,
      stream: 'traceback',
      text: data.traceback || [data.error_type, data.message].filter(Boolean).join(': '),
      buffered: false,
      instance: data.instance ?? null,
    });
  }

  private runFinished(data: RunFinishedData): void {
    const state = this.state;
    state.finished = data;
    state.status = FINISHED_STATUSES.has(data.status) ? data.status : 'failed';
    for (const [id, step] of Object.entries(state.steps)) {
      if (step.status === 'running') this.step(id).status = 'stopped';
      else if (step.status === 'queued') this.step(id).status = 'not-run';
    }
  }
}

/** Fold a batch of events into a run state; the input state is not changed. */
export function reduceEvents(state: RunState, events: RunEvent[]): RunState {
  if (events.every((event) => state.seen.has(event.seq))) return state;
  const draft = new Draft(state);
  for (const event of events) draft.apply(event);
  return draft.state;
}

/** Fold one event into a run state; a seq already seen returns the state unchanged. */
export function reduceEvent(state: RunState, event: RunEvent): RunState {
  return reduceEvents(state, [event]);
}

/** The steps a run plans to do (compute, load or marker), and how many of them are done. */
export function runProgress(state: RunState): { done: number; total: number } {
  let done = 0;
  let total = 0;
  for (const step of Object.values(state.steps)) {
    if (step.planned === 'pruned' || step.planned === 'untargeted') continue;
    if (step.status === 'pruned' || step.status === 'untargeted') continue;
    total += 1;
    if (terminal(step.status)) done += 1;
  }
  return { done, total };
}

/** Steps that failed in a run, in the order they failed. */
export function failedSteps(state: RunState): StepFailedData[] {
  return Object.values(state.steps)
    .filter((step) => step.status === 'failed' && step.error)
    .map((step) => step.error!)
    .sort((a, b) => a.index - b.index);
}
