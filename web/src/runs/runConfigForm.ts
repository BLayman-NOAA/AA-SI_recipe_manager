/**
 * Form logic of the Run Config pane: the config file's inputs and run
 * settings, the drafts typed over them, and the requests they make.
 *
 * Pure. "Try in this session" sets session values over the file, keeping the
 * session values the form leaves alone (`PUT inputs` and `PUT config`).
 * "Save to config file" writes the file (`POST config-file/save`).
 */
import type { InputField, RunConfigBlock, SaveConfigRequest, SetRunConfigRequest } from '../api/models';
import { holdsNonFinite, toDraft, type ControlKind } from '../inputs/inputForm';
import { controlForValue, parseValue, sameValue, valueType } from '../inspector/paramForm';

const NON_FINITE_ERROR = 'NaN and infinity can only be written in the file itself.';

/** The run settings a config file holds, in the order the server lists them. */
export const SETTINGS = [
  'user_cache_dir',
  'survey_cache_dir',
  'outputs_dir',
  'temp_dir',
  'executor',
  'dask_scheduler',
  'dask_workers',
  'storage_options',
] as const;

export type SettingName = (typeof SETTINGS)[number];

export const SETTING_LABELS: Record<SettingName, string> = {
  user_cache_dir: 'User cache',
  survey_cache_dir: 'Survey cache',
  outputs_dir: 'Outputs',
  temp_dir: 'Temp folder',
  executor: 'Executor',
  dask_scheduler: 'Dask scheduler',
  dask_workers: 'Dask workers',
  storage_options: 'Storage options',
};

export const EXECUTORS = ['sequential', 'dask', 'prefect'] as const;

/** A config input's edit: the text (or checkbox state) typed, or its removal from the file. */
export type ConfigInputEdit = { draft: string | boolean } | { remove: true };

/** The form's edits: config inputs by name (an added input has one too) and settings' typed text. */
export interface ConfigEdits {
  inputs: Record<string, ConfigInputEdit>;
  settings: Partial<Record<SettingName, string>>;
}

export const NO_EDITS: ConfigEdits = { inputs: {}, settings: {} };

/** A config input the form lists: one the file has, or one the user adds. */
export interface ConfigInputField {
  name: string;
  /** The declared type, else one read from the value. */
  type: string | null;
  kind: ControlKind;
  /** The value the file gives; undefined for an added input. */
  fileValue: unknown;
  inFile: boolean;
  /** The recipe declares it, so the session can try a value. */
  field: InputField | null;
}

/** A run setting the form lists. */
export interface SettingField {
  name: SettingName;
  /** What the file gives, or undefined when the file leaves it out. */
  fileValue: unknown;
  /** What a run uses now: the file, then the session. */
  effective: unknown;
  /** The session overrides it. */
  overridden: boolean;
}

/**
 * The config inputs to list: the file's in file order, then the ones the
 * edits add.
 */
export function configInputFields(
  block: RunConfigBlock,
  declared: InputField[],
  edits: ConfigEdits,
): ConfigInputField[] {
  const fields = new Map(declared.map((field) => [field.name, field]));
  const fromFile = block.contents?.inputs ?? {};
  const make = (name: string, inFile: boolean): ConfigInputField => {
    const field = fields.get(name) ?? null;
    const fileValue = inFile ? fromFile[name] : undefined;
    const type = valueType(field?.type ?? null, inFile ? fileValue : field?.default);
    return { name, type, kind: controlForValue(type, fileValue), fileValue, inFile, field };
  };
  const added = Object.keys(edits.inputs).filter((name) => !(name in fromFile));
  return [...Object.keys(fromFile).map((name) => make(name, true)), ...added.map((name) => make(name, false))];
}

/** The declared inputs the file does not list yet, for Add input. */
export function addableInputs(declared: InputField[], fields: ConfigInputField[]): InputField[] {
  const listed = new Set(fields.map((field) => field.name));
  return declared.filter((field) => !listed.has(field.name));
}

/** Every run setting with its file value and the session's override. */
export function settingFields(block: RunConfigBlock): SettingField[] {
  const written = block.contents?.settings ?? {};
  return SETTINGS.map((name) => ({
    name,
    fileValue: written[name],
    effective: block[name],
    overridden: name in block.overrides,
  }));
}

/** The draft a config input shows: the user's edit, else the file's value. */
export function inputDraft(field: ConfigInputField, edit: ConfigInputEdit | undefined): string | boolean {
  if (edit && 'draft' in edit) return edit.draft;
  return toDraft(field.fileValue, field.kind);
}

/** The text a setting shows: the user's, else the file's value. */
export function settingDraft(field: SettingField, edits: ConfigEdits): string {
  const typed = edits.settings[field.name];
  if (typed !== undefined) return typed;
  return toDraft(field.fileValue, field.name === 'storage_options' ? 'json' : 'text') as string;
}

type Parsed = { ok: true; value: unknown } | { ok: false; error: string };

/** A typed input value; an empty text means null, as a new config's inputs are. */
export function parseInput(field: ConfigInputField, draft: string | boolean): Parsed {
  if (typeof draft === 'string' && draft.trim() === '') return { ok: true, value: null };
  return parseValue(draft, field.type);
}

/** A typed setting value; an empty text means null, which the file leaves to the default. */
export function parseSetting(name: SettingName, text: string): Parsed {
  const value = text.trim();
  if (value === '') return { ok: true, value: null };
  if (name === 'dask_workers') {
    const workers = Number(value);
    return Number.isInteger(workers) && workers > 0
      ? { ok: true, value: workers }
      : { ok: false, error: 'Enter a whole number above 0.' };
  }
  if (name === 'executor' && !(EXECUTORS as readonly string[]).includes(value)) {
    return { ok: false, error: `Choose ${EXECUTORS.join(', ')}.` };
  }
  if (name === 'storage_options') {
    try {
      const parsed: unknown = JSON.parse(value);
      if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
        return { ok: false, error: 'Enter a JSON object.' };
      }
      if (holdsNonFinite(parsed)) return { ok: false, error: NON_FINITE_ERROR };
      return { ok: true, value: parsed };
    } catch {
      return { ok: false, error: 'Not valid JSON.' };
    }
  }
  return { ok: true, value };
}

/** Whether a setting value is what the file gives; null matches a setting the file leaves out. */
function sameSetting(field: SettingField, value: unknown): boolean {
  return field.fileValue === undefined ? value === null : sameValue(value, field.fileValue);
}

/** Whether a setting's typed text holds a value other than the file's, a change not saved yet. */
export function isUnsavedSetting(field: SettingField, text: string | undefined): boolean {
  if (text === undefined) return false;
  const parsed = parseSetting(field.name, text);
  return !parsed.ok || !sameSetting(field, parsed.value);
}

export interface ConfigPlan {
  /** Errors by `input:<name>` or `setting:<name>`. */
  errors: Record<string, string>;
  /** The typed values of the inputs edited (not removed), by name. */
  inputs: Record<string, unknown>;
  /** The typed values of the settings edited, by name. */
  settings: Partial<Record<SettingName, unknown>>;
  /** Inputs to leave out of the file. */
  removed: string[];
}

/** Parse every edit of the form. */
export function planEdits(fields: ConfigInputField[], settings: SettingField[], edits: ConfigEdits): ConfigPlan {
  const plan: ConfigPlan = { errors: {}, inputs: {}, settings: {}, removed: [] };
  for (const field of fields) {
    const edit = edits.inputs[field.name];
    if (!edit) continue;
    if ('remove' in edit) {
      plan.removed.push(field.name);
      continue;
    }
    const parsed = parseInput(field, edit.draft);
    if (parsed.ok) plan.inputs[field.name] = parsed.value;
    else plan.errors[`input:${field.name}`] = parsed.error;
  }
  for (const field of settings) {
    const text = edits.settings[field.name];
    if (text === undefined) continue;
    const parsed = parseSetting(field.name, text);
    if (parsed.ok) plan.settings[field.name] = parsed.value;
    else plan.errors[`setting:${field.name}`] = parsed.error;
  }
  return plan;
}

/**
 * What "Save to config file" sends, or null when the file already holds the
 * form's values.
 *
 * `inputs` is the file's whole `inputs:` after the save, in file order with
 * the added ones last, and is left out when no input changes; `settings` names
 * only the settings that change.
 */
export function saveRequest(
  fields: ConfigInputField[],
  settings: SettingField[],
  plan: ConfigPlan,
): Omit<SaveConfigRequest, 'version' | 'scope'> | null {
  const inputs: Record<string, unknown> = {};
  let inputsChanged = plan.removed.length > 0;
  for (const field of fields) {
    if (plan.removed.includes(field.name)) continue;
    if (field.name in plan.inputs) {
      inputs[field.name] = plan.inputs[field.name];
      if (!field.inFile || !sameValue(plan.inputs[field.name], field.fileValue)) inputsChanged = true;
    } else if (field.inFile) {
      inputs[field.name] = field.fileValue;
    }
  }
  const changedSettings: SetRunConfigRequest = {};
  for (const field of settings) {
    if (!(field.name in plan.settings)) continue;
    const value = plan.settings[field.name];
    if (!sameSetting(field, value)) (changedSettings as Record<string, unknown>)[field.name] = value;
  }
  const settingsChanged = Object.keys(changedSettings).length > 0;
  if (!inputsChanged && !settingsChanged) return null;
  return {
    ...(inputsChanged ? { inputs } : {}),
    ...(settingsChanged ? { settings: changedSettings } : {}),
  };
}

/**
 * The file's inputs a save would write back unchanged that hold NaN or
 * infinity: JSON carries them as `.nan` text, which the file would then hold.
 */
export function nonFiniteKept(fields: ConfigInputField[], plan: ConfigPlan): string[] {
  return fields
    .filter((field) => field.inFile && !(field.name in plan.inputs) && !plan.removed.includes(field.name))
    .filter((field) => holdsNonFinite(field.fileValue))
    .map((field) => field.name);
}

/** What "Try in this session" sends. */
export interface SessionRequests {
  /** The `PUT inputs` values: the session's values with the edited inputs over them; null sends nothing. */
  inputs: Record<string, unknown> | null;
  /** The `PUT config` body: the session's overrides with the edited settings over them; null sends nothing. */
  config: SetRunConfigRequest | null;
  /** Edited inputs the recipe does not declare, which a session cannot set. */
  skipped: string[];
}

/**
 * The session requests for the form's edits: each edited value that differs
 * from what a run uses now becomes a session value, and the session values
 * the form leaves alone stay. An empty setting clears its session override.
 */
export function sessionRequests(
  fields: ConfigInputField[],
  settings: SettingField[],
  plan: ConfigPlan,
  declared: InputField[],
  overrides: Record<string, unknown>,
): SessionRequests {
  const skipped: string[] = [];
  const values = Object.fromEntries(
    declared.filter((field) => field.source === 'session').map((field) => [field.name, field.value]),
  );
  let inputsChanged = false;
  for (const field of fields) {
    if (!(field.name in plan.inputs)) continue;
    if (!field.field) {
      skipped.push(field.name);
      continue;
    }
    const value = plan.inputs[field.name];
    if (sameValue(value, field.field.value)) continue;
    values[field.name] = value;
    inputsChanged = true;
  }
  const config: Record<string, unknown> = { ...overrides };
  let configChanged = false;
  for (const field of settings) {
    if (!(field.name in plan.settings)) continue;
    const value = plan.settings[field.name];
    if (value === null ? !field.overridden : sameValue(value, field.effective)) continue;
    config[field.name] = value;
    configChanged = true;
  }
  return {
    inputs: inputsChanged ? values : null,
    config: configChanged ? (config as SetRunConfigRequest) : null,
    skipped,
  };
}
