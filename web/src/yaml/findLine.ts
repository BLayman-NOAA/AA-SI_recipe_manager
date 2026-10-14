/** Find a step's definition line in recipe YAML text. */
import { escapeRegExp } from '../text';

/**
 * The index of the line defining a step, or null.
 *
 * Recipes write `- id: <step>` as the first key of each step; a step whose id
 * is not its first key (`id: <step>` on a later line) is found as a fallback.
 * Quotes and a trailing comment are allowed.
 */
export function stepLine(text: string, stepId: string): number | null {
  const id = `(['"]?)${escapeRegExp(stepId)}\\1\\s*(#.*)?$`;
  const first = new RegExp(`^\\s*-\\s*id:\\s*${id}`);
  const later = new RegExp(`^\\s*id:\\s*${id}`);
  const lines = text.split(/\r?\n/);
  const index = lines.findIndex((line) => first.test(line));
  if (index >= 0) return index;
  const fallback = lines.findIndex((line) => later.test(line));
  return fallback >= 0 ? fallback : null;
}
