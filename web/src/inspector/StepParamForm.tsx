/**
 * The step inspector's Parameters section as an edit form. App only: it sends
 * edits through the query client, so the docs site keeps the read only table.
 */
import { useId, type FormEvent } from 'react';
import { fieldSources } from '../api/fields';
import type { Graph, StepNode } from '../api/graph';
import type { Problem, SessionDocument } from '../api/models';
import { useEdit, useMutationError, useSetInputs } from '../api/queries';
import { fileName } from '../diagram/runConfig';
import { DraftActions, UnappliedBadge, ValueInput } from '../inputs/InputsPane';
import { isNonFinite, isUnapplied } from '../inputs/inputForm';
import { showValue } from '../text';
import { bindingTarget, paramBinding, stepReach, targetReaders, targetText, type BindingTarget } from './bindings';
import { EditField, FieldButton, useEditError, useEdits } from './formParts';
import {
  constraintNotes,
  fieldValue,
  fingerprintTitle,
  formValue,
  isUndeclared,
  paramDraft,
  paramKind,
  paramOperations,
  paramProblems,
  paramType,
  parseValue,
  sameValue,
  sessionValues,
  type ParamEdit,
  type ParamField,
} from './paramForm';
import { useSharedConfirm } from './SharedValueDialog';

export const SURVEY_BANNER =
  'Loaded from the survey cache. Changing parameters here means recomputing this step and everything ' +
  'after it in your cache.';

function ProblemLines({ problems }: { problems: Problem[] }) {
  return (
    <>
      {problems.map((problem, index) => (
        <div
          key={index}
          className={problem.severity === 'error' ? 'rc-field-error' : 'rc-field-help rc-status-warning'}
        >
          {problem.message}
        </div>
      ))}
    </>
  );
}

interface ParamControlProps {
  graph: Graph;
  stepId: string;
  field: ParamField;
  target: BindingTarget | undefined;
  /** The run config file's name, for a value set there. */
  configFile: string | null;
  edit: ParamEdit | undefined;
  error: string | undefined;
  problems: Problem[];
  onEdit: (edit: ParamEdit | undefined) => void;
  onOpenRunConfig?: () => void;
  idPrefix: string;
}

function ParamControl(props: ParamControlProps) {
  const { graph, stepId, field, target, configFile, edit, error, problems, onEdit, onOpenRunConfig, idPrefix } =
    props;
  const kind = paramKind(field, target);
  const id = `${idPrefix}rc-param-${stepId}-${field.name}`;
  const type = paramType(field, target);
  const reset = edit !== undefined && 'reset' in edit;
  const editable = kind !== 'wired' && kind !== 'config';
  const mode = field.type === 'path' ? field.fingerprint_mode : null;
  const unapplied = isUnapplied(edit, fieldValue(field, target), (draft) => parseValue(draft, type));
  const badges = (
    <>
      {field.type && <span className="rc-badge">{field.type}</span>}
      {field.units && (
        <span className="rc-badge" title="units">
          {field.units}
        </span>
      )}
      <span className={`rc-badge${field.set ? ' rc-badge-session' : ''}`}>{field.set ? 'set' : 'default'}</span>
      {field.required && <span className="rc-badge">required</span>}
      {isUndeclared(field) && (
        <span className="rc-badge rc-badge-warning" title="The op's spec has no parameter of this name.">
          not declared by the op
        </span>
      )}
      {mode && (
        <span className="rc-badge" title={fingerprintTitle(mode)}>
          fingerprint {mode}
        </span>
      )}
      {reset && <span className="rc-badge">reset on apply</span>}
      {unapplied && <UnappliedBadge />}
    </>
  );
  let action = null;
  if (editable && !target) {
    action = (
      <FieldButton
        label="Reset"
        disabled={field.set ? reset : edit === undefined}
        onClick={() => onEdit(field.set ? { reset: true } : undefined)}
      />
    );
  } else if (editable) {
    action = <FieldButton label="Reset" disabled={edit === undefined} onClick={() => onEdit(undefined)} />;
  }
  const notes = (
    <>
      <ProblemLines problems={problems} />
      {editable && isNonFinite(fieldValue(field, target)) && (
        <div className="rc-field-help">Not a finite number; NaN and infinity can only be written in the YAML pane.</div>
      )}
      {constraintNotes(field.constraints).map((note) => (
        <div key={note} className="rc-field-help">
          {note}
        </div>
      ))}
    </>
  );
  return (
    <EditField
      id={id}
      name={field.name}
      badges={badges}
      action={action}
      error={error}
      notes={notes}
      description={field.description}
    >
      {kind === 'wired' && (
        <>
          <div className="rc-mono rc-wrap">{showValue(fieldValue(field, target))}</div>
          <div className="rc-field-help">Wired from another step; wiring can't be edited here yet.</div>
        </>
      )}
      {kind === 'config' && <div className="rc-mono rc-wrap">{showValue(fieldValue(field, target)) || 'null'}</div>}
      {editable && (
        <ValueInput
          id={id}
          kind={kind}
          draft={paramDraft(field, edit, target)}
          invalid={Boolean(error)}
          integer={type === 'int'}
          onChange={(draft) => onEdit({ draft })}
        />
      )}
      {target && kind !== 'wired' && (
        <div className="rc-field-help rc-param-target">
          {targetText(graph, target, configFile)}
          {target.kind === 'config' && onOpenRunConfig && (
            <>
              {' '}
              <button type="button" className="rc-link-button" onClick={onOpenRunConfig}>
                Open Run Config
              </button>
            </>
          )}
        </div>
      )}
    </EditField>
  );
}

export interface StepParamFormProps {
  graph: Graph;
  step: StepNode;
  session: SessionDocument;
  /** The survey tier holds the step (its badge's surveyBacked). */
  surveyBacked: boolean;
  /** Shows the Run Config pane, where a value the run config file sets is edited. */
  onOpenRunConfig?: () => void;
}

/**
 * One field per parameter of a step, with one Apply sending every change. A
 * bound parameter edits the place its value is set (bindings.ts): an
 * override or an input default by an edit, a session value through the
 * session's inputs. A change to a value other steps use is confirmed first.
 */
export function StepParamForm({ graph, step, session, surveyBacked, onOpenRunConfig }: StepParamFormProps) {
  const edit = useEdit(session.id);
  const setInputs = useSetInputs(session.id);
  const editError = useEditError(edit);
  const inputsError = useMutationError(setInputs);
  const { edits, setEdit, discard } = useEdits<ParamEdit>();
  const { ask, dialog } = useSharedConfirm(graph);
  const idPrefix = useId();
  const declared = session.inputs.declared;
  const sources = fieldSources(declared);
  const targets: Record<string, BindingTarget> = {};
  for (const field of step.params) {
    const binding = paramBinding(graph, step, field.name, sources);
    if (binding) targets[field.name] = bindingTarget(binding, session.inputs.values);
  }
  const plan = paramOperations(step, edits, targets, session.validation.problems);
  const invalid = Object.keys(plan.errors).length > 0;
  const changed = plan.operations.length > 0 || Object.keys(plan.inputs).length > 0;
  const configFile = session.run_config.path ? fileName(session.run_config.path) : null;

  // Edit operations first, then the session's values; a failure keeps the form.
  const apply = async () => {
    try {
      if (plan.operations.length > 0) await edit.mutateAsync(plan.operations);
      if (Object.keys(plan.inputs).length > 0) {
        await setInputs.mutateAsync({ ...sessionValues(declared), ...plan.inputs });
      }
      discard();
    } catch {
      // Shown by the form's apply error.
    }
  };
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (invalid || !changed) return;
    const reached = new Set<string>();
    for (const field of step.params) {
      const target = targets[field.name];
      const edited = edits[field.name];
      if (!target || !edited || sameValue(formValue(field, edited, target), target.value)) continue;
      for (const id of targetReaders(graph, target)) reached.add(id);
    }
    ask({ doc: event.currentTarget.ownerDocument, ...stepReach(step.id, [...reached]), apply: () => void apply() });
  };
  return (
    <section>
      <h3>Parameters</h3>
      {surveyBacked && (
        <div className="rc-banner rc-survey-banner" role="note">
          {SURVEY_BANNER}
        </div>
      )}
      {step.params.length === 0 ? (
        <div className="rc-empty">No parameters.</div>
      ) : (
        <form onSubmit={submit}>
          {step.params.map((field) => (
            <ParamControl
              key={field.name}
              graph={graph}
              stepId={step.id}
              field={field}
              target={targets[field.name]}
              configFile={configFile}
              edit={edits[field.name]}
              error={plan.errors[field.name]}
              problems={paramProblems(session.validation.problems, step.id, field.name)}
              onEdit={(next) => setEdit(field.name, next)}
              onOpenRunConfig={onOpenRunConfig}
              idPrefix={idPrefix}
            />
          ))}
          <DraftActions
            invalid={invalid}
            changed={changed}
            count={Object.keys(edits).length}
            discard={discard}
            applying={edit.isPending || setInputs.isPending}
            applyError={editError ?? inputsError}
          />
        </form>
      )}
      {dialog}
    </section>
  );
}
