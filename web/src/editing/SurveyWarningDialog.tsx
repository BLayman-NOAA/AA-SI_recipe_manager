import type { Graph } from '../api/graph';
import { useUndo } from '../api/queries';
import { showPane } from '../shell/dock';
import { Dialog } from '../shell/Dialog';
import { useView } from '../state/view';
import { plural } from '../text';
import { useEditingStore } from './store';
import { groupByInclude } from './survey';

/**
 * After an edit that changes the hash of survey backed steps (FR-53): the
 * edit is applied already; Undo takes it back, Keep change closes, and Show
 * on diagram keeps it and highlights the steps. A save to the run config file
 * is not in the session's history, so it has no Undo.
 */
export function SurveyWarningDialog({ sid, graph }: { sid: string; graph: Graph }) {
  const warning = useEditingStore((state) => state.surveyWarning);
  const dismiss = useEditingStore((state) => state.dismissSurveyWarning);
  const undo = useUndo(sid);
  if (!warning) return null;
  const groups = groupByInclude(graph, warning.lost);
  const steps = plural(warning.lost.length, 'survey backed step');
  const lead = warning.config
    ? `The save to the run config file takes ${steps} out of the survey cache, which would now be recomputed in your user cache:`
    : `This edit changes the hash of ${steps}, which would now be recomputed in your user cache:`;

  return (
    <Dialog title="Survey backed steps" onClose={dismiss}>
      <p>{lead}</p>
      {groups.map((group) => (
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
      {warning.config && (
        <p className="rc-field-help">
          The file is written already. Undo does not reach it; change the values back in the Run Config pane to
          use the survey cache again.
        </p>
      )}
      <div className="rc-dialog-actions">
        {!warning.config && (
          <button
            type="button"
            className="rc-button rc-primary"
            autoFocus
            onClick={() => {
              undo.mutate();
              dismiss();
            }}
          >
            Undo
          </button>
        )}
        <button
          type="button"
          className={`rc-button${warning.config ? ' rc-primary' : ''}`}
          autoFocus={warning.config}
          onClick={dismiss}
        >
          Keep change
        </button>
        <button
          type="button"
          className="rc-button"
          onClick={() => {
            dismiss();
            useView.getState().setHighlight(warning.lost);
            showPane('diagram');
          }}
        >
          Show on diagram
        </button>
      </div>
    </Dialog>
  );
}
