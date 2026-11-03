/** The Inputs section of a sub-recipe with a control for each declared default. App only. */
import { useId, type FormEvent } from 'react';
import { overridingAncestor, rowValue, type InputRow } from '../diagram/buildView';
import { useEdit } from '../api/queries';
import { UnappliedBadge, ValueInput } from '../inputs/InputsPane';
import { isUnapplied, toDraft, type FieldEdit } from '../inputs/inputForm';
import { useViewStore } from '../state/view';
import { includeReach, inputReaders, overriddenNote, runSetNote } from './bindings';
import { InputRowDetails, Mono, OwnInputsSection, rowBadge, RowSummary } from './InspectorBody';
import type { IncludeFormProps } from './OverrideForm';
import { EditField, FieldButton, PlanActions, useEdits } from './formParts';
import { controlForValue, defaultOperations, hasDefault, parseValue, valueType, type DefaultField } from './paramForm';
import { useSharedConfirm } from './SharedValueDialog';

/**
 * One row per input of a sub-recipe, holding a control for the default the
 * file declares and then the row's facts; one Apply sends every change. An
 * input whose value comes from elsewhere says so, since its default then
 * changes nothing; one set further up offers to switch to that override.
 * Apply asks first when a changed default feeds steps outside the
 * sub-recipe. Without a declared input to edit, the read only section is
 * shown.
 */
export function InputDefaultForm({ graph, include, session, rows }: IncludeFormProps) {
  const store = useViewStore();
  const edit = useEdit(session.id);
  const { edits, setEdit, discard } = useEdits<FieldEdit>();
  const idPrefix = useId();
  const { ask, dialog } = useSharedConfirm(graph);
  const fields = new Map<string, DefaultField>(
    rows
      .filter((row) => row.source === 'own')
      .map((row) => [
        row.name,
        {
          name: row.name,
          type: row.type,
          default: include.inputs?.find((input) => input.name === row.name)?.default,
          document: include.source,
        },
      ]),
  );
  const plan = defaultOperations([...fields.values()], edits);
  if (fields.size === 0) return <OwnInputsSection graph={graph} include={include} rows={rows} />;

  const note = (row: InputRow) => {
    if (row.setBy === 'parent') {
      const setter = overridingAncestor(graph, include, row.name);
      if (!setter) return null;
      return (
        <div className="rc-field-help rc-status-warning">
          {overriddenNote(setter.overrides[row.name])}{' '}
          <button
            type="button"
            className="rc-link-button"
            onClick={() => store.getState().select({ kind: 'include', id: setter.id })}
          >
            Edit the override
          </button>
        </div>
      );
    }
    const text = row.setBy === 'run' && row.runSource ? runSetNote(row.runSource) : null;
    return text && <div className="rc-field-help rc-status-warning">{text}</div>;
  };

  const control = (row: InputRow, field: DefaultField) => {
    const type = valueType(field.type, field.default);
    const kind = controlForValue(type, field.default);
    const id = `${idPrefix}rc-default-${row.name}`;
    const fieldEdit = edits[row.name];
    const reset = fieldEdit !== undefined && 'reset' in fieldEdit;
    const unapplied = isUnapplied(fieldEdit, field.default, (draft) => parseValue(draft, type));
    return (
      <EditField
        id={id}
        name={row.name}
        badges={
          <>
            {reset && <span className="rc-badge">removed on apply</span>}
            {unapplied && <UnappliedBadge />}
          </>
        }
        action={
          hasDefault(field) && (
            <FieldButton
              label={reset ? 'Keep' : 'Remove default'}
              onClick={() => setEdit(row.name, reset ? undefined : { reset: true })}
            />
          )
        }
        error={plan.errors[row.name]}
        notes={note(row)}
      >
        <ValueInput
          id={id}
          kind={kind}
          draft={fieldEdit && 'draft' in fieldEdit ? fieldEdit.draft : toDraft(field.default, kind)}
          invalid={Boolean(plan.errors[row.name])}
          integer={type === 'int'}
          disabled={reset}
          onChange={(draft) => setEdit(row.name, { draft })}
        />
      </EditField>
    );
  };

  // A default reaches steps only where nothing else gives the input its value.
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (Object.keys(plan.errors).length > 0 || plan.operations.length === 0) return;
    const reached = rows
      .filter((row) => row.name in edits && (row.setBy === 'default' || row.setBy === 'unset'))
      .flatMap((row) => inputReaders(graph, row.name, include.source));
    ask({
      doc: event.currentTarget.ownerDocument,
      ...includeReach(include, reached),
      apply: () => void edit.mutateAsync(plan.operations).then(discard, () => undefined),
    });
  };

  return (
    <section>
      <h3>Inputs</h3>
      <p className="rc-field-help">
        What <Mono>{include.source}</Mono> declares; each shows the value a run would use, and its field edits the
        file's default.
      </p>
      <form onSubmit={submit}>
        {rows.map((row) => {
          const field = fields.get(row.name);
          return (
            <details key={row.name} className="rc-row-details">
              <RowSummary name={row.name} value={rowValue(row)} source={rowBadge(row)} />
              {field && control(row, field)}
              <InputRowDetails graph={graph} row={row} />
            </details>
          );
        })}
        <PlanActions edits={edits} plan={plan} discard={discard} edit={edit} />
      </form>
      {dialog}
    </section>
  );
}
