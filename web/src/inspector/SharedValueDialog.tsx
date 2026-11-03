/** Asking before an edit changes a value other steps also use. App only. */
import { useState } from 'react';
import type { Graph } from '../api/graph';
import { groupByInclude } from '../editing/survey';
import { Dialog } from '../shell/Dialog';
import { plural } from '../text';
import type { Reach } from './bindings';

/** An edit waiting for the user to confirm it. */
export interface SharedChange extends Reach {
  /** The document of the form's window, where the dialog shows. */
  doc: Document;
  apply: () => void;
}

function SharedValueDialog({ graph, change, onDone }: { graph: Graph; change: SharedChange; onDone: () => void }) {
  return (
    <Dialog doc={change.doc} title="Shared value" onClose={onDone}>
      <p>This value also feeds {change.lead}:</p>
      {groupByInclude(graph, change.others).map((group) => (
        <div key={group.include?.id ?? 'root'} className="rc-saveas-section">
          <div className="rc-field-name">{group.include ? group.include.title : 'Top level'}</div>
          <ul className="rc-dialog-list">
            {group.steps.map((id) => (
              <li key={id} className="rc-mono">
                {id}
              </li>
            ))}
          </ul>
        </div>
      ))}
      <div className="rc-dialog-actions">
        <button type="button" className="rc-button" onClick={onDone}>
          Cancel
        </button>
        <button
          type="button"
          className="rc-button rc-primary"
          autoFocus
          onClick={() => {
            onDone();
            change.apply();
          }}
        >
          Change for all {plural(change.total, 'step')}
        </button>
      </div>
    </Dialog>
  );
}

/**
 * A form's confirmation of edits to shared values: `ask` applies at once when
 * no other step is listed, else shows the dialog, which `dialog` renders.
 */
export function useSharedConfirm(graph: Graph) {
  const [pending, setPending] = useState<SharedChange | null>(null);
  const ask = (change: SharedChange) => {
    if (change.others.length === 0) change.apply();
    else setPending(change);
  };
  const dialog = pending && <SharedValueDialog graph={graph} change={pending} onDone={() => setPending(null)} />;
  return { ask, dialog };
}
