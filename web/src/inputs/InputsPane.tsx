import { useId, useState, type FormEvent } from 'react';
import type { InputField, Problem } from '../api/models';
import { useCurrentSession, useMutationError, useSetInputs } from '../api/queries';
import {
  controlFor,
  fieldDraft,
  formValues,
  inputProblems,
  type Edits,
  type FieldEdit,
  type FormResult,
} from './inputForm';

interface FieldProps {
  field: InputField;
  edit: FieldEdit | undefined;
  error: string | undefined;
  onEdit: (edit: FieldEdit | undefined) => void;
  /** Makes the control's element id unique to its form (from useId). */
  idPrefix: string;
}

/** One input's control with its badges, reset, error and description. */
export function InputControl({ field, edit, error, onEdit, idPrefix }: FieldProps) {
  const kind = controlFor(field.type);
  const draft = fieldDraft(field, edit);
  const id = `${idPrefix}rc-input-${field.name}`;
  const className = `rc-input${error ? ' rc-invalid' : ''}`;
  const reset = 'reset' in (edit ?? {});
  const canReset = field.source === 'session' ? !reset : edit !== undefined;

  return (
    <div className="rc-field">
      <div className="rc-field-head">
        <label className="rc-field-name rc-mono" htmlFor={id}>
          {field.name}
        </label>
        {field.type && <span className="rc-badge">{field.type}</span>}
        <span className={`rc-badge rc-badge-${field.source}`}>{field.source}</span>
        {field.required && <span className="rc-badge">required</span>}
        {reset && <span className="rc-badge">reset on apply</span>}
        <span className="rc-spacer" />
        <button
          type="button"
          className="rc-button rc-small"
          disabled={!canReset}
          onClick={() => onEdit(field.source === 'session' ? { reset: true } : undefined)}
        >
          Reset
        </button>
      </div>
      {kind === 'checkbox' && (
        <input
          id={id}
          type="checkbox"
          checked={draft === true}
          onChange={(event) => onEdit({ draft: event.target.checked })}
        />
      )}
      {kind === 'number' && (
        <input
          id={id}
          className={className}
          type="number"
          step={field.type === 'int' ? 1 : 'any'}
          value={String(draft)}
          onChange={(event) => onEdit({ draft: event.target.value })}
        />
      )}
      {kind === 'text' && (
        <input
          id={id}
          className={className}
          type="text"
          value={String(draft)}
          onChange={(event) => onEdit({ draft: event.target.value })}
        />
      )}
      {kind === 'json' && (
        <textarea
          id={id}
          className={`${className} rc-mono`}
          value={String(draft)}
          onChange={(event) => onEdit({ draft: event.target.value })}
        />
      )}
      {error && <div className="rc-field-error">{error}</div>}
      {field.description && <div className="rc-field-help">{field.description}</div>}
    </div>
  );
}

/** The edits of an inputs form and what applying them would send. */
export interface InputDraft {
  edits: Edits;
  result: FormResult;
  invalid: boolean;
  setEdit: (name: string, edit: FieldEdit | undefined) => void;
  discard: () => void;
  /** A form submit handler that applies the values and clears the edits once applied. */
  submit: (onApply: (values: Record<string, unknown>) => Promise<unknown> | void) => (event: FormEvent) => void;
}

/** Edits to the declared inputs, validated by inputForm.ts. */
export function useInputDraft(declared: InputField[]): InputDraft {
  const [edits, setEdits] = useState<Edits>({});
  const result = formValues(declared, edits);
  const invalid = Object.keys(result.errors).length > 0;
  return {
    edits,
    result,
    invalid,
    setEdit: (name, edit) =>
      setEdits((current) => {
        const next = { ...current };
        if (edit === undefined) delete next[name];
        else next[name] = edit;
        return next;
      }),
    discard: () => setEdits({}),
    submit: (onApply) => (event) => {
      event.preventDefault();
      if (invalid || !result.changed) return;
      Promise.resolve(onApply(result.values)).then(
        () => setEdits({}),
        () => undefined,
      );
    },
  };
}

interface DraftActionsProps {
  draft: InputDraft;
  applying?: boolean;
  applyError?: string | null;
}

/** Apply and Discard for an inputs form, and the error of the last apply. */
export function DraftActions({ draft, applying, applyError }: DraftActionsProps) {
  return (
    <>
      <div className="rc-pane-toolbar">
        <button
          type="submit"
          className="rc-button rc-primary"
          disabled={draft.invalid || !draft.result.changed || applying}
        >
          Apply
        </button>
        <button
          type="button"
          className="rc-button"
          disabled={Object.keys(draft.edits).length === 0}
          onClick={draft.discard}
        >
          Discard changes
        </button>
      </div>
      {applyError && <div className="rc-field-error">{applyError}</div>}
    </>
  );
}

/** Problems that name a declared input. */
export function InputProblems({ problems, declared }: { problems: Problem[]; declared: InputField[] }) {
  const related = inputProblems(problems, declared);
  if (related.length === 0) return null;
  return (
    <section>
      <h3>Problems</h3>
      <ul className="rc-list">
        {related.map((problem, index) => (
          <li key={index} className={`rc-problem-${problem.severity} rc-field-help`}>
            <span className="rc-problem-icon">{problem.severity}</span> {problem.message}
          </li>
        ))}
      </ul>
    </section>
  );
}

export interface InputsFormProps {
  declared: InputField[];
  problems: Problem[];
  applying?: boolean;
  applyError?: string | null;
  /** Send the values; edits are cleared once the returned promise resolves. */
  onApply: (values: Record<string, unknown>) => Promise<unknown> | void;
}

/** One control per declared recipe input; Apply sends the session's values. */
export function InputsForm({ declared, problems, applying, applyError, onApply }: InputsFormProps) {
  const draft = useInputDraft(declared);
  const idPrefix = useId();

  if (declared.length === 0) {
    return <div className="rc-empty">This recipe declares no inputs.</div>;
  }
  return (
    <form onSubmit={draft.submit(onApply)}>
      {declared.map((field) => (
        <InputControl
          key={field.name}
          field={field}
          edit={draft.edits[field.name]}
          error={draft.result.errors[field.name]}
          onEdit={(edit) => draft.setEdit(field.name, edit)}
          idPrefix={idPrefix}
        />
      ))}
      <DraftActions draft={draft} applying={applying} applyError={applyError} />
      <InputProblems problems={problems} declared={declared} />
    </form>
  );
}

export function InputsPane() {
  const session = useCurrentSession();
  const setInputs = useSetInputs(session.data?.id ?? null);
  const applyError = useMutationError(setInputs);
  if (!session.data) {
    return <div className="rc-placeholder">No session is open.</div>;
  }
  return (
    <div className="rc-pane">
      <InputsForm
        key={session.data.id}
        declared={session.data.inputs.declared}
        problems={session.data.validation.problems}
        applying={setInputs.isPending}
        applyError={applyError}
        onApply={(values) => setInputs.mutateAsync(values)}
      />
    </div>
  );
}
