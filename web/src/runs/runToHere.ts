/** The FR-57 hint: the nearest step downstream that the recipe checkpoints. Pure. */
import { findStep, stepNodes, type Graph, type GraphEdgeKind } from '../api/graph';

/** Edges a step's result flows along to later steps. */
const FLOW_KINDS: ReadonlySet<GraphEdgeKind> = new Set(['data', 'map_over', 'item', 'collect']);

/** A step and every step it depends on, through any step to step edge. */
export function ancestry(graph: Graph, stepId: string): Set<string> {
  const steps = new Set(stepNodes(graph).map((step) => step.id));
  const parents = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (!steps.has(edge.source) || !steps.has(edge.target)) continue;
    parents.set(edge.target, [...(parents.get(edge.target) ?? []), edge.source]);
  }
  const seen = new Set<string>([stepId]);
  const queue = [stepId];
  while (queue.length > 0) {
    for (const parent of parents.get(queue.shift()!) ?? []) {
      if (!seen.has(parent)) {
        seen.add(parent);
        queue.push(parent);
      }
    }
  }
  return seen;
}

export interface CheckpointedDownstream {
  step: string;
  /** Steps a run to `step` runs beyond a run to the start, in recipe order. */
  extraSteps: string[];
}

/** The nearest step downstream of a step with `checkpoint: always`, or null. */
export function nearestCheckpointedDownstream(graph: Graph, stepId: string): CheckpointedDownstream | null {
  const children = new Map<string, string[]>();
  for (const edge of graph.edges) {
    if (!FLOW_KINDS.has(edge.kind)) continue;
    children.set(edge.source, [...(children.get(edge.source) ?? []), edge.target]);
  }
  const seen = new Set<string>([stepId]);
  const queue = [stepId];
  while (queue.length > 0) {
    for (const child of children.get(queue.shift()!) ?? []) {
      if (seen.has(child)) continue;
      seen.add(child);
      const step = findStep(graph, child);
      if (!step) continue;
      if (step.checkpoint === 'always') {
        const before = ancestry(graph, stepId);
        const after = ancestry(graph, child);
        const extraSteps = stepNodes(graph)
          .map((node) => node.id)
          .filter((id) => after.has(id) && !before.has(id));
        return { step: child, extraSteps };
      }
      queue.push(child);
    }
  }
  return null;
}
