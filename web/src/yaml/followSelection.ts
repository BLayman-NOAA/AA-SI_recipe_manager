/** Which recipe file, and which step in it, a YAML pane shows for the diagram's selection. */
import { findInclude, findStep, type Graph } from '../api/graph';
import type { Selection } from '../state/view';

/** The file and step the YAML pane should show for a selection, or null. */
export function yamlTarget(graph: Graph, selected: Selection | null): { path: string; stepId: string | null } | null {
  if (!selected) return null;
  if (selected.kind === 'step') {
    const step = findStep(graph, selected.id);
    return step ? { path: step.document, stepId: step.id } : null;
  }
  if (selected.kind === 'include') {
    const include = findInclude(graph, selected.id);
    return include ? { path: include.source, stepId: null } : null;
  }
  return null;
}

/** The recipe files of a graph: the root document first, then each include's source, once. */
export function graphFiles(graph: Graph): string[] {
  // Step documents of the root file are its name alone (graph.py root_document).
  const files = [graph.recipe.source.split(/[\\/]/).pop() ?? graph.recipe.source];
  for (const include of graph.includes) {
    if (!files.includes(include.source)) files.push(include.source);
  }
  return files;
}
