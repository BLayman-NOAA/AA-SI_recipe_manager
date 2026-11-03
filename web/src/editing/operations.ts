/** Edit operations in the server's wire format, their constructors and menu labels. */

/** A key path into a parameter value: mapping keys and list indexes. */
export type ValuePath = (string | number)[];

export type CheckpointValue = 'always' | 'never' | 'save' | null;

export interface SetParamOp {
  kind: 'set_param';
  step: string;
  name: string;
  value: unknown;
  path: ValuePath;
  position: number | null;
}

export interface UnsetParamOp {
  kind: 'unset_param';
  step: string;
  name: string;
  path: ValuePath;
}

export interface SetCheckpointOp {
  kind: 'set_checkpoint';
  step: string;
  value: CheckpointValue;
  position: number | null;
}

export interface SetIncludeOverrideOp {
  kind: 'set_include_override';
  /** The include's source file, as the graph's `includes[].source` names it. */
  include: string;
  name: string;
  value: unknown;
  position: number | null;
}

export interface UnsetIncludeOverrideOp {
  kind: 'unset_include_override';
  include: string;
  name: string;
}

export interface SetRecipeInputOp {
  kind: 'set_recipe_input';
  /** The declaring file's label. */
  document: string;
  name: string;
  /** A field of the input declaration, such as `default`. */
  field: string;
  value: unknown;
  position: number | null;
}

export interface UnsetRecipeInputOp {
  kind: 'unset_recipe_input';
  document: string;
  name: string;
  field: string;
}

export interface ReplaceTextOp {
  kind: 'replace_text';
  document: string;
  text: string;
}

export type Operation =
  | SetParamOp
  | UnsetParamOp
  | SetCheckpointOp
  | SetIncludeOverrideOp
  | UnsetIncludeOverrideOp
  | SetRecipeInputOp
  | UnsetRecipeInputOp
  | ReplaceTextOp;

export type OperationKind = Operation['kind'];

export function setParam(
  step: string,
  name: string,
  value: unknown,
  path: ValuePath = [],
  position: number | null = null,
): SetParamOp {
  return { kind: 'set_param', step, name, value, path, position };
}

/** Remove a parameter (or a key inside it) so the op's default applies. */
export function unsetParam(step: string, name: string, path: ValuePath = []): UnsetParamOp {
  return { kind: 'unset_param', step, name, path };
}

/** Set a step's checkpoint key; null removes it. */
export function setCheckpoint(step: string, value: CheckpointValue, position: number | null = null): SetCheckpointOp {
  return { kind: 'set_checkpoint', step, value, position };
}

export function setIncludeOverride(
  include: string,
  name: string,
  value: unknown,
  position: number | null = null,
): SetIncludeOverrideOp {
  return { kind: 'set_include_override', include, name, value, position };
}

export function unsetIncludeOverride(include: string, name: string): UnsetIncludeOverrideOp {
  return { kind: 'unset_include_override', include, name };
}

export function setRecipeInput(
  document: string,
  name: string,
  field: string,
  value: unknown,
  position: number | null = null,
): SetRecipeInputOp {
  return { kind: 'set_recipe_input', document, name, field, value, position };
}

export function unsetRecipeInput(document: string, name: string, field: string): UnsetRecipeInputOp {
  return { kind: 'unset_recipe_input', document, name, field };
}

/** Replace a whole file's text (the YAML pane, a reload from disk). */
export function replaceText(document: string, text: string): ReplaceTextOp {
  return { kind: 'replace_text', document, text };
}

const KIND_TEXT: Record<OperationKind, string> = {
  set_param: 'parameter change',
  unset_param: 'parameter reset',
  set_checkpoint: 'checkpoint change',
  set_include_override: 'override change',
  unset_include_override: 'override removal',
  set_recipe_input: 'input change',
  unset_recipe_input: 'input removal',
  replace_text: 'text edit',
};

/**
 * The menu label of an Undo or Redo item from the session's `undo_kind` or
 * `redo_kind`: "Undo parameter change", or plain "Undo" with nothing to undo.
 */
export function historyLabel(action: 'Undo' | 'Redo', kind: string | null): string {
  if (!kind) return action;
  const text = KIND_TEXT[kind as OperationKind] ?? kind.replace(/_/g, ' ');
  return `${action} ${text}`;
}
