/** What the inspector shows, derived from graph nodes. */
import type { StepNode } from '../api/graph';

export { paramRows, type ParamRow } from '../diagram/buildView';

/** The op reference page for a step, as an absolute URL, or null. */
export function opReferenceUrl(step: StepNode, docsUrl: string): string | null {
  if (!step.op_url) return null;
  return new URL(step.op_url, docsUrl).toString();
}
