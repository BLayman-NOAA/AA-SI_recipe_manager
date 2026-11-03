/**
 * Form logic for the inspector's edit forms: parameters, include overrides
 * and input defaults. Controls by type, drafts, and the edit operations a
 * form's Apply sends.
 */
import type { GraphInclude, GraphParam, StepNode } from '../api/graph';
import type { InputField, Problem } from '../api/models';
import {
  setIncludeOverride,
  setParam,
  setRecipeInput,
  unsetIncludeOverride,
  unsetParam,
  unsetRecipeInput,
  type Operation,
} from '../editing/operations';
import {
  controlFor,
  holdsNonFinite,
  isNonFinite,
  parseDraft,
  toDraft,
  type ControlKind,
  type FieldEdit,
  type Parsed,
} from '../inputs/inputForm';
import { escapeRegExp } from '../text';
import { targetOperation, type BindingTarget } from './bindings';

/** A step parameter as the graph gives it, with the spec's required, constraints and fingerprint mode. */
export type ParamField = GraphParam;

export type ParamEdit = FieldEdit;

/**
 * What a parameter field shows: a value control, a read only wire, or a
 * read only value the run config file sets.
 */
export type ParamKind = ControlKind | 'wired' | 'config';

/** The operations a form would send, and the drafts that do not parse. */
export interface FormPlan {
  operations: Operation[];
  errors: Record<string, string>;
}

/** The control for a parameter type: objects, lists and dicts as JSON, paths as text. */
export function controlForParam(type: string | null): ControlKind {
  return type === 'object' ? 'json' : controlFor(type);
}

/** A value with a `${...}` reference in it. */
export function isTemplate(value: unknown): value is string {
  return typeof value === 'string' && value.includes('${');
}

/** The type a value is edited as: the declared one, else one read from the value. */
export function valueType(type: string | null, value: unknown): string | null {
  if (type) return type;
  if (typeof value === 'boolean') return 'bool';
  if (typeof value === 'number') return Number.isInteger(value) ? 'int' : 'float';
  if (Array.isArray(value)) return 'list';
  if (value !== null && typeof value === 'object') return 'dict';
  return 'str';
}

/**
 * The control for a value of a type. A reference or a non finite number is
 * shown as text, since a number input cannot hold it.
 */
export function controlForValue(type: string | null, value: unknown): ControlKind {
  if (isTemplate(value) || isNonFinite(value)) return 'text';
  return controlForParam(valueType(type, value));
}

/**
 * Turn a draft into a value of a type. A draft holding a `${...}` reference
 * is kept as text; anything else must parse as the type, and NaN and infinity
 * never do.
 */
export function parseValue(draft: string | boolean, type: string | null): Parsed {
  if (isTemplate(draft)) return { ok: true, value: draft };
  let parsed: Parsed;
  if (type !== 'object') parsed = parseDraft(draft, type);
  else {
    try {
      parsed = { ok: true, value: JSON.parse(String(draft)) as unknown };
    } catch {
      parsed = { ok: false, error: 'Not valid JSON.' };
    }
  }
  if (parsed.ok && typeof parsed.value === 'object' && holdsNonFinite(parsed.value)) {
    return { ok: false, error: 'NaN and infinity can only be written in the YAML pane.' };
  }
  return parsed;
}

export function sameValue(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

/** The value a parameter has: the set one, else the default. */
export function paramValue(field: ParamField): unknown {
  return field.set ? field.value : field.default;
}

/** The value a parameter field starts from: what its binding's target holds, else its own value. */
export function fieldValue(field: ParamField, target?: BindingTarget): unknown {
  return target ? target.value : paramValue(field);
}

/** The type a parameter is edited as. */
export function paramType(field: ParamField, target?: BindingTarget): string | null {
  return valueType(field.type, fieldValue(field, target));
}

/** A parameter the op does not declare: the graph gives it no fingerprint mode. */
export function isUndeclared(field: ParamField): boolean {
  return field.fingerprint_mode === null;
}

/** A value that reads a step's output, which only wiring may change. */
export function isWire(value: unknown): boolean {
  return typeof value === 'string' && /\$\{(?!inputs\.)/.test(value);
}

/**
 * What a parameter field shows. A bound one edits its binding's target, read
 * only when the run config file sets it or a step's output wires it.
 */
export function paramKind(field: ParamField, target?: BindingTarget): ParamKind {
  if (target?.kind === 'config') return 'config';
  const value = fieldValue(field, target);
  if (target ? isWire(value) : isTemplate(value)) return 'wired';
  return controlForValue(paramType(field, target), value);
}

/** The draft a parameter shows: the user's edit, the default on a reset, else its value. */
export function paramDraft(field: ParamField, edit: ParamEdit | undefined, target?: BindingTarget): string | boolean {
  const kind = controlForValue(paramType(field, target), fieldValue(field, target));
  if (edit && 'draft' in edit) return edit.draft;
  if (edit && 'reset' in edit) return toDraft(field.default, kind);
  return toDraft(fieldValue(field, target), kind);
}

/** The edits of a step's parameter form: edit operations, then session input values. */
export interface ParamPlan extends FormPlan {
  /** New values of recipe inputs the session sets, by input name. */
  inputs: Record<string, unknown>;
}

/**
 * The operations a step's parameter edits come to.
 *
 * A changed value sets the parameter and a reset removes it so the default
 * applies. A bound parameter's edit changes its binding's target instead: an
 * override, an input's default, or the session's value of the input. A value
 * equal to what is there sends nothing.
 *
 * Args:
 *   step: The step.
 *   edits: The edits, by parameter name.
 *   targets: The binding targets of the bound parameters, by name.
 *   problems: The session's validation problems, for exclusiveErrors.
 */
export function paramOperations(
  step: StepNode,
  edits: Record<string, ParamEdit>,
  targets: Record<string, BindingTarget> = {},
  problems: Problem[] = [],
): ParamPlan {
  const operations: Operation[] = [];
  const inputs: Record<string, unknown> = {};
  const errors: Record<string, string> = {};
  for (const field of step.params) {
    const edit = edits[field.name];
    const target = targets[field.name];
    if (!edit || target?.kind === 'config') continue;
    if ('reset' in edit) {
      if (field.set && !target) operations.push(unsetParam(step.id, field.name));
      continue;
    }
    const parsed = parseValue(edit.draft, paramType(field, target));
    if (!parsed.ok) {
      errors[field.name] = parsed.error;
      continue;
    }
    if (sameValue(parsed.value, fieldValue(field, target))) continue;
    if (!target) operations.push(setParam(step.id, field.name, parsed.value));
    else if (target.kind === 'session') inputs[target.name] = parsed.value;
    else operations.push(targetOperation(target, parsed.value)!);
  }
  return { operations, inputs, errors: { ...exclusiveErrors(step, edits, targets, problems), ...errors } };
}

/** The input values the session sets now, the base a form's session changes go on. */
export function sessionValues(declared: InputField[]): Record<string, unknown> {
  return Object.fromEntries(
    declared.filter((field) => field.source === 'session').map((field) => [field.name, field.value]),
  );
}

/** The parameters a parameter may not be set together with (its `exclusive_with` constraint). */
export function exclusiveWith(field: ParamField): string[] {
  const names = field.constraints?.exclusive_with;
  return Array.isArray(names) ? names.filter((name): name is string => typeof name === 'string') : [];
}

/**
 * The value a parameter has once a form's edits apply, or undefined while its
 * draft does not parse. A bound parameter has its binding target's value.
 */
export function formValue(field: ParamField, edit: ParamEdit | undefined, target?: BindingTarget): unknown {
  if (!edit) return fieldValue(field, target);
  if ('reset' in edit) return target ? fieldValue(field, target) : field.default;
  const parsed = parseValue(edit.draft, paramType(field, target));
  return parsed.ok ? parsed.value : undefined;
}

const INPUT_REF = /^\$\{inputs\.\w+\}$/;

/**
 * Whether a parameter is given a value once the form's edits apply, as the
 * server's dry run counts it: an unbound parameter only when the step sets it,
 * since a spec default is not passed to the op, and never a null value or a
 * whole `${inputs.x}` left unresolved.
 */
function isGiven(field: ParamField, edit: ParamEdit | undefined, target?: BindingTarget): boolean {
  if (!target && (edit ? 'reset' in edit : !field.set)) return false;
  const value = formValue(field, edit, target);
  return value !== null && value !== undefined && !(typeof value === 'string' && INPUT_REF.test(value));
}

/**
 * "Set only one of A and B." on both parameters of an exclusive pair that
 * would both be given a value once the form's edits apply, by parameter name.
 * Only a pair the form edits is marked, and not one a validation problem
 * already names, which the fields show.
 */
export function exclusiveErrors(
  step: StepNode,
  edits: Record<string, ParamEdit>,
  targets: Record<string, BindingTarget> = {},
  problems: Problem[] = [],
): Record<string, string> {
  const errors: Record<string, string> = {};
  const index = new Map(step.params.map((field, position) => [field.name, position]));
  const valued = (field: ParamField) => isGiven(field, edits[field.name], targets[field.name]);
  const reported = (a: string, b: string) =>
    paramProblems(problems, step.id, a).some((problem) => paramProblems([problem], step.id, b).length > 0);
  for (const field of step.params) {
    for (const name of exclusiveWith(field)) {
      const other = step.params.find((candidate) => candidate.name === name);
      if (!other || other === field || !(field.name in edits || other.name in edits)) continue;
      if (!valued(field) || !valued(other) || reported(field.name, other.name)) continue;
      const pair = [field.name, other.name].sort((a, b) => index.get(a)! - index.get(b)!);
      const message = `Set only one of ${pair[0]} and ${pair[1]}.`;
      errors[field.name] ??= message;
      errors[other.name] ??= message;
    }
  }
  return errors;
}

/**
 * The operations an include's override edits come to: a new or changed value
 * sets the override, a reset removes it.
 */
export function overrideOperations(include: GraphInclude, edits: Record<string, FieldEdit>): FormPlan {
  const operations: Operation[] = [];
  const errors: Record<string, string> = {};
  for (const [name, edit] of Object.entries(edits)) {
    const present = name in include.overrides;
    if ('reset' in edit) {
      if (present) operations.push(unsetIncludeOverride(include.source, name));
      continue;
    }
    const declaration = include.inputs?.find((input) => input.name === name);
    const parsed = parseValue(edit.draft, valueType(declaration?.type ?? null, include.overrides[name]));
    if (!parsed.ok) errors[name] = parsed.error;
    else if (!present || !sameValue(parsed.value, include.overrides[name])) {
      operations.push(setIncludeOverride(include.source, name, parsed.value));
    }
  }
  return { operations, errors };
}

/** An input declaration whose default a form edits. */
export interface DefaultField {
  name: string;
  type: string | null;
  default: unknown;
  /** The file declaring the input. */
  document: string;
}

/** Whether a declaration has a default to remove. */
export function hasDefault(field: DefaultField): boolean {
  return field.default !== null && field.default !== undefined;
}

/** The operations default edits come to: a changed default sets it, a reset removes it. */
export function defaultOperations(fields: DefaultField[], edits: Record<string, FieldEdit>): FormPlan {
  const operations: Operation[] = [];
  const errors: Record<string, string> = {};
  for (const field of fields) {
    const edit = edits[field.name];
    if (!edit) continue;
    if ('reset' in edit) {
      if (hasDefault(field)) operations.push(unsetRecipeInput(field.document, field.name, 'default'));
      continue;
    }
    if (isNonFinite(field.default) && edit.draft === field.default) continue;
    const parsed = parseValue(edit.draft, valueType(field.type, field.default));
    if (!parsed.ok) errors[field.name] = parsed.error;
    else if (!sameValue(parsed.value, field.default)) {
      operations.push(setRecipeInput(field.document, field.name, 'default', parsed.value));
    }
  }
  return { operations, errors };
}

/** Validation problems of a step that name one of its parameters as a whole word. */
export function paramProblems(problems: Problem[], stepId: string, name: string): Problem[] {
  const word = new RegExp(`\\b${escapeRegExp(name)}\\b`);
  return problems.filter((problem) => problem.step === stepId && word.test(problem.message));
}

/** Notes for a parameter's constraints, such as "The path must exist." */
export function constraintNotes(constraints: Record<string, unknown> | undefined): string[] {
  const notes: string[] = [];
  for (const [key, value] of Object.entries(constraints ?? {})) {
    if (key === 'must_exist') notes.push(value ? 'The path must exist.' : 'The path need not exist yet.');
    else if (key === 'exclusive_with' && Array.isArray(value)) {
      if (value.length > 0) notes.push(`Mutually exclusive with ${value.join(', ')}.`);
    } else if (key === 'create_if_missing') {
      if (value) notes.push('The folder is created if it is missing.');
    } else notes.push(`${key}: ${JSON.stringify(value)}`);
  }
  return notes;
}

/** The tooltip of a path parameter's fingerprint badge. */
export function fingerprintTitle(mode: string): string {
  if (mode === 'off') return 'Only the path text goes into the step hash.';
  return `The folder's contents (${mode}) go into the step hash instead of the path.`;
}
