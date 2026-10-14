import { useHealth } from '../api/queries';
import { Dialog } from './Dialog';
import { useDialogs } from './dialogs';
import { useDocsUrl } from './links';
import { milestoneTooltip } from './menus';
import { SHORTCUTS } from './shortcuts';

function ShortcutsTable() {
  return (
    <table className="rc-table">
      <thead>
        <tr>
          <th>Keys</th>
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {SHORTCUTS.map((shortcut) => (
          <tr key={shortcut.id}>
            <td>
              <kbd>{shortcut.keys}</kbd>
            </td>
            <td>
              {shortcut.action}
              {shortcut.milestone !== undefined && (
                <span className="rc-row-sub"> ({milestoneTooltip(shortcut.milestone).toLowerCase()})</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function About() {
  const health = useHealth();
  const docsUrl = useDocsUrl();
  return (
    <div>
      <p>AA-SI recipe manager client.</p>
      <table className="rc-table">
        <tbody>
          <tr>
            <th>Server version</th>
            <td>{health.data?.version ?? (health.isError ? 'not reachable' : 'checking')}</td>
          </tr>
          <tr>
            <th>Dev mode</th>
            <td>{health.data ? (health.data.dev ? 'yes' : 'no') : ''}</td>
          </tr>
          <tr>
            <th>State folder</th>
            <td className="rc-mono">{health.data?.state_dir ?? ''}</td>
          </tr>
          <tr>
            <th>Documentation</th>
            <td>
              <a href={docsUrl} target="_blank" rel="noreferrer">
                {docsUrl}
              </a>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

/** The dialogs the menus open. */
export function ShellDialogs() {
  const open = useDialogs((state) => state.open);
  const close = useDialogs((state) => state.close);
  if (open === 'shortcuts') {
    return (
      <Dialog title="Keyboard Shortcuts" onClose={close}>
        <ShortcutsTable />
      </Dialog>
    );
  }
  if (open === 'about') {
    return (
      <Dialog title="About" onClose={close}>
        <About />
      </Dialog>
    );
  }
  return null;
}
