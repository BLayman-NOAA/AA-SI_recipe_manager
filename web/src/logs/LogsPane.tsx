import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { LogEntry, RunRecord } from '../api/models';
import { useRunLog, useRuns } from '../api/queries';
import { heldRun, shownRun, useRunStore } from '../runs/store';
import { useSessionStore } from '../state/session';
import { plural } from '../text';
import { filterLogs, instanceText, lastEntries, LOG_STREAMS, stepsOf, type LogFilter } from './logList';

/** "run 3: completed, 2026-10-14 10:21" for the run selector. */
export function runLabel(run: RunRecord, number: number): string {
  const when = (run.started ?? run.created).slice(0, 16).replace('T', ' ');
  return `run ${number}: ${run.status}, ${when}`;
}

interface LogListProps {
  entries: LogEntry[];
  filter: LogFilter;
  /** Incremented to scroll to the filtered step's traceback. */
  focus: number;
}

/** The filtered log: the last entries, following the end unless the user scrolls up. */
export function LogList({ entries, filter, focus }: LogListProps) {
  const list = useRef<HTMLDivElement>(null);
  const [follow, setFollow] = useState(true);
  const filtered = useMemo(() => filterLogs(entries, filter), [entries, filter]);
  const { shown, hidden } = lastEntries(filtered);
  // Starts at 0 so a request made just before the pane opened is honoured.
  const focused = useRef(0);

  useLayoutEffect(() => {
    const element = list.current;
    if (follow && element) element.scrollTop = element.scrollHeight;
  }, [shown, follow]);

  // Scroll to the traceback once it is listed (it may arrive after the request).
  useEffect(() => {
    if (focus === focused.current) return;
    const traceback = list.current?.querySelector('[data-stream="traceback"]');
    if (!traceback) return;
    focused.current = focus;
    setFollow(false);
    traceback.scrollIntoView?.({ block: 'start' });
  }, [focus, shown]);

  const onScroll = () => {
    const element = list.current;
    if (!element) return;
    setFollow(element.scrollHeight - element.scrollTop - element.clientHeight < 8);
  };

  return (
    <>
      <label className="rc-check rc-log-follow">
        <input type="checkbox" checked={follow} onChange={(event) => setFollow(event.target.checked)} />
        Follow
      </label>
      <div className="rc-log" ref={list} onScroll={onScroll} role="log" aria-label="Run log">
        {hidden > 0 && <div className="rc-log-hidden">{plural(hidden, 'earlier entry')} hidden</div>}
        {shown.length === 0 && <div className="rc-empty">No log entries.</div>}
        {shown.map((entry) => (
          <div key={`${entry.seq}-${entry.stream}-${entry.time}`} className={`rc-log-line rc-log-${entry.stream}`} data-stream={entry.stream}>
            <span className="rc-log-step">
              {entry.step ?? 'run'}
              {instanceText(entry.instance) && `[${instanceText(entry.instance)}]`}
            </span>
            <pre className="rc-log-text">{entry.text}</pre>
          </div>
        ))}
      </div>
    </>
  );
}

/** The run log: run selector, filters and the list (FR-70 to FR-72). */
export function LogsPane() {
  const sid = useSessionStore((state) => state.sid);
  const runs = useRuns(sid).data ?? [];
  const logRun = useRunStore((state) => state.logRun);
  const logStep = useRunStore((state) => state.logStep);
  const logFocus = useRunStore((state) => state.logFocus);
  const fallback = useRunStore((state) => shownRun(state)?.runId ?? null);
  const runId = logRun ?? fallback ?? runs[0]?.id ?? null;
  const held = useRunStore((state) => heldRun(state, runId));
  const fetched = useRunLog(runId, null, held === null);
  const [stream, setStream] = useState('');
  const [text, setText] = useState('');
  const { setLogRun, setLogStep } = useRunStore.getState();

  const entries = held ? held.logs : (fetched.data?.entries ?? []);
  const steps = stepsOf(entries);
  const filter = useMemo(() => ({ step: logStep, stream, text }), [logStep, stream, text]);

  if (!sid) return <div className="rc-placeholder">No session is open.</div>;
  if (!runId) return <div className="rc-placeholder">No runs yet. Run the recipe to see its log here.</div>;

  return (
    <div className="rc-pane rc-logs">
      <div className="rc-pane-toolbar">
        <select
          className="rc-input"
          aria-label="Run"
          value={runId}
          onChange={(event) => setLogRun(event.target.value)}
        >
          {!runs.some((run) => run.id === runId) && <option value={runId}>{runId}</option>}
          {runs.map((run, index) => (
            <option key={run.id} value={run.id}>
              {runLabel(run, runs.length - index)}
            </option>
          ))}
        </select>
        <select
          className="rc-input"
          aria-label="Step"
          value={logStep}
          onChange={(event) => setLogStep(event.target.value)}
        >
          <option value="">All steps</option>
          {logStep && !steps.includes(logStep) && <option value={logStep}>{logStep}</option>}
          {steps.map((step) => (
            <option key={step} value={step}>
              {step}
            </option>
          ))}
        </select>
        <select
          className="rc-input"
          aria-label="Stream"
          value={stream}
          onChange={(event) => setStream(event.target.value)}
        >
          <option value="">All streams</option>
          {LOG_STREAMS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        <input
          className="rc-input"
          type="search"
          placeholder="Search the log"
          aria-label="Search the log"
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
      </div>
      {fetched.error && !held && <div className="rc-field-error">{fetched.error.message}</div>}
      {fetched.data?.truncated && !held && (
        <div className="rc-field-help">The server sent the first part of a long log.</div>
      )}
      <LogList entries={entries} filter={filter} focus={logFocus} />
    </div>
  );
}
