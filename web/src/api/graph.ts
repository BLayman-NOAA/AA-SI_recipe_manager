/**
 * The flow diagram document (graph schema 2) built by aa_recipe_manager.graph.
 *
 * The server sends it as an untyped dict, so these types are written by hand
 * to match build_recipe_graph field for field.
 */

export const GRAPH_SCHEMA = 2;

/** Facts about the recipe the graph was built from. */
export interface GraphRecipeInfo {
  name: string;
  description: string | null;
  version: string | null;
  source: string;
  parent: string | null;
  level: number | null;
  cruise: string | null;
}

/** A step input port. */
export interface GraphInputPort {
  name: string;
  type: string | null;
  required: boolean;
  description: string | null;
  value: unknown;
  many: boolean;
  semantic: string | null;
}

/** A step parameter. */
export interface GraphParam {
  name: string;
  type: string | null;
  units: string | null;
  description: string | null;
  default: unknown;
  set: boolean;
  value: unknown;
}

/** A step output port. */
export interface GraphOutputPort {
  name: string;
  type: string | null;
  description: string | null;
  disposed: boolean;
  many: boolean;
  semantic: string | null;
}

/** The implementation a registered op runs by default. */
export interface GraphImplementation {
  key: string;
  callable_path: string;
  dependency: string | null;
}

/** A sweep over parameter lists. */
export interface GraphSweep {
  mode: string;
  params: Record<string, unknown[]>;
}

/** A recipe step. */
export interface StepNode {
  id: string;
  kind: 'step';
  op: string;
  op_url: string | null;
  custom: boolean;
  extends: string | null;
  callable_path: string | null;
  category: string | null;
  implementation: GraphImplementation | null;
  /** Innermost include containing the step, or null for a root step. */
  include: string | null;
  /** File defining the step, relative to the root recipe's folder. */
  document: string;
  hash: string | null;
  editable: boolean;
  description: string | null;
  sink: boolean;
  mapped: boolean;
  group: string | null;
  collector: boolean;
  sweep: GraphSweep | null;
  checkpoint: 'always' | 'never' | 'save' | null;
  regenerate: 'if-missing' | 'always' | 'never' | null;
  inputs: GraphInputPort[];
  params: GraphParam[];
  outputs: GraphOutputPort[];
}

/** A recipe input referenced by a step (id `in:<name>`). */
export interface InputNode {
  id: string;
  kind: 'input';
  label: string;
  type: string | null;
  declared: boolean;
  required: boolean | null;
  default: unknown;
  description: string | null;
  /**
   * The file declaring the input: the root document or an include's source,
   * null for an input no file declares. Absent in older graphs.
   */
  declared_in?: string | null;
}

/** A reference to a step outside the recipe (id `ext:<step>.<output>` or `ext:<dep>`). */
export interface ExternalNode {
  id: string;
  kind: 'external';
  label: string;
  type: string | null;
}

/** A recipe output or an unconsumed step output (id `out:<name>`). */
export interface OutputNode {
  id: string;
  kind: 'output';
  label: string;
  type: string | null;
  declared: boolean;
  from: { step: string; output: string };
}

export type GraphNode = StepNode | InputNode | ExternalNode | OutputNode;
export type GraphNodeKind = GraphNode['kind'];

export type GraphEdgeKind =
  | 'data'
  | 'param'
  | 'map_over'
  | 'item'
  | 'collect'
  | 'depends'
  | 'exit';

/** A wire between two nodes. */
export interface GraphEdge {
  source: string;
  target: string;
  kind: GraphEdgeKind;
  source_output: string | null;
  target_input: string | null;
  label: string;
  type: string | null;
  /** Mapped group id, on the map_over edge that feeds a group. */
  group?: string;
  /** Inputs of the group's first step that read the item. */
  item_inputs?: string[];
}

/** A mapped chain: steps that run once per item of `map_over`. */
export interface GraphGroup {
  id: string;
  kind: 'map';
  map_over: string;
  label: string;
  source: string | null;
  members: string[];
}

/** A step output consumed outside the include that produces it. */
export interface ConsumedOutput {
  step: string;
  output: string;
  by: string[];
}

/** A sub-recipe include block (id `inc:<path>`). */
export interface GraphInclude {
  id: string;
  source: string;
  name: string | null;
  title: string;
  description: string | null;
  /** Smallest other include whose steps contain this one's, or null. */
  parent: string | null;
  /** Index in the recipe's steps of the include's first step. */
  position: number;
  steps: string[];
  overrides: Record<string, unknown>;
  map_over: string | null;
  /**
   * For each override, the step slots it was substituted into (`inputs.<port>`,
   * `params.<name>` or `map_over`), through nested includes to the final steps.
   * Absent in graphs from servers that predate it.
   */
  bindings?: Record<string, IncludeBinding[]>;
  /** The file's own input declarations, in file order. Absent in older graphs. */
  inputs?: GraphIncludeInput[];
  consumed: ConsumedOutput[];
}

/** One input declaration of a sub-recipe file, as written there. */
export interface GraphIncludeInput {
  name: string;
  type: string | null;
  required: boolean;
  default: unknown;
  description: string | null;
}

/** One step slot an include override was substituted into. */
export interface IncludeBinding {
  step: string;
  slot: string;
}

/** The whole flow diagram document. */
export interface Graph {
  schema: number;
  slug: string;
  title: string;
  recipe: GraphRecipeInfo;
  nodes: GraphNode[];
  edges: GraphEdge[];
  groups: GraphGroup[];
  includes: GraphInclude[];
}

/** The step nodes of a graph, in recipe order. */
export function stepNodes(graph: Graph): StepNode[] {
  return graph.nodes.filter((node): node is StepNode => node.kind === 'step');
}

/** One step node by id. */
export function findStep(graph: Graph, id: string): StepNode | undefined {
  return stepNodes(graph).find((node) => node.id === id);
}

/** One include by id. */
export function findInclude(graph: Graph, id: string): GraphInclude | undefined {
  return graph.includes.find((include) => include.id === id);
}

/** A recipe input's name, without the `inputs.` of its label. */
export function inputName(node: InputNode): string {
  return node.label.replace(/^inputs\./, '');
}

/** The root recipe's file name, as the `document` of its steps names it. */
export function rootDocument(graph: Graph): string {
  return graph.recipe.source.split(/[\/]/).pop() || graph.recipe.source;
}

/**
 * The file declaring a recipe input. Older graphs do not say, so every input
 * reads as the root document's.
 */
export function declaredIn(graph: Graph, input: InputNode): string {
  return input.declared_in ?? rootDocument(graph);
}

/** The include whose source is a file, or undefined for the root document. */
export function includeOf(graph: Graph, document: string): GraphInclude | undefined {
  return graph.includes.find((include) => include.source === document);
}

/** Things grouped by the file they come from; `file` is null for those with none. */
export interface DocumentGroup<T> {
  file: string | null;
  items: T[];
}

/**
 * Group items by file: the root document first, then the includes' sources in
 * graph order, then any other file, then items without one. Empty groups are
 * left out and items keep their order within a group.
 */
export function groupByDocument<T>(
  graph: Graph,
  items: T[],
  fileOf: (item: T) => string | null | undefined,
): DocumentGroup<T>[] {
  const files = new Map<string, T[]>();
  for (const file of [rootDocument(graph), ...graph.includes.map((include) => include.source)]) {
    files.set(file, []);
  }
  const loose: T[] = [];
  for (const item of items) {
    const file = fileOf(item);
    if (!file) loose.push(item);
    else files.set(file, [...(files.get(file) ?? []), item]);
  }
  const groups: DocumentGroup<T>[] = [...files]
    .filter(([, list]) => list.length > 0)
    .map(([file, list]) => ({ file, items: list }));
  if (loose.length > 0) groups.push({ file: null, items: loose });
  return groups;
}

/** The inputs a sub-recipe file declares that its include entry does not override. */
export function ownInputs(include: GraphInclude): GraphIncludeInput[] {
  return (include.inputs ?? []).filter((input) => !(input.name in include.overrides));
}

/** The op as the client names it, custom steps included. */
export function opLabel(step: StepNode): string {
  if (!step.custom) return step.op;
  return step.extends ? `custom, extends ${step.extends}` : 'custom step';
}
