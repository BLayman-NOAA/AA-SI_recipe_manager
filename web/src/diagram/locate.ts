/** Find the drill path that shows a step or an include, and select steps through it. */
import { findInclude, findStep, type Graph } from '../api/graph';
import type { ViewStore } from '../state/view';

/** An include and the includes around it, outermost first; empty for null. */
function parentChain(graph: Graph, includeId: string | null): string[] {
  const chain: string[] = [];
  let id = includeId;
  while (id && !chain.includes(id) && findInclude(graph, id)) {
    chain.unshift(id);
    id = findInclude(graph, id)?.parent ?? null;
  }
  return chain;
}

function innermostInclude(graph: Graph, stepId: string): string | null {
  let best: { id: string; size: number } | null = null;
  for (const include of graph.includes) {
    if (include.steps.includes(stepId) && (!best || include.steps.length < best.size)) {
      best = { id: include.id, size: include.steps.length };
    }
  }
  return best?.id ?? null;
}

/**
 * Include ids containing a step, outermost first.
 *
 * This is also the drill path that shows the step as a step node.
 */
export function includeChain(graph: Graph, stepId: string): string[] {
  return parentChain(graph, findStep(graph, stepId)?.include ?? innermostInclude(graph, stepId));
}

/** The drill path into an include: the includes around it, outermost first, then itself. */
export function includePath(graph: Graph, includeId: string): string[] {
  return parentChain(graph, includeId);
}

/** Select a step in a view store, drilling to the sub-recipe that shows it. */
export function selectStep(store: ViewStore, graph: Graph | null | undefined, stepId: string): void {
  const view = store.getState();
  if (graph) {
    const drill = includeChain(graph, stepId);
    if (drill.join('>') !== view.drill.join('>')) view.setDrill(drill);
  }
  view.select({ kind: 'step', id: stepId });
}
