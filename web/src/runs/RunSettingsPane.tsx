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

interface RunSettingsViewProps {
  config: RunConfigBlock;
  /** Validation problems; only the run config ones are shown. */
  problems?: Problem[];
  saving?: boolean;
  error?: string | null;
  onSetExecutor: (executor: SetRunConfigRequest['executor']) => void;
}

/** Effective run settings and where they come from; only the executor is editable. */
export function RunSettingsView({ config, problems = [], saving, error, onSetExecutor }: RunSettingsViewProps) {
  const configProblems = problems.filter(isRunConfigProblem);
  const overridden = (key: string) => key in config.overrides;
  const executorOverride = config.overrides.executor as string | undefined;

  return (
    <div>
      <div className="rc-field">
        <div className="rc-field-head">
          <label className="rc-field-name" htmlFor="rc-executor">
            Executor
          </label>
          {executorOverride !== undefined && <span className="rc-badge rc-badge-session">session</span>}
        </div>
        <select
          id="rc-executor"
          className="rc-input"
          value={executorOverride ?? ''}
          disabled={saving}
          onChange={(event) =>
            onSetExecutor((event.target.value || null) as SetRunConfigRequest['executor'])
          }
        >
          <option value="">From the run config ({showValue(config.executor) || 'sequential'})</option>
          {EXECUTORS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>
        {error && <div className="rc-field-error">{error}</div>}
      </div>
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
      <p className="rc-field-help">Other run settings become editable in a later milestone.</p>
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
        onSetExecutor={(executor) =>
          setRunConfig.mutate({ ...(config.overrides as SetRunConfigRequest), executor })
        }
      />
    </div>
  );
}
