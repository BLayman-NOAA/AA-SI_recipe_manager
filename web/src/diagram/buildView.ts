/**
 * What the diagram draws: the session graph folded to the current view.
 *
 * Pure. buildView turns the graph, the drill path and the view options into
 * the nodes and edges React Flow renders, in named steps that share a small
 * context. Recipe inputs and step outputs are drawn as boxes whose lines are
 * routed outside the step layout (buses.ts). Node data carries everything the
 * node components need, so they stay presentational.
 */
import {
  findInclude,
  findStep,
  inputName,
  stepNodes,
  type ExternalNode,
  type Graph,
  type GraphEdge,
  type GraphEdgeKind,
  type GraphGroup,
  type GraphInclude,
  type GraphNode,
  type GraphParam,
  type InputNode,
  type StepNode,
} from '../api/graph';
import type { Selection } from '../state/view';
import { plural, showValue, valueText } from '../text';
import { includeChain } from './locate';

export interface ViewOptions {
  drill: string[];
  connected: boolean;
  selected: Selection | null;
  /** Steps whose id or op contain this text are marked as matches. */
  findQuery?: string;
  /** Current recipe input values by name; defaults are shown when absent. */
  inputValues?: Record<string, unknown>;
  /** Step ids showing their parameter list. */
  expandedSteps: Set<string>;
  /** Inputs and outputs box node ids listing their rows. */
  expandedBoxes: Set<string>;
}

export type Badge = 'per item' | 'collects' | 'sweep' | 'checkpoint' | 'writes files' | 'custom';

/** A connection point on a node: the React Flow handle id and its label. */
export interface Port {
  id: string;
  label: string;
  title: string;
}

export interface ParamRow {
  name: string;
  value: string;
  set: boolean;
  type: string;
  description: string | null;
}

export interface StepData {
  step: StepNode;
  badges: Badge[];
  /** The node lists its parameters. */
  expanded: boolean;
  params: ParamRow[];
}

export interface IncludeData {
  include: GraphInclude;
  stepCount: number;
  overrideCount: number;
  inPorts: Port[];
  outPorts: Port[];
  /** The box lists its overrides. */
  expanded: boolean;
  /** One row per override the include entry sets. */
  rows: InputRow[];
}

export interface MapGroupData {
  group: GraphGroup;
}

export interface GhostData {
  stepId: string;
  op: string | null;
  /** Title of the include the step belongs to, or null for a root step. */
  where: string | null;
}

export interface ExternalData {
  external: ExternalNode;
}

/** One recipe input, or one override of the include drilled into. */
export interface InputRow {
  name: string;
  value: unknown;
  /** The value differs from the declared default, or the parent sets it. */
  set: boolean;
  type: string | null;
  description: string | null;
  /** The graph input node, or null for an override. */
  inputId: string | null;
  /** Steps outside the sub-recipe the override value references. */
  from: { step: string; output: string }[];
  /** Ids of the steps that read the value. */
  usedBy: string[];
  /**
   * For an override that passes on an input of the parent, the value that
   * input has, where the parent recipe's inputs make it known.
   */
  resolved?: unknown;
}

export type InputsBoxId = 'inputs' | `overrides:${string}`;

export interface InputsBoxData {
  id: InputsBoxId;
  title: 'Recipe inputs' | 'Inputs from the parent';
  include: GraphInclude | null;
  rows: InputRow[];
  expanded: boolean;
}

export interface OutputRow {
  name: string;
  type: string | null;
  /** A recipe output, an output nothing reads, or one read outside the sub-recipe. */
  kind: 'final' | 'unused' | 'exit';
  /** Steps outside the sub-recipe that read an exit. */
  by: string[];
  outputId: string | null;
}

export interface OutputsBoxData {
  owner: string;
  /** The owner on one line: a step id or a short include title. */
  ownerLabel: string;
  /** The owner in full: a step id or the include title. */
  ownerTitle: string;
  rows: OutputRow[];
  expanded: boolean;
  /** Every row is unused: the box and its line are drawn dotted. */
  muted: boolean;
  /** How many rows of each kind, such as "2 used later, 3 unused". */
  summary: string;
}

interface ViewNodeBase {
  id: string;
  /** Container node (a mapGroup) this node sits in. */
  parentId?: string;
  /** What a click on the node selects, or null. */
  selection: Selection | null;
  dimmed: boolean;
  selected: boolean;
  /** Matches the find query. */
  match: boolean;
}

export type ViewNode = ViewNodeBase &
  (
    | { kind: 'step'; data: StepData }
    | { kind: 'include'; data: IncludeData }
    | { kind: 'mapGroup'; data: MapGroupData }
    | { kind: 'ghost'; data: GhostData }
    | { kind: 'external'; data: ExternalData }
    | { kind: 'inputsBox'; data: InputsBoxData }
    | { kind: 'outputsBox'; data: OutputsBoxData }
  );

export type ViewNodeKind = ViewNode['kind'];

export interface ViewEdge {
  id: string;
  source: string;
  target: string;
  sourceHandle: string;
  targetHandle: string;
  kind: GraphEdgeKind;
  /** The names joined by listLabel. */
  label: string;
  /** Outputs, inputs or parameters the edge carries. */
  names: string[];
  type: string | null;
  dimmed: boolean;
  /** Lines of an inputs box ('in') or into an outputs box ('out'). */
  bus: 'in' | 'out' | null;
  /** A line into an outputs box none of whose outputs is used later. */
  muted: boolean;
}

export interface Crumb {
  label: string;
  drill: string[];
}

export interface View {
  nodes: ViewNode[];
  edges: ViewEdge[];
  breadcrumb: Crumb[];
}

/** Generic handles every node with edges has, used when no port applies. */
export const IN_HANDLE = 'in';
export const OUT_HANDLE = 'out';

/** Handles of the box lines: `bus` on a box, `bus-in` and `bus-out` on other nodes. */
export const BOX_HANDLE = 'bus';
export const BUS_IN_HANDLE = 'bus-in';
export const BUS_OUT_HANDLE = 'bus-out';

export const CONTAINER_KINDS: ReadonlySet<ViewNodeKind> = new Set(['mapGroup']);

const MAX_LABELS = 3;

/** Names joined by ", ", at most three, then "+N more" (docs recipe_links._list_label). */
export function listLabel(names: string[]): string {
  if (names.length <= MAX_LABELS) return names.join(', ');
  return `${names.slice(0, MAX_LABELS).join(', ')} +${names.length - MAX_LABELS} more`;
}

/** Badges a step node shows, in the docs site's order. */
export function stepBadges(step: StepNode): Badge[] {
  const badges: Badge[] = [];
  if (step.mapped) badges.push('per item');
  if (step.collector) badges.push('collects');
  if (step.sweep) badges.push('sweep');
  if (step.checkpoint === 'always' || step.checkpoint === 'save') badges.push('checkpoint');
  if (step.sink) badges.push('writes files');
  if (step.custom) badges.push('custom');
  return badges;
}

/** One row per parameter: the set value, or the default when left unset. */
export function paramRows(step: StepNode): ParamRow[] {
  return step.params.map((param: GraphParam) => ({
    name: param.name,
    value: showValue(param.set ? param.value : param.default),
    set: param.set,
    type: [param.type, param.units && `(${param.units})`].filter(Boolean).join(' '),
    description: param.description,
  }));
}

/** Steps whose id or op contain the query, case insensitive; exact id matches first. */
export function findSteps(graph: Graph, query: string): StepNode[] {
  const text = query.trim().toLowerCase();
  if (!text) return [];
  const found = stepNodes(graph).filter(
    (step) => step.id.toLowerCase().includes(text) || step.op.toLowerCase().includes(text),
  );
  return [
    ...found.filter((step) => step.id.toLowerCase() === text),
    ...found.filter((step) => step.id.toLowerCase() !== text),
  ];
}

function isNumberText(text: string): boolean {
  return text.trim() !== '' && !Number.isNaN(Number(text));
}

/**
 * Whether a step slot receives an include override, for graphs without the
 * include's bindings.
 *
 * Flattening replaces `${inputs.<name>}` with the override value, so the
 * value is matched instead: a reference matches any slot that contains it, a
 * plain string matches an equal slot value, and a number, boolean, list or
 * dict also needs the slot name to end the override name (`noise_ping_num`
 * feeds `ping_num`), since small numbers repeat across unrelated params.
 */
export function overrideFeeds(
  name: string,
  value: unknown,
  slotName: string,
  slotValue: unknown,
): boolean {
  const wanted = showValue(value);
  const actual = showValue(slotValue);
  if (!wanted || !actual) return false;
  if (wanted.includes('${')) return actual.includes(wanted);
  if (wanted !== actual) return false;
  if (typeof value === 'string' && !isNumberText(value)) return true;
  return name.toLowerCase().endsWith(slotName.toLowerCase());
}

function stepTakesOverride(step: StepNode, name: string, value: unknown): boolean {
  return (
    step.inputs.some((port) => overrideFeeds(name, value, port.name, port.value)) ||
    step.params.some((param) => overrideFeeds(name, value, param.name, param.value))
  );
}

const STEP_REF = /\$\{([A-Za-z_]\w*)\.([\w.]+)\}/g;
const INPUT_REF = /\$\{inputs\.(\w+)\}/g;

/** The (step, output) pairs a value references, `${step.output}`. */
function stepRefs(value: unknown): [string, string][] {
  return [...showValue(value).matchAll(STEP_REF)]
    .filter((match) => match[1] !== 'inputs')
    .map((match) => [match[1], match[2]]);
}

/** An edge re-targeted to visible nodes, before duplicates are merged. */
interface Folded {
  source: string;
  target: string;
  sourceHandle: string;
  targetHandle: string;
  kind: GraphEdgeKind;
  label: string;
  type: string | null;
  /** The graph edge's target input, and its target step when it was folded away. */
  targetInput?: string | null;
  foldedTarget?: string;
  /** The source step and output when the source was folded into an include box. */
  foldedSource?: { step: string; output: string };
}

/** A step output read outside the sub-recipe drilled into. */
interface Exit {
  owner: string;
  step: string;
  output: string;
  type: string | null;
  by: string[];
}

/** What every step of buildView shares. */
interface Context {
  graph: Graph;
  options: ViewOptions;
  byId: Map<string, GraphNode>;
  steps: StepNode[];
  /** The drill path, without ids the graph does not have. */
  drill: string[];
  /** The include drilled into, or null for the whole recipe. */
  scope: GraphInclude | null;
  inScope: (stepId: string) => boolean;
  /** The visible node standing for an in scope step. */
  representative: (stepId: string) => string;
  /** Includes between the scope and a step, outermost first. */
  stepChain: (stepId: string) => string[];
}

function makeContext(graph: Graph, options: ViewOptions): Context {
  const drill = options.drill.filter((id) => findInclude(graph, id));
  const scope = drill.length > 0 ? findInclude(graph, drill[drill.length - 1]) ?? null : null;
  const scopeSteps = scope ? new Set(scope.steps) : null;
  const chains = new Map<string, string[]>();
  const stepChain = (stepId: string) => {
    let chain = chains.get(stepId);
    if (!chain) {
      chain = includeChain(graph, stepId);
      if (scope) chain = chain.slice(chain.indexOf(scope.id) + 1);
      chains.set(stepId, chain);
    }
    return chain;
  };
  return {
    graph,
    options,
    byId: new Map(graph.nodes.map((node) => [node.id, node])),
    steps: stepNodes(graph),
    drill,
    scope,
    inScope: (stepId) => !scopeSteps || scopeSteps.has(stepId),
    representative: (stepId) => stepChain(stepId)[0] ?? stepId,
    stepChain,
  };
}

function base(id: string, selection: Selection | null, parentId?: string): ViewNodeBase {
  return {
    id,
    ...(parentId ? { parentId } : {}),
    selection,
    dimmed: false,
    selected: false,
    match: false,
  };
}

/** Rules 1 and 2: in scope steps, and one box per outermost include. */
function placeSteps(ctx: Context): Map<string, ViewNode> {
  const nodes = new Map<string, ViewNode>();
  for (const step of ctx.steps) {
    if (!ctx.inScope(step.id)) continue;
    const rep = ctx.representative(step.id);
    if (rep === step.id) {
      const data = {
        step,
        badges: stepBadges(step),
        expanded: ctx.options.expandedSteps.has(step.id),
        params: paramRows(step),
      };
      nodes.set(step.id, { ...base(step.id, { kind: 'step', id: step.id }), kind: 'step', data });
    } else if (!nodes.has(rep)) {
      const include = findInclude(ctx.graph, rep)!;
      nodes.set(rep, {
        ...base(rep, { kind: 'include', id: rep }),
        kind: 'include',
        data: {
          include,
          stepCount: include.steps.length,
          overrideCount: Object.keys(include.overrides).length,
          inPorts: [],
          outPorts: [],
          expanded: ctx.options.expandedBoxes.has(rep),
          rows: includeOverrideRows(ctx.graph, rep, ctx.options.inputValues),
        },
      });
    }
  }
  return nodes;
}

/** The list a per item edge's target maps over (`raw_urls` of `${query_ncei.raw_urls}`). */
function itemList(graph: Graph, target: GraphNode): string | null {
  if (target.kind !== 'step') return null;
  const group = graph.groups.find((candidate) => candidate.id === target.group);
  return group?.map_over.replace(/^\$\{|\}$/g, '').split('.').pop() ?? null;
}

/**
 * The label of a folded edge. `_item` never shows: a per item edge reads
 * "item of <list>", and a map over edge into a visible step "item of <list> -> x".
 * An edge with an end folded into an include box shows the output passed.
 */
function edgeLabel(graph: Graph, edge: GraphEdge, target: GraphNode, folded: boolean): string {
  const into = edge.item_inputs?.join(', ') || edge.target_input;
  if (edge.kind === 'item') {
    const list = `item of ${itemList(graph, target) ?? 'the list'}`;
    return folded || !into ? list : `${list} -> ${into}`;
  }
  if (folded) return edge.source_output ?? edge.label;
  if (edge.kind === 'map_over') {
    const list = `item of ${edge.source_output ?? 'the list'}`;
    return into ? `${list} -> ${into}` : list;
  }
  return edge.label;
}

interface Folding {
  edges: Folded[];
  ghosts: Map<string, ViewNode>;
  /** Exits by `step.output`. */
  exits: Map<string, Exit>;
  ghostOf: (step: StepNode) => string;
}

/**
 * Rules 3 and 4: re-target every graph edge to visible representatives.
 *
 * Ends on a step keep its port handles; ends folded into an include box get
 * their handles later, from the edges that are drawn (assignPorts). Outputs
 * read outside the scope become exits, rows of the owner's outputs box.
 */
function foldEdges(ctx: Context): Folding {
  const { graph, inScope, representative } = ctx;
  const ghosts = new Map<string, ViewNode>();
  const exits = new Map<string, Exit>();
  const edges: Folded[] = [];

  const ghostOf = (step: StepNode) => {
    const id = `ghost:${step.id}`;
    if (!ghosts.has(id)) {
      const where = step.include ? findInclude(graph, step.include)?.title ?? null : null;
      ghosts.set(id, {
        ...base(id, { kind: 'step', id: step.id }),
        kind: 'ghost',
        data: { stepId: step.id, op: step.op, where },
      });
    }
    return id;
  };

  /** The visible source, its handle, and the step output when it was folded. */
  const sourceEnd = (
    edge: GraphEdge,
    node: GraphNode,
    target: GraphNode,
  ): { from: string; handle: string; foldedSource?: { step: string; output: string } } => {
    if (node.kind !== 'step') return { from: node.id, handle: OUT_HANDLE };
    if (!inScope(node.id)) return { from: ghostOf(node), handle: OUT_HANDLE };
    const from = representative(node.id);
    const output = edge.kind === 'item' ? itemList(graph, target) : edge.source_output;
    if (from !== node.id) {
      return { from, handle: OUT_HANDLE, ...(output ? { foldedSource: { step: node.id, output } } : {}) };
    }
    const known = output !== null && node.outputs.some((port) => port.name === output);
    return { from, handle: known ? `out:${output}` : OUT_HANDLE };
  };

  const targetEnd = (edge: GraphEdge, node: GraphNode): [string, string] => {
    if (node.kind !== 'step') return [node.id, IN_HANDLE];
    const rep = representative(node.id);
    const input = edge.target_input;
    const known = rep === node.id && input !== null && node.inputs.some((port) => port.name === input);
    return [rep, known ? `in:${input}` : IN_HANDLE];
  };

  const stepEnd = (node: GraphNode) => (node.kind === 'step' ? representative(node.id) : node.id);

  for (const edge of graph.edges) {
    const source = ctx.byId.get(edge.source);
    const target = ctx.byId.get(edge.target);
    if (!source || !target) continue;
    const sourceIn = source.kind !== 'step' || inScope(source.id);
    const targetIn = target.kind === 'step' ? inScope(target.id) : true;
    if (target.kind === 'output' && source.kind === 'step' && !inScope(source.id)) continue;
    if (!sourceIn && !targetIn) continue;
    if (source.kind !== 'step' && !targetIn) continue;

    if (sourceIn && !targetIn && source.kind === 'step') {
      // A per item edge passes the mapped list, never the _item placeholder.
      const output = (edge.kind === 'item' ? itemList(graph, target) : edge.source_output) ?? '';
      const key = `${source.id}.${output}`;
      const exit = exits.get(key) ?? {
        owner: representative(source.id),
        step: source.id,
        output,
        type: edge.type,
        by: [],
      };
      if (!exit.by.includes(target.id)) exit.by.push(target.id);
      exits.set(key, exit);
      continue;
    }

    if (sourceIn && stepEnd(source) === stepEnd(target)) continue;
    const end = sourceEnd(edge, source, target);
    const [to, targetHandle] = targetEnd(edge, target);
    const targetFolded = to !== target.id;
    edges.push({
      source: end.from,
      target: to,
      sourceHandle: end.handle,
      targetHandle,
      kind: edge.kind,
      label: edgeLabel(graph, edge, target, Boolean(end.foldedSource) || targetFolded),
      type: edge.type,
      targetInput: edge.target_input,
      ...(targetFolded ? { foldedTarget: target.id } : {}),
      ...(end.foldedSource ? { foldedSource: end.foldedSource } : {}),
    });
  }
  for (const exit of exits.values()) exit.by.sort();
  return { edges, ghosts, exits, ghostOf };
}

/**
 * Ports of collapsed include boxes, from the edges that are drawn (FR-11).
 *
 * Outputs are the include's consumed outputs only. Inbound edges carrying the
 * same input name from the same source share one port. A port is labelled
 * `step.name` when the name alone repeats within the box.
 */
function assignPorts(nodes: Map<string, ViewNode>, edges: Folded[]) {
  const box = (id: string) => {
    const node = nodes.get(id);
    return node?.kind === 'include' ? node : null;
  };
  for (const edge of edges) {
    const from = box(edge.source);
    const out = edge.foldedSource;
    const consumed = from?.data.include.consumed ?? [];
    if (from && out && consumed.some((item) => item.step === out.step && item.output === out.output)) {
      const id = `out:${out.step}.${out.output}`;
      if (!from.data.outPorts.some((port) => port.id === id)) {
        from.data.outPorts.push({ id, label: out.output, title: `${out.step}.${out.output}` });
      }
      edge.sourceHandle = id;
    }
    const to = box(edge.target);
    if (to && edge.foldedTarget && edge.targetInput) {
      const id = `in:${edge.targetInput}@${edge.source}`;
      const title = `${edge.foldedTarget}.${edge.targetInput}`;
      const port = to.data.inPorts.find((existing) => existing.id === id);
      if (!port) to.data.inPorts.push({ id, label: edge.targetInput, title });
      else if (!port.title.split(', ').includes(title)) port.title += `, ${title}`;
      edge.targetHandle = id;
    }
  }
  for (const node of nodes.values()) {
    if (node.kind !== 'include') continue;
    relabelRepeats(node.data.outPorts, (port) => port.title);
    relabelRepeats(node.data.inPorts, (port) => `${port.label} from ${sourceName(port.id.split('@')[1])}`);
  }
}

/** Give ports whose labels repeat within a box a longer label. */
function relabelRepeats(ports: Port[], longer: (port: Port) => string) {
  const counts = new Map<string, number>();
  for (const port of ports) counts.set(port.label, (counts.get(port.label) ?? 0) + 1);
  for (const port of ports) {
    if ((counts.get(port.label) ?? 0) > 1) port.label = longer(port);
  }
}

/** A short name for an edge source: the step, ghost or sub-recipe file name. */
function sourceName(id: string): string {
  if (id.startsWith('inc:')) return id.slice(4).split('/').pop()!.replace(/\.ya?ml$/, '');
  return id.replace(/^(ext|ghost):/, '');
}

function inputValue(input: InputNode, options: ViewOptions): unknown {
  const name = inputName(input);
  const values = options.inputValues;
  return values && name in values ? values[name] : input.default;
}

/** A row for a recipe input; a map over input reads "item of <input>". */
function recipeInputRow(input: InputNode, options: ViewOptions, mapped: boolean): InputRow {
  const value = inputValue(input, options);
  return {
    name: mapped ? `item of ${inputName(input)}` : inputName(input),
    value,
    set: JSON.stringify(value) !== JSON.stringify(input.default),
    type: input.type,
    description: input.description,
    inputId: input.id,
    from: [],
    usedBy: [],
  };
}

/** The value an input of an include's parent has, or undefined when it is not known. */
function parentInput(
  graph: Graph,
  include: GraphInclude,
  name: string,
  inputValues?: Record<string, unknown>,
): unknown {
  const parent = include.parent ? findInclude(graph, include.parent) : null;
  if (parent) {
    if (!(name in parent.overrides)) return undefined;
    const value = parent.overrides[name];
    const resolved = resolveInputRefs(graph, parent, value, inputValues);
    if (resolved !== undefined) return resolved;
    return showValue(value).includes('${') ? undefined : value;
  }
  if (inputValues && name in inputValues) return inputValues[name];
  const input = graph.nodes.find((node) => node.id === `in:${name}`);
  return input?.kind === 'input' ? input.default : undefined;
}

/**
 * An override value with the parent's inputs it passes on replaced by their
 * values, or undefined when it passes none or one is not known.
 */
function resolveInputRefs(
  graph: Graph,
  include: GraphInclude,
  value: unknown,
  inputValues?: Record<string, unknown>,
): unknown {
  if (typeof value !== 'string' || !value.includes('${inputs.')) return undefined;
  const whole = /^\$\{inputs\.(\w+)\}$/.exec(value);
  if (whole) return parentInput(graph, include, whole[1], inputValues);
  let known = true;
  const text = value.replace(INPUT_REF, (_ref, name: string) => {
    const resolved = parentInput(graph, include, name, inputValues);
    if (resolved === undefined) known = false;
    return showValue(resolved);
  });
  return known ? text : undefined;
}

/**
 * One row per override an include entry sets: its value, the value of the
 * parent input it passes on (when known), the steps outside the include it
 * references and the member steps that take it.
 *
 * The steps come from the include's bindings, where the loader recorded each
 * slot an override was substituted into; graphs without bindings fall back to
 * matching values (overrideFeeds).
 */
export function includeOverrideRows(
  graph: Graph,
  includeId: string,
  inputValues?: Record<string, unknown>,
): InputRow[] {
  const include = findInclude(graph, includeId);
  if (!include) return [];
  const members = new Set(include.steps);
  const steps = stepNodes(graph).filter((step) => members.has(step.id));
  const usedBy = (name: string, value: unknown) => {
    const bound = include.bindings?.[name];
    if (!bound) return steps.filter((step) => stepTakesOverride(step, name, value)).map((step) => step.id);
    return steps.filter((step) => bound.some((binding) => binding.step === step.id)).map((step) => step.id);
  };
  return Object.entries(include.overrides).map(([name, value]) => {
    const resolved = resolveInputRefs(graph, include, value, inputValues);
    return {
      name,
      value,
      set: true,
      type: null,
      description: null,
      inputId: null,
      from: stepRefs(value)
        .filter(([step]) => !members.has(step) && findStep(graph, step))
        .map(([step, output]) => ({ step, output })),
      usedBy: usedBy(name, value),
      ...(resolved === undefined ? {} : { resolved }),
    };
  });
}

/**
 * A row's value as text: an override that passes on a known parent input reads
 * `${inputs.x} = value`. An empty override reads "none", an empty recipe input "unset".
 */
export function rowValue(row: InputRow): string {
  const text = showValue(row.value) || (row.inputId ? 'unset' : 'none');
  return row.resolved === undefined ? text : `${text} = ${valueText(row.resolved)}`;
}

/** Add a name to the line into a target, keeping first seen order. */
function addName(lines: Map<string, string[]>, target: string, name: string) {
  const names = lines.get(target) ?? [];
  if (!names.includes(name)) names.push(name);
  lines.set(target, names);
}

function busEdge(
  source: string,
  target: string,
  handles: [string, string],
  kind: GraphEdgeKind,
  names: string[],
  bus: 'in' | 'out',
  label = listLabel(names),
): ViewEdge {
  return {
    id: `e:${source}->${target}`,
    source,
    target,
    sourceHandle: handles[0],
    targetHandle: handles[1],
    kind,
    label,
    names,
    type: null,
    dimmed: false,
    bus,
    muted: false,
  };
}

/**
 * Rule 4: one inputs box, with one line per target naming what it sends.
 *
 * At the root it is "Recipe inputs", with a row per recipe input. Drilled in,
 * it is "Inputs from the parent", with a row per override of the include, and
 * recipe input lines join the override that passes the input on. An outside
 * step an override references stays a ghost with a line into the box, unless
 * that ghost is already wired straight to a step the override feeds.
 */
function boxInputs(ctx: Context, folding: Folding): { kept: Folded[]; nodes: ViewNode[]; edges: ViewEdge[] } {
  const { scope, options } = ctx;
  const id: InputsBoxId = scope ? `overrides:${scope.id}` : 'inputs';
  const rows = new Map<string, InputRow>();
  const lines = new Map<string, string[]>();
  const feeds = new Map<string, string[]>();
  const kept: Folded[] = [];
  const direct = new Set(folding.edges.map((edge) => `${edge.source}>${edge.target}`));
  const use = (row: InputRow, stepId: string) => {
    if (!row.usedBy.includes(stepId)) row.usedBy.push(stepId);
  };

  for (const row of scope ? includeOverrideRows(ctx.graph, scope.id, options.inputValues) : []) {
    rows.set(row.name, row);
    const fed = new Set(row.usedBy.map((stepId) => ctx.representative(stepId)));
    for (const rep of fed) addName(lines, rep, row.name);
    for (const { step, output } of row.from) {
      const ghost = folding.ghostOf(findStep(ctx.graph, step)!);
      if (![...fed].some((rep) => direct.has(`${ghost}>${rep}`))) addName(feeds, ghost, output);
    }
  }

  /** Override rows by the recipe inputs their values pass on. */
  const passedOn = new Map<string, InputRow[]>();
  for (const row of rows.values()) {
    for (const [, input] of showValue(row.value).matchAll(INPUT_REF)) {
      passedOn.set(input, [...(passedOn.get(input) ?? []), row]);
    }
  }

  for (const edge of folding.edges) {
    const source = ctx.byId.get(edge.source);
    if (source?.kind !== 'input') {
      kept.push(edge);
      continue;
    }
    const stepId = edge.foldedTarget ?? edge.target;
    const passing = passedOn.get(inputName(source)) ?? [];
    if (passing.length > 0) {
      for (const row of passing) {
        addName(lines, edge.target, row.name);
        use(row, stepId);
      }
      continue;
    }
    const mapped = edge.kind === 'map_over';
    const key = `${source.id}${mapped ? '#item' : ''}`;
    const row = rows.get(key) ?? recipeInputRow(source, options, mapped);
    rows.set(key, row);
    addName(lines, edge.target, row.name);
    use(row, stepId);
  }

  if (!scope && rows.size === 0) return { kept, nodes: [], edges: [] };
  const order = new Map(ctx.graph.nodes.map((node, index) => [node.id, index]));
  const sorted = [...rows.values()];
  if (!scope) sorted.sort((a, b) => order.get(a.inputId!)! - order.get(b.inputId!)!);
  const box: ViewNode = {
    ...base(id, { kind: 'inputsBox', id }),
    kind: 'inputsBox',
    data: {
      id,
      title: scope ? 'Inputs from the parent' : 'Recipe inputs',
      include: scope,
      rows: sorted,
      expanded: options.expandedBoxes.has(id),
    },
  };
  const edges = [
    ...[...feeds].map(([ghost, names]) => busEdge(ghost, id, [BUS_OUT_HANDLE, BOX_HANDLE], 'data', names, 'in')),
    ...[...lines].map(([target, names]) => busEdge(id, target, [BOX_HANDLE, BUS_IN_HANDLE], 'param', names, 'in')),
  ];
  return { kept, nodes: [box], edges };
}

/** The label of an output box owner: a step id or an include title. */
function ownerLabel(ctx: Context, owner: string): string {
  const include = findInclude(ctx.graph, owner);
  return include ? shortTitle(ctx.graph, include.title) : owner;
}

/**
 * An include title without the cruise prefix it shares with the recipe name
 * (`processing_lvl_4` for `hb1603_processing_lvl_4` in `hb1603_dive_analysis`).
 */
export function shortTitle(graph: Graph, title: string): string {
  const prefix = (graph.recipe.cruise ?? graph.recipe.name.split('_')[0]).toLowerCase();
  if (!/^[a-z]+\d+$/.test(prefix)) return title;
  const strip = `${prefix}_`;
  return title.toLowerCase().startsWith(strip) && title.length > strip.length ? title.slice(strip.length) : title;
}

const KIND_ORDER: Record<OutputRow['kind'], number> = { exit: 0, final: 1, unused: 2 };

/** How many rows of each kind a box lists, such as "2 used later, 3 unused". */
function outputSummary(rows: OutputRow[]): string {
  const count = { exit: 0, final: 0, unused: 0 };
  for (const row of rows) count[row.kind] += 1;
  const parts = [
    count.exit > 0 ? `${count.exit} used later` : '',
    count.final > 0 ? plural(count.final, 'recipe output') : '',
    count.unused > 0 ? `${count.unused} unused` : '',
  ];
  return parts.filter(Boolean).join(', ');
}

/** An output name without the step prefix a row in an include's box carries. */
function outputName(row: OutputRow): string {
  return row.name.slice(row.name.lastIndexOf('.') + 1);
}

/**
 * Rule 5: an outputs box per producer whose outputs leave the view.
 *
 * Outputs read inside the view are core lines only. At the top level a box
 * lists the recipe's declared outputs. Drilled in, it lists the outputs read
 * outside the sub-recipe and the declared ones, and for a step also the ones
 * nothing reads. A box whose rows are all unused is muted, and so is its line.
 */
function boxOutputs(ctx: Context, folded: Folded[], exits: Map<string, Exit>): {
  kept: Folded[];
  nodes: ViewNode[];
  edges: ViewEdge[];
} {
  const rowsByOwner = new Map<string, OutputRow[]>();
  const add = (owner: string, row: OutputRow) => {
    const rows = rowsByOwner.get(owner) ?? [];
    rows.push(row);
    rowsByOwner.set(owner, rows);
  };
  const kept: Folded[] = [];
  for (const edge of folded) {
    const target = ctx.byId.get(edge.target);
    if (target?.kind !== 'output') {
      kept.push(edge);
      continue;
    }
    const showsUnused = ctx.scope !== null && !findInclude(ctx.graph, edge.source);
    if (!target.declared && !showsUnused) continue;
    const { step, output } = target.from;
    add(edge.source, {
      name: edge.source === step ? output : `${step}.${output}`,
      type: target.type,
      kind: target.declared ? 'final' : 'unused',
      by: [],
      outputId: target.id,
    });
  }
  for (const exit of exits.values()) {
    add(exit.owner, {
      name: exit.owner === exit.step ? exit.output : `${exit.step}.${exit.output}`,
      type: exit.type,
      kind: 'exit',
      by: exit.by,
      outputId: null,
    });
  }

  const nodes: ViewNode[] = [];
  const edges: ViewEdge[] = [];
  for (const [owner, unsorted] of rowsByOwner) {
    const id = `cout:${owner}`;
    const rows = [...unsorted].sort((a, b) => KIND_ORDER[a.kind] - KIND_ORDER[b.kind]);
    const muted = rows.every((row) => row.kind === 'unused');
    nodes.push({
      ...base(id, { kind: 'outputsBox', id: owner }),
      kind: 'outputsBox',
      data: {
        owner,
        ownerLabel: ownerLabel(ctx, owner),
        ownerTitle: findInclude(ctx.graph, owner)?.title ?? owner,
        rows,
        expanded: ctx.options.expandedBoxes.has(id),
        muted,
        summary: outputSummary(rows),
      },
    });
    const labelled = muted ? rows : rows.filter((row) => row.kind !== 'unused');
    const label = listLabel([...new Set(labelled.map(outputName))]);
    const names = rows.map((row) => row.name);
    edges.push({ ...busEdge(owner, id, [BUS_OUT_HANDLE, BOX_HANDLE], 'exit', names, 'out', label), muted });
  }
  return { kept, nodes, edges };
}

/** Rule 4: edges whose ends folded to the same pair become one, names joined. */
function mergeEdges(kept: Folded[]): ViewEdge[] {
  const merged = new Map<string, ViewEdge>();
  for (const edge of kept) {
    const id = `e:${edge.source}->${edge.target}`;
    const existing = merged.get(id);
    if (!existing) {
      merged.set(id, {
        id,
        source: edge.source,
        target: edge.target,
        sourceHandle: edge.sourceHandle,
        targetHandle: edge.targetHandle,
        kind: edge.kind,
        label: '',
        names: edge.label ? [edge.label] : [],
        type: edge.type,
        dimmed: false,
        bus: null,
        muted: false,
      });
      continue;
    }
    if (existing.sourceHandle !== edge.sourceHandle) existing.sourceHandle = OUT_HANDLE;
    if (existing.targetHandle !== edge.targetHandle) existing.targetHandle = IN_HANDLE;
    if (existing.kind !== edge.kind) existing.kind = 'data';
    if (existing.type !== edge.type) existing.type = null;
    if (edge.label && !existing.names.includes(edge.label)) existing.names.push(edge.label);
  }
  return [...merged.values()].map((edge) => ({ ...edge, label: listLabel(edge.names) }));
}

/** References to steps in other files that still have an edge. */
function externalNodes(ctx: Context, edges: ViewEdge[]): ViewNode[] {
  const used = new Set(edges.map((edge) => edge.source));
  return ctx.graph.nodes
    .filter((node): node is ExternalNode => node.kind === 'external' && used.has(node.id))
    .map((node) => ({
      ...base(node.id, { kind: 'input', id: node.id }),
      kind: 'external' as const,
      data: { external: node },
    }));
}

/** Rule 6: a mapped group is drawn when all its members are visible steps. */
function drawGroups(ctx: Context, nodes: Map<string, ViewNode>) {
  for (const group of ctx.graph.groups) {
    const members = group.members.map((id) => nodes.get(id));
    if (members.length === 0 || members.some((node) => node?.kind !== 'step')) continue;
    nodes.set(group.id, { ...base(group.id, null), kind: 'mapGroup', data: { group } });
    for (const node of members) node!.parentId = group.id;
  }
}

function sameSelection(a: Selection | null, b: Selection): boolean {
  return a !== null && a.kind === b.kind && a.id === b.id;
}

/** The node a selection stands for in this view, or null. */
function focusNode(ctx: Context, selected: Selection, ids: Set<string>): string | null {
  switch (selected.kind) {
    case 'step':
      if (ids.has(`ghost:${selected.id}`)) return `ghost:${selected.id}`;
      return ctx.inScope(selected.id) ? ctx.representative(selected.id) : null;
    case 'outputsBox':
      return `cout:${selected.id}`;
    default:
      return selected.id;
  }
}

/** Rule 7: the selected node, and in connected mode everything not next to it dimmed. */
function markSelection(ctx: Context, nodes: ViewNode[], edges: ViewEdge[]) {
  const { selected, connected } = ctx.options;
  if (!selected) return;
  for (const node of nodes) node.selected = sameSelection(node.selection, selected);
  if (!connected) return;

  const ids = new Set(nodes.map((node) => node.id));
  const focus = focusNode(ctx, selected, ids);
  if (!focus || !ids.has(focus)) return;
  const near = new Set<string>([focus]);
  for (const edge of edges) {
    if (edge.source === focus) near.add(edge.target);
    if (edge.target === focus) near.add(edge.source);
  }
  for (const node of nodes) {
    node.dimmed = !CONTAINER_KINDS.has(node.kind) && !near.has(node.id);
  }
  for (const edge of edges) {
    edge.dimmed = edge.source !== focus && edge.target !== focus;
  }
}

function markMatches(ctx: Context, nodes: ViewNode[]) {
  const found = new Set(findSteps(ctx.graph, ctx.options.findQuery ?? '').map((step) => step.id));
  if (found.size === 0) return;
  for (const node of nodes) {
    if (node.kind === 'step') node.match = found.has(node.id);
    if (node.kind === 'include') node.match = node.data.include.steps.some((id) => found.has(id));
  }
}

/** React Flow needs every container before its children. */
function sortParentsFirst(nodes: ViewNode[]): ViewNode[] {
  return [...nodes.filter((node) => !node.parentId), ...nodes.filter((node) => node.parentId)];
}

/** Rule 8: the recipe, then one crumb per drill level. */
function breadcrumb(ctx: Context): Crumb[] {
  const { graph, drill } = ctx;
  const crumbs: Crumb[] = [{ label: graph.recipe.name || graph.title, drill: [] }];
  drill.forEach((id, index) => {
    crumbs.push({ label: findInclude(graph, id)?.title ?? id, drill: drill.slice(0, index + 1) });
  });
  return crumbs;
}

/**
 * Fold the session graph to what the diagram shows.
 *
 * Args:
 *   graph: The session graph (schema 2).
 *   options: Drill path, expanded steps and boxes, connected mode, selection.
 *
 * Returns:
 *   Nodes (containers before their children), edges and the breadcrumb.
 */
export function buildView(graph: Graph, options: ViewOptions): View {
  const ctx = makeContext(graph, options);
  const nodes = placeSteps(ctx);
  const folding = foldEdges(ctx);
  const inputs = boxInputs(ctx, folding);
  const outputs = boxOutputs(ctx, inputs.kept, folding.exits);
  assignPorts(nodes, outputs.kept);
  const core = mergeEdges(outputs.kept);
  const externals = externalNodes(ctx, core);
  drawGroups(ctx, nodes);

  const all = [...folding.ghosts.values(), ...externals, ...nodes.values(), ...inputs.nodes, ...outputs.nodes];
  const edges = [...inputs.edges, ...core, ...outputs.edges];
  markSelection(ctx, all, edges);
  markMatches(ctx, all);
  return { nodes: sortParentsFirst(all), edges, breadcrumb: breadcrumb(ctx) };
}

function boxView(graph: Graph, drill: string[], inputValues?: Record<string, unknown>): View {
  return buildView(graph, {
    drill,
    connected: false,
    selected: null,
    inputValues,
    expandedSteps: new Set(),
    expandedBoxes: new Set(),
  });
}

/** The rows of the inputs box a drill path shows, or none. */
export function inputsBoxRows(graph: Graph, drill: string[], inputValues?: Record<string, unknown>): InputRow[] {
  const box = boxView(graph, drill, inputValues).nodes.find((node) => node.kind === 'inputsBox');
  return box?.kind === 'inputsBox' ? box.data.rows : [];
}

/** The rows of one producer's outputs box in a drill path, or none. */
export function outputsBoxRows(graph: Graph, drill: string[], owner: string): OutputRow[] {
  const box = boxView(graph, drill).nodes.find((node) => node.id === `cout:${owner}`);
  return box?.kind === 'outputsBox' ? box.data.rows : [];
}

/** Where one step output goes: the steps that read it, and whether it is a recipe output. */
export interface OutputStatus {
  step: string;
  name: string;
  type: string | null;
  /** In the recipe's outputs block. */
  final: boolean;
  /** Ids of the steps that read it, sorted. */
  by: string[];
}

/** The status of every output of a step, in port order. */
export function stepOutputRows(graph: Graph, stepId: string): OutputStatus[] {
  const step = findStep(graph, stepId);
  if (!step) return [];
  const byId = new Map(graph.nodes.map((node) => [node.id, node]));
  const statuses = new Map<string, OutputStatus>(
    step.outputs.map((port) => [port.name, { step: stepId, name: port.name, type: port.type, final: false, by: [] }]),
  );
  for (const edge of graph.edges) {
    const target = edge.source === stepId ? byId.get(edge.target) : undefined;
    if (!target) continue;
    const name = edge.kind === 'item' ? itemList(graph, target) : edge.source_output;
    const status = name === null ? undefined : statuses.get(name);
    if (!status) continue;
    if (target.kind === 'output') status.final ||= target.declared;
    else if (target.kind === 'step' && !status.by.includes(target.id)) status.by.push(target.id);
  }
  for (const status of statuses.values()) status.by.sort();
  return [...statuses.values()];
}

/** The status of one step output, or undefined when the step has no such output. */
export function outputStatus(graph: Graph, stepId: string, output: string): OutputStatus | undefined {
  return stepOutputRows(graph, stepId).find((status) => status.name === output);
}

/** An output row of a sub-recipe, with the member step that produces it. */
export interface IncludeOutputRow extends OutputRow {
  step: string;
  /** Also a recipe output, whatever else reads it. */
  final: boolean;
}

/**
 * The outputs of a sub-recipe's steps that matter outside it.
 *
 * Returns:
 *   Rows for the outputs read outside the include, the recipe outputs and the
 *   outputs nothing reads, in step order, and how many outputs are read only
 *   inside the include.
 */
export function includeOutputRows(graph: Graph, includeId: string): { rows: IncludeOutputRow[]; inside: number } {
  const include = findInclude(graph, includeId);
  const rows: IncludeOutputRow[] = [];
  let inside = 0;
  const members = new Set(include?.steps ?? []);
  for (const stepId of include?.steps ?? []) {
    for (const status of stepOutputRows(graph, stepId)) {
      const by = status.by.filter((id) => !members.has(id));
      const kind = by.length > 0 ? 'exit' : status.final ? 'final' : status.by.length === 0 ? 'unused' : null;
      if (!kind) {
        inside += 1;
        continue;
      }
      const { name, type, final } = status;
      rows.push({ step: stepId, name, type, kind, by, outputId: null, final });
    }
  }
  return { rows, inside };
}
