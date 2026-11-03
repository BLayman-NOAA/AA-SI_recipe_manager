/** The Overrides section of a sub-recipe with a control in each row. App only. */
import { useId, type FormEvent } from 'react';
import type { Graph, GraphInclude } from '../api/graph';
import type { SessionDocument } from '../api/models';
import { useEdit } from '../api/queries';
import { rowValue, type InputRow } from '../diagram/buildView';
import { UnappliedBadge, ValueInput } from '../inputs/InputsPane';
import { isUnapplied, toDraft, type FieldEdit } from '../inputs/inputForm';
import { showValue } from '../text';
import { includerFile } from './bindings';
import { Mono, OverrideDetails, RowSummary } from './InspectorBody';
import { EditField, FieldButton, PlanActions, useEdits } from './formParts';
import { controlForValue, isWire, overrideOperations, parseValue, valueType } from './paramForm';

export interface IncludeFormProps {
  graph: Graph;
  include: GraphInclude;
  session: SessionDocument;
  /** The section's read only rows, from InspectorBody. */
  rows: InputRow[];
}

/**
 * One row per override of an include entry, holding its control and then its
 * facts, plus the file's declared inputs not yet overridden to add; one Apply
 * sends every change.
 */
export function OverrideForm({ graph, include, session, rows }: IncludeFormProps) {
  const edit = useEdit(session.id);
  const { edits, setEdit, discard } = useEdits<FieldEdit>();
  const idPrefix = useId();
  const plan = overrideOperations(include, edits);
  const declared = include.inputs ?? [];
  const byName = new Map(rows.map((row) => [row.name, row]));
  const added = Object.keys(edits).filter((name) => !(name in include.overrides));
  const names = [...Object.keys(include.overrides), ...added];
  const addable = declared.filter((input) => !(input.name in include.overrides) && !(input.name in edits));

  const field = (name: string) => {
    const present = name in include.overrides;
    const current = include.overrides[name];
    const declaration = declared.find((input) => input.name === name);
    const start = present ? current : declaration?.default;
    const type = valueType(declaration?.type ?? null, start);
    const kind = controlForValue(type, start);
    const id = `${idPrefix}rc-override-${name}`;
    const fieldEdit = edits[name];
    const reset = fieldEdit !== undefined && 'reset' in fieldEdit;
    const wire = present && isWire(current);
    const unapplied = isUnapplied(fieldEdit, present ? current : undefined, (draft) =>
      parseValue(draft, valueType(declaration?.type ?? null, current)),
    );
    let action = null;
    if (!present) action = <FieldButton label="Cancel" onClick={() => setEdit(name, undefined)} />;
    else if (!wire) {
      action = (
        <FieldButton
          label={reset ? 'Keep' : 'Remove'}
          onClick={() => setEdit(name, reset ? undefined : { reset: true })}
        />
      );
    }
    return (
      <EditField
        id={id}
        name={name}
        badges={
          <>
            {declaration?.type && <span className="rc-badge">{declaration.type}</span>}
            {reset && <span className="rc-badge">removed on apply</span>}
            {unapplied && <UnappliedBadge />}
          </>
        }
        action={action}
        error={plan.errors[name]}
        description={declaration?.description}
      >
        {wire ? (
          <div className="rc-field-help">Wired from another step; wiring can't be edited here yet.</div>
        ) : (
          <ValueInput
            id={id}
            kind={kind}
            draft={fieldEdit && 'draft' in fieldEdit ? fieldEdit.draft : toDraft(start, kind)}
            invalid={Boolean(plan.errors[name])}
            integer={type === 'int'}
            disabled={reset}
            onChange={(draft) => setEdit(name, { draft })}
          />
        )}
      </EditField>
    );
  };

  const add = (name: string) => {
    const declaration = declared.find((input) => input.name === name);
    if (!declaration) return;
    const type = valueType(declaration.type, declaration.default);
    setEdit(name, { draft: toDraft(declaration.default, controlForValue(type, declaration.default)) });
  };

  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (Object.keys(plan.errors).length > 0 || plan.operations.length === 0) return;
    void edit.mutateAsync(plan.operations).then(discard, () => undefined);
  };

  return (
    <section>
      <h3>Overrides</h3>
      <p className="rc-field-help">
        Values the parent sets where it includes this sub-recipe, written in{' '}
        <Mono>{includerFile(graph, include)}</Mono>.
      </p>
      <form onSubmit={submit}>
        {names.length === 0 && <div className="rc-empty">No overrides.</div>}
        {names.map((name) => {
          const row = byName.get(name);
          const present = name in include.overrides;
          // A row added here opens on its control; the others open when clicked.
          return (
            <details key={name} className="rc-row-details" open={present ? undefined : true}>
              <RowSummary
                name={name}
                value={row ? rowValue(row) : showValue(include.overrides[name])}
                source={present ? undefined : 'new'}
              />
              {field(name)}
              {row && <OverrideDetails graph={graph} row={row} />}
            </details>
          );
        })}
        {addable.length > 0 && (
          <select
            className="rc-input"
            aria-label="Add an override"
            value=""
            onChange={(event) => add(event.target.value)}
          >
            <option value="">Add an override</option>
            {addable.map((input) => (
              <option key={input.name} value={input.name}>
                {input.name}
              </option>
            ))}
          </select>
        )}
        <PlanActions edits={edits} plan={plan} discard={discard} edit={edit} />
      </form>
    </section>
  );
}
