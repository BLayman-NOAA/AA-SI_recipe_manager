/** Form logic for the Inputs pane: controls by type, drafts and the values to apply. */
import { isRunConfigProblem, type InputField, type Problem } from '../api/models';
import { escapeRegExp } from '../text';

export type ControlKind = 'number' | 'checkbox' | 'text' | 'json';

/** A field the user has touched: the text (or checkbox state) typed, or a reset. */
export type FieldEdit = { draft: string | boolean } | { reset: true };

export type Edits = Record<string, FieldEdit>;

export function controlFor(type: string | null): ControlKind {
  switch (type) {
    case 'int':
    case 'float':
      return 'number';
    case 'bool':
      return 'checkbox';
    case 'list':
    case 'dict':
      return 'json';
    default:
      return 'text';
  }
}

/** The editable form of a value for a control. */
export function toDraft(value: unknown, kind: ControlKind): string | boolean {
  if (kind === 'checkbox') return value === true;
  if (value === null || value === undefined) return '';
  if (kind === 'json') return JSON.stringify(value, null, 2);
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

export type Parsed = { ok: true; value: unknown } | { ok: false; error: string };

/** Turn a draft back into a value of the input's type. */
export function parseDraft(draft: string | boolean, type: string | null): Parsed {
  const kind = controlFor(type);
  if (kind === 'checkbox') return { ok: true, value: draft === true };
  const text = String(draft);
  if (kind === 'number') {
    if (text.trim() === '') return { ok: false, error: 'Enter a number.' };
    const value = Number(text);
    if (!Number.isFinite(value)) return { ok: false, error: 'Enter a number.' };
    if (type === 'int' && !Number.isInteger(value)) {
      return { ok: false, error: 'Enter a whole number.' };
    }
    return { ok: true, value };
  }
  if (kind === 'json') {
    try {
      const value: unknown = JSON.parse(text);
      if (type === 'list' && !Array.isArray(value)) return { ok: false, error: 'Enter a JSON list.' };
      if (type === 'dict' && (value === null || typeof value !== 'object' || Array.isArray(value))) {
        return { ok: false, error: 'Enter a JSON object.' };
      }
      return { ok: true, value };
    } catch {
      return { ok: false, error: 'Not valid JSON.' };
    }
  }
  return { ok: true, value: text };
}

/** The draft a field shows: the user's edit, else the effective value. */
export function fieldDraft(field: InputField, edit: FieldEdit | undefined): string | boolean {
  const kind = controlFor(field.type);
  if (edit && 'draft' in edit) return edit.draft;
  if (edit && 'reset' in edit) return toDraft(field.default, kind);
  return toDraft(field.value, kind);
}

function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export interface FormResult {
  /** The values the session will set: earlier session values plus changes, minus resets. */
  values: Record<string, unknown>;
  errors: Record<string, string>;
  changed: boolean;
}

/** Combine the declared inputs and the user's edits into the PUT body. */
export function formValues(declared: InputField[], edits: Edits): FormResult {
  const values: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  let changed = false;
  for (const field of declared) {
    const edit = edits[field.name];
    const fromSession = field.source === 'session';
    if (edit && 'reset' in edit) {
      if (fromSession) changed = true;
      continue;
    }
    if (edit && 'draft' in edit) {
      const parsed = parseDraft(edit.draft, field.type);
      if (!parsed.ok) {
        errors[field.name] = parsed.error;
        if (fromSession) values[field.name] = field.value;
        continue;
      }
      if (!sameValue(parsed.value, field.value)) {
        values[field.name] = parsed.value;
        changed = true;
        continue;
      }
    }
    if (fromSession) values[field.name] = field.value;
  }
  return { values, errors, changed };
}

/**
 * Validation problems that concern recipe inputs rather than a step.
 *
 * A problem without a step belongs here when it mentions `inputs.` or a
 * declared input name as a whole word; run config problems go to Run Settings.
 */
export function inputProblems(problems: Problem[], declared: InputField[]): Problem[] {
  const names = declared.map((field) => new RegExp(`\\b${escapeRegExp(field.name)}\\b`));
  return problems.filter(
    (problem) =>
      !problem.step &&
      !isRunConfigProblem(problem) &&
      (problem.message.includes('inputs.') || names.some((name) => name.test(problem.message))),
  );
}
