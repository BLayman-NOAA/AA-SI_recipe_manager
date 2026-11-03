/**
 * Pieces the inspector's edit forms share: their edits, the Apply that sends
 * them as one edit and its error, and the field layout. App only.
 */
import { useEffect, useRef, useState, type ReactNode } from 'react';
import { editProblems, isApiError } from '../api/client';
import { STALE_VERSION_MESSAGE, useChangeError, useEdit } from '../api/queries';
import { DraftActions } from '../inputs/InputsPane';
import { Prose } from '../Prose';
import type { FormPlan } from './paramForm';

/** A form's edits by field name, with set, clear and discard. */
export function useEdits<E>() {
  const [edits, setEdits] = useState<Record<string, E>>({});
  return {
    edits,
    setEdit: (name: string, edit: E | undefined) =>
      setEdits((current) => {
        const next = { ...current };
        if (edit === undefined) delete next[name];
        else next[name] = edit;
        return next;
      }),
    discard: () => setEdits({}),
  };
}

type EditMutation = ReturnType<typeof useEdit>;

export const STALE_EDIT_MESSAGE = `${STALE_VERSION_MESSAGE} Apply again to retry.`;

/**
 * The error of a form's last edit, for its Apply: each problem of a 422 on a
 * line. A stale version says so here as well as in the status bar.
 */
export function useEditError(edit: EditMutation): ReactNode {
  const message = useChangeError(edit);
  if (isApiError(edit.error, 'StaleVersion')) return STALE_EDIT_MESSAGE;
  if (!message || !edit.error) return null;
  const problems = editProblems(edit.error);
  if (problems.length === 1) return problems[0];
  return (
    <ul className="rc-list">
      {problems.map((problem, index) => (
        <li key={index}>{problem}</li>
      ))}
    </ul>
  );
}

/** Apply and Discard for a form of edits and its plan. */
export function PlanActions({
  edits,
  plan,
  discard,
  edit,
}: {
  edits: Record<string, unknown>;
  plan: FormPlan;
  discard: () => void;
  edit: EditMutation;
}) {
  const applyError = useEditError(edit);
  return (
    <DraftActions
      invalid={Object.keys(plan.errors).length > 0}
      changed={plan.operations.length > 0}
      count={Object.keys(edits).length}
      discard={discard}
      applying={edit.isPending}
      applyError={applyError}
    />
  );
}

interface EditFieldProps {
  id: string;
  name: string;
  badges?: ReactNode;
  /** A button at the end of the head, such as Reset. */
  action?: ReactNode;
  children: ReactNode;
  error?: string;
  notes?: ReactNode;
  description?: string | null;
}

/**
 * A description behind a small "i" icon. A click opens it as a card; Escape
 * or a click elsewhere closes it.
 */
export function InfoTip({ name, text }: { name: string; text: string }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const doc = ref.current?.ownerDocument;
    if (!open || !doc) return;
    const onPointer = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    // The pane's document, a popout's or the main one, sees the key before the window's
    // shortcuts, so Escape closes only the card.
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return;
      event.stopPropagation();
      setOpen(false);
    };
    doc.addEventListener('pointerdown', onPointer);
    doc.addEventListener('keydown', onKey);
    return () => {
      doc.removeEventListener('pointerdown', onPointer);
      doc.removeEventListener('keydown', onKey);
    };
  }, [open]);
  const label = `Description of ${name}`;
  return (
    <details ref={ref} className="rc-info" open={open}>
      <summary
        aria-label={label}
        title={label}
        onClick={(event) => {
          event.preventDefault();
          setOpen(!open);
        }}
      >
        i
      </summary>
      <div className="rc-info-card">
        <Prose text={text} />
      </div>
    </details>
  );
}

/** One field of an edit form: name, description icon and badges, the control, its error and notes. */
export function EditField({ id, name, badges, action, children, error, notes, description }: EditFieldProps) {
  return (
    <div className="rc-field">
      <div className="rc-field-head">
        <label className="rc-field-name rc-mono" htmlFor={id}>
          {name}
        </label>
        {description && <InfoTip name={name} text={description} />}
        {badges}
        <span className="rc-spacer" />
        {action}
      </div>
      {children}
      {error && <div className="rc-field-error">{error}</div>}
      {notes}
    </div>
  );
}

/** A small button for a field's head. */
export function FieldButton({ label, onClick, disabled }: { label: string; onClick: () => void; disabled?: boolean }) {
  return (
    <button type="button" className="rc-button rc-small" disabled={disabled} onClick={onClick}>
      {label}
    </button>
  );
}

