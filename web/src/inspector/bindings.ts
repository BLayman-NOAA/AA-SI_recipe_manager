/**
 * How a step parameter is bound to an override or a recipe input, where its
 * value is set, and the steps that value feeds.
 */
import {
  declaredIn,
  findInclude,
  findStep,
  includeOf,
  inputName,
  rootDocument,
  stepNodes,
  type Graph,
  type GraphInclude,
  type InputNode,
  type StepNode,
} from '../api/graph';
import type { InputSource } from '../api/models';
import { includeOverrideRows, overridingAncestor, readers } from '../diagram/buildView';
import { setIncludeOverride, setRecipeInput, type Operation } from '../editing/operations';
import { plural, showValue } from '../text';

/**
 * Where a bound parameter's value comes from.
 *
 * An override binding is an include override the loader substituted into the
 * step; when that override only passes on an input of the file above
 * (`${inputs.y}`), `passesOn` is what gives that input its value. An input
 * binding is a `${inputs.x}` the step reads from the flattened recipe.
 */
export type Binding =
  | { kind: 'override'; include: GraphInclude; name: string; passesOn: Binding | null }
  | {
      kind: 'input';
      name: string;
      declaredIn: string;
      source: InputSource;
      default: unknown;
    };

/**
 * The place that gives a bound parameter its value, which an edit of the
 * parameter changes: an include override, an input's declared default, the
 * session's value or the run config file's value. `value` is what it holds.
 */
export type BindingTarget =
  | { kind: 'override'; include: GraphInclude; name: string; value: unknown }
  | { kind: 'default' | 'session' | 'config'; name: string; declaredIn: string; value: unknown };

const PASS_ON = /^\$\{inputs\.(\w+)\}$/;

/** The file holding an include's entry: its parent's source, or the root file. */
export function includerFile(graph: Graph, include: GraphInclude): string {
  const parent = include.parent ? findInclude(graph, include.parent) : undefined;
  return parent ? parent.source : rootDocument(graph);
}

function inputBinding(graph: Graph, node: InputNode, sources: Record<string, InputSource>): Binding {
  const name = inputName(node);
  const file = declaredIn(graph, node);
  const source = sources[name] ?? 'default';
  return { kind: 'input', name, declaredIn: file, source, default: node.default };
}

/** An override binding, following an override that passes on an input of the file above. */
function overrideBinding(
  graph: Graph,
  include: GraphInclude,
  name: string,
  sources: Record<string, InputSource>,
): Binding {
  const value = include.overrides[name];
  const passed = typeof value === 'string' ? PASS_ON.exec(value) : null;
  let passesOn: Binding | null = null;
  if (passed) {
    const setter = overridingAncestor(graph, include, passed[1]);
    const node = graph.nodes.find((candidate) => candidate.id === `in:${passed[1]}`);
    if (setter) passesOn = overrideBinding(graph, setter, passed[1], sources);
    else if (node?.kind === 'input' && node.declared) passesOn = inputBinding(graph, node, sources);
  }
  return { kind: 'override', include, name, passesOn };
}

/**
 * How a step's parameter is bound, or null when it holds its own value.
 *
 * The nearest include whose bindings list the slot `params.<name>` gives an
 * override binding; else a `param` edge from a declared recipe input gives
 * an input binding.
 */
export function paramBinding(
  graph: Graph,
  step: StepNode,
  name: string,
  sources: Record<string, InputSource> = {},
): Binding | null {
  const slot = `params.${name}`;
  for (let include = step.include ? findInclude(graph, step.include) : undefined; include; ) {
    const bound = Object.entries(include.bindings ?? {}).find(([, slots]) =>
      slots.some((binding) => binding.step === step.id && binding.slot === slot),
    );
    if (bound) return overrideBinding(graph, include, bound[0], sources);
    include = include.parent ? findInclude(graph, include.parent) : undefined;
  }
  const edge = graph.edges.find(
    (candidate) =>
      candidate.kind === 'param' &&
      candidate.target === step.id &&
      candidate.target_input === name &&
      candidate.source.startsWith('in:'),
  );
  const node = edge && graph.nodes.find((candidate) => candidate.id === edge.source);
  return node?.kind === 'input' && node.declared ? inputBinding(graph, node, sources) : null;
}

/**
 * Where a bound parameter's value is set: the end of its binding chain.
 *
 * Overrides that pass on an input of the file above are followed to the
 * override or input that gives the value. An input's value is the session's
 * or the run config's when one of them gives it, else its declared default.
 *
 * Args:
 *   binding: From paramBinding.
 *   inputValues: The recipe inputs' current values, for a session or run
 *     config value.
 */
export function bindingTarget(binding: Binding, inputValues: Record<string, unknown> = {}): BindingTarget {
  let last = binding;
  while (last.kind === 'override' && last.passesOn) last = last.passesOn;
  if (last.kind === 'override') {
    return { kind: 'override', include: last.include, name: last.name, value: last.include.overrides[last.name] };
  }
  const { name } = last;
  const where = { name, declaredIn: last.declaredIn };
  if (last.source === 'session' || last.source === 'config') {
    return { kind: last.source, ...where, value: name in inputValues ? inputValues[name] : last.default };
  }
  return { kind: 'default', ...where, value: last.default };
}

/**
 * Where a target lives, in words: "Set in a.yaml (override of b.yaml)".
 *
 * Args:
 *   graph: The session graph.
 *   target: From bindingTarget.
 *   configFile: The run config file's name, for a value set there.
 */
export function targetText(graph: Graph, target: BindingTarget, configFile: string | null = null): string {
  switch (target.kind) {
    case 'override':
      return `Set in ${includerFile(graph, target.include)} (override of ${target.include.source})`;
    case 'default':
      return `Default of input ${target.name} in ${target.declaredIn}`;
    case 'session':
      return 'Set in this session';
    case 'config':
      return `Set in ${configFile ?? 'the run config file'}; edit it in the Run Config pane`;
  }
}

/** The edit operation that gives a target a value, or null for a session or run config value. */
export function targetOperation(target: BindingTarget, value: unknown): Operation | null {
  if (target.kind === 'override') return setIncludeOverride(target.include.source, target.name, value);
  if (target.kind === 'default') return setRecipeInput(target.declaredIn, target.name, 'default', value);
  return null;
}

/** Step ids in graph order. */
function inGraphOrder(graph: Graph, ids: Set<string>): string[] {
  return stepNodes(graph)
    .filter((step) => ids.has(step.id))
    .map((step) => step.id);
}

/** The steps an include's override reaches, through the include's bindings. */
export function overrideReaders(graph: Graph, include: GraphInclude, name: string): string[] {
  const bound = include.bindings?.[name];
  if (bound) return inGraphOrder(graph, new Set(bound.map((binding) => binding.step)));
  return includeOverrideRows(graph, include.id).find((row) => row.name === name)?.usedBy ?? [];
}

/**
 * The steps an input's value reaches: the steps reading it, and the steps of
 * the includes the declaring file passes it into with `${inputs.<name>}`.
 *
 * Args:
 *   graph: The session graph.
 *   name: The input.
 *   file: The file declaring it.
 */
export function inputReaders(graph: Graph, name: string, file: string): string[] {
  const ids = new Set(readers(graph, `in:${name}`).filter((id) => findStep(graph, id)));
  const owner = includeOf(graph, file)?.id ?? null;
  for (const include of graph.includes) {
    if ((include.parent ?? null) !== owner) continue;
    for (const [override, value] of Object.entries(include.overrides)) {
      if (value !== `\${inputs.${name}}`) continue;
      for (const step of overrideReaders(graph, include, override)) ids.add(step);
    }
  }
  return inGraphOrder(graph, ids);
}

/** The steps a target's value reaches. */
export function targetReaders(graph: Graph, target: BindingTarget): string[] {
  if (target.kind === 'override') return overrideReaders(graph, target.include, target.name);
  return inputReaders(graph, target.name, target.declaredIn);
}

/** The steps changed values feed besides those a form is about. */
export interface Reach {
  /** What they are, in words, such as "2 other steps". */
  lead: string;
  /** The steps listed: those the form is not about. */
  others: string[];
  /** Every step the changed values feed. */
  total: number;
}

/** What changed values reaching `reached` mean for a step's form: the other steps they feed. */
export function stepReach(stepId: string, reached: string[]): Reach {
  const all = new Set([...reached, stepId]);
  const others = [...all].filter((id) => id !== stepId);
  return { lead: plural(others.length, 'other step'), others, total: all.size };
}

/** What changed values reaching `reached` mean for a sub-recipe's form: the steps outside it they feed. */
export function includeReach(include: GraphInclude, reached: string[]): Reach {
  const all = new Set(reached);
  const others = [...all].filter((id) => !include.steps.includes(id));
  return { lead: `${plural(others.length, 'step')} outside ${include.title}`, others, total: all.size };
}

/** The text a form shows when an input's default is not what a run uses. */
export function overriddenNote(override: unknown): string {
  return (
    `The parent sets this input where it includes the file (override: ${showValue(override)}), ` +
    'so the default is not used. Edit the override instead.'
  );
}

/** The text a form shows when the run config or the session gives an input its value. */
export function runSetNote(source: InputSource): string | null {
  if (source === 'config') return 'The run config sets this input, so its default is not used while it does.';
  if (source === 'session') return 'This session sets this input, so its default is not used while it does.';
  return null;
}
