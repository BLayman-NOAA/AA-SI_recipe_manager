/**
 * The run config file a session uses, as the diagram's run config box shows it.
 *
 * Pure. The app builds it from the session; the docs site has no session and
 * passes none, so it draws no box.
 */
import type { InputField, RunConfigBlock } from '../api/models';

/** How a config input reaches the run: it sets a recipe input, the session overrides it, or no input takes it. */
export type ConfigInputState = 'set' | 'overridden' | 'unused';

export interface RunConfigInput {
  name: string;
  /** The value the file gives. */
  value: unknown;
  state: ConfigInputState;
  /** The session's value of an overridden input. */
  sessionValue?: unknown;
}

export interface RunConfigSetting {
  name: string;
  value: unknown;
}

export interface RunConfigView {
  /** The file in use, absolute. */
  path: string;
  /** Its file name, the box title. */
  name: string;
  /** Every entry of the file's `inputs:`, in file order. */
  inputs: RunConfigInput[];
  /** The run settings the file gives a value, in the order the server lists them. */
  settings: RunConfigSetting[];
  /** Why the file could not be read, or null. */
  error: string | null;
}

/** The last part of a path. */
export function fileName(path: string): string {
  return path.split(/[\\/]/).pop() ?? path;
}

/**
 * The run config box of a session, or null when it uses no config file.
 *
 * Args:
 *   block: The session's `run_config`.
 *   declared: The session's declared inputs, whose `source` says which the
 *     session overrides.
 */
export function runConfigView(block: RunConfigBlock, declared: InputField[]): RunConfigView | null {
  if (!block.path) return null;
  const fields = new Map(declared.map((field) => [field.name, field]));
  const unused = new Set(block.unused_inputs);
  const inputs = Object.entries(block.contents?.inputs ?? {}).map(([name, value]): RunConfigInput => {
    const field = fields.get(name);
    if (unused.has(name)) return { name, value, state: 'unused' };
    if (field?.source === 'session') return { name, value, state: 'overridden', sessionValue: field.value };
    return { name, value, state: 'set' };
  });
  const settings = Object.entries(block.contents?.settings ?? {})
    .filter(([, value]) => value !== null)
    .map(([name, value]) => ({ name, value }));
  const error = block.error ?? (block.contents ? null : 'The file could not be read.');
  return { path: block.path, name: fileName(block.path), inputs, settings, error };
}
