/** The run config box's inspector: the file in use, its inputs and what becomes of each, its settings. App only. */
import type { InputField, RunConfigBlock } from '../api/models';
import { configRowTag } from '../diagram/buildView';
import { fileName, runConfigView, type ConfigInputState } from '../diagram/runConfig';
import { showPane } from '../shell/dock';
import { showValue } from '../text';
import { Facts, Mono } from './InspectorBody';

const STATE_BADGE: Record<ConfigInputState, string> = {
  set: 'rc-badge-config',
  overridden: 'rc-badge-session',
  unused: '',
};

/** The other recipes using the file, by name with the path as the title. */
export function SharedWith({ paths }: { paths: string[] }) {
  if (paths.length === 0) return <span className="rc-muted">no other recipe in this folder</span>;
  return (
    <>
      {paths.map((path, index) => (
        <span key={path} className="rc-mono" title={path}>
          {index > 0 && ', '}
          {fileName(path)}
        </span>
      ))}
    </>
  );
}

export function RunConfigInspector({ block, declared }: { block: RunConfigBlock; declared: InputField[] }) {
  const view = runConfigView(block, declared);
  if (!view) return <div className="rc-placeholder">The session uses no run config file.</div>;
  const settings = Object.entries(block.contents?.settings ?? {});
  return (
    <div className="rc-pane rc-inspector">
      <div className="rc-inspector-title">
        <span className="rc-mono">{view.name}</span>
        <span className="rc-badge">run config</span>
      </div>
      <Facts
        rows={[
          ['file', <Mono>{view.path}</Mono>],
          ['found', block.chosen ? 'chosen for this session' : 'as the CLI finds it'],
          ['shared with', <SharedWith paths={block.shared_with} />],
        ]}
      />
      {view.error && <p className="rc-field-error">{view.error}</p>}
      <section>
        <h3>Inputs</h3>
        {view.inputs.length === 0 ? (
          <div className="rc-empty">The file sets no inputs.</div>
        ) : (
          <table className="rc-table">
            <tbody>
              {view.inputs.map((row) => (
                <tr key={row.name}>
                  <th className="rc-mono">{row.name}</th>
                  <td className="rc-mono">
                    {showValue(row.value) || 'null'}{' '}
                    <span className={`rc-badge ${STATE_BADGE[row.state]}`}>
                      {configRowTag(row) ?? 'sets the input'}
                    </span>
                    {row.state === 'overridden' && (
                      <div className="rc-field-help">The session uses {showValue(row.sessionValue) || 'null'}.</div>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <section>
        <h3>Run settings</h3>
        {settings.length === 0 ? (
          <div className="rc-empty">The file sets no run settings.</div>
        ) : (
          <table className="rc-table">
            <tbody>
              {settings.map(([name, value]) => (
                <tr key={name}>
                  <th className="rc-mono">{name}</th>
                  <td className="rc-mono">{showValue(value) || <span className="rc-empty">null</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      <p className="rc-field-help">Edit the file, or choose or create another, in the Run Config pane.</p>
      <button type="button" className="rc-button rc-small" onClick={() => showPane('runConfig')}>
        Open the Run Config pane
      </button>
    </div>
  );
}
