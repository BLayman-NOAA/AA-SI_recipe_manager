/** Helpers for the session's declared input fields, shared by the app and the docs site viewer. */
import { declaredIn, type Graph } from './graph';
import type { InputField, InputSource } from './models';

/** Each declared input's value source, by name. */
export function fieldSources(declared: InputField[]): Record<string, InputSource> {
  return Object.fromEntries(declared.map((field) => [field.name, field.source]));
}

/** The file declaring an input field, or null when the graph does not have the input. */
export function fieldFile(graph: Graph, field: InputField): string | null {
  const node = graph.nodes.find((candidate) => candidate.id === `in:${field.name}`);
  return node?.kind === 'input' ? declaredIn(graph, node) : null;
}

/** A value source as a badge reads it: "run config" for the run config. */
export function sourceLabel(source: string): string {
  return source === 'config' ? 'run config' : source;
}
