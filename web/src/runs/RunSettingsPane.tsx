import { useEffect, useState, type ReactNode } from 'react';
import {
  isRunConfigProblem,
  RUN_CONFIG_PREFIX,
  type Problem,
  type RunConfigBlock,
  type SetRunConfigRequest,
} from '../api/models';
import { useCurrentSession, useMutationError, useSetRunConfig } from '../api/queries';
import { showValue } from '../text';

type SettingKey = Exclude<keyof RunConfigBlock, 'source' | 'overrides'>;

const SETTINGS: { key: SettingKey; label: string }[] = [
  { key: 'user_cache_dir', label: 'User cache' },
  { key: 'survey_cache_dir', label: 'Survey cache' },
  { key: 'outputs_dir', label: 'Outputs' },
  { key: 'temp_dir', label: 'Temp folder' },
  { key: 'executor', label: 'Executor' },
  { key: 'dask_scheduler', label: 'Dask scheduler' },
  { key: 'dask_workers', label: 'Dask workers' },
  { key: 'storage_options', label: 'Storage options' },
];

const EXECUTORS = ['sequential', 'dask'] as const;
const SCHEDULERS = ['threads', 'processes'];
/** The scheduler select's value for a scheduler address. */
const ADDRESS = 'address';

/** The run settings the pane edits, as session overrides; null clears one. */
export type RunSettingEdit = Pick<SetRunConfigRequest, 'executor' | 'dask_scheduler' | 'dask_workers'>;

interface RunSettingsViewProps {
  config: RunConfigBlock;
  /** Validation problems; only the run config ones are shown. */
  problems?: Problem[];
  saving?: boolean;
  error?: string | null;
  onSet: (edit: RunSettingEdit) => void;
}

interface FieldProps {
  id: string;
  label: string;
  overridden: boolean;
  children: ReactNode;
}

function Field({ id, label, overridden, children }: FieldProps) {
  return (
    <div className="rc-field">
      <div className="rc-field-head">
        <label className="rc-field-name" htmlFor={id}>
          {label}
        </label>
        {overridden && <span className="rc-badge rc-badge-session">session</span>}
      </div>
      {children}
    </div>
  );
}

/** A text field applied on Enter or when it loses focus. */
function CommitInput({
  id,
  value,
  placeholder,
  type = 'text',
  disabled,
  onCommit,
}: {
  id: string;
  value: string;
  placeholder: string;
  type?: 'text' | 'number';
  disabled?: boolean;
  onCommit: (text: string) => void;
}) {
  const [text, setText] = useState(value);
  useEffect(() => setText(value), [value]);
  const commit = () => {
    if (text !== value) onCommit(text.trim());
  };
  return (
    <input
      id={id}
      className="rc-input"
      type={type}
      min={type === 'number' ? 1 : undefined}
      value={text}
      placeholder={placeholder}
      disabled={disabled}
      onChange={(event) => setText(event.target.value)}
      onBlur={commit}
      onKeyDown={(event) => {
        if (event.key === 'Enter') commit();
      }}
    />
  );
}

/** Effective run settings and where they come from; the executor and its Dask options are editable. */
export function RunSettingsView({ config, problems = [], saving, error, onSet }: RunSettingsViewProps) {
  const configProblems = problems.filter(isRunConfigProblem);
  const overridden = (key: string) => key in config.overrides;
  const executorOverride = config.overrides.executor as string | undefined;
  const schedulerOverride = config.overrides.dask_scheduler as string | undefined;
  const workersOverride = config.overrides.dask_workers as number | undefined;
  const [address, setAddress] = useState(
    schedulerOverride !== undefined && !SCHEDULERS.includes(schedulerOverride),
  );
  const schedulerChoice = address ? ADDRESS : (schedulerOverride ?? '');

  return (
    <div>
      <Field id="rc-executor" label="Executor" overridden={executorOverride !== undefined}>
        <select
          id="rc-executor"
          className="rc-input"
          value={executorOverride ?? ''}
          disabled={saving}
          onChange={(event) => onSet({ executor: (event.target.value || null) as SetRunConfigRequest['executor'] })}
        >
          <option value="">From the run config ({showValue(config.executor) || 'sequential'})</option>
          {EXECUTORS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
      </Field>
      <Field id="rc-dask-scheduler" label="Dask scheduler" overridden={schedulerOverride !== undefined}>
        <select
          id="rc-dask-scheduler"
          className="rc-input"
          value={schedulerChoice}
          disabled={saving}
          onChange={(event) => {
            const value = event.target.value;
            setAddress(value === ADDRESS);
            if (value !== ADDRESS) onSet({ dask_scheduler: value || null });
          }}
        >
          <option value="">From the run config ({showValue(config.dask_scheduler) || 'threads'})</option>
          {SCHEDULERS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
          <option value={ADDRESS}>A scheduler address</option>
        </select>
        {address && (
          <CommitInput
            id="rc-dask-address"
            value={schedulerOverride && !SCHEDULERS.includes(schedulerOverride) ? schedulerOverride : ''}
            placeholder="tcp://host:8786"
            disabled={saving}
            onCommit={(text) => onSet({ dask_scheduler: text || null })}
          />
        )}
      </Field>
      <Field id="rc-dask-workers" label="Dask workers" overridden={workersOverride !== undefined}>
        <CommitInput
          id="rc-dask-workers"
          type="number"
          value={workersOverride === undefined ? '' : String(workersOverride)}
          placeholder={`From the run config (${showValue(config.dask_workers) || 'default'})`}
          disabled={saving}
          onCommit={(text) => {
            const workers = Number.parseInt(text, 10);
            onSet({ dask_workers: Number.isFinite(workers) && workers > 0 ? workers : null });
          }}
        />
        <div className="rc-field-help">The Dask options apply when the executor is dask.</div>
      </Field>
      {error && <div className="rc-field-error">{error}</div>}
      <table className="rc-table">
        <tbody>
          {SETTINGS.map(({ key, label }) => (
            <tr key={key}>
              <th>{label}</th>
              <td className="rc-mono">
                {showValue(config[key]) || <span className="rc-empty">not set</span>}{' '}
                {overridden(key) && <span className="rc-badge rc-badge-session">session</span>}
              </td>
            </tr>
          ))}
          <tr>
            <th>Config file</th>
            <td className="rc-mono">{config.source ?? <span className="rc-empty">none found</span>}</td>
          </tr>
        </tbody>
      </table>
      {configProblems.length > 0 && (
        <section>
          <h3>Problems</h3>
          <ul className="rc-list">
            {configProblems.map((problem, index) => (
              <li key={index} className={`rc-problem-${problem.severity} rc-field-help`}>
                <span className="rc-problem-icon">{problem.severity}</span>{' '}
                {problem.message.slice(RUN_CONFIG_PREFIX.length)}
              </li>
            ))}
          </ul>
        </section>
      )}
      <p className="rc-field-help">The cache, outputs and temp folders become editable in a later milestone.</p>
    </div>
  );
}

export function RunSettingsPane() {
  const session = useCurrentSession();
  const setRunConfig = useSetRunConfig(session.data?.id ?? null);
  const error = useMutationError(setRunConfig);
  if (!session.data) {
    return <div className="rc-placeholder">No session is open.</div>;
  }
  const config = session.data.run_config;
  return (
    <div className="rc-pane">
      <RunSettingsView
        config={config}
        problems={session.data.validation.problems}
        saving={setRunConfig.isPending}
        error={error}
        onSet={(edit) => setRunConfig.mutate({ ...(config.overrides as SetRunConfigRequest), ...edit })}
      />
    </div>
  );
}
