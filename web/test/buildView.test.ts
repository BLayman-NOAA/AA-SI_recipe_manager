// Fixtures: `aa-recipe graph tests/fixtures/client_recipes/<name>.yaml --json`
// written to test/fixtures/<name>.graph.json; regenerate after a graph schema change.
import { describe, expect, it } from 'vitest';
import { findInclude, findStep, type Graph, type GraphEdge, type StepNode } from '../src/api/graph';
import {
  buildView,
  findSteps,
  includeInputRows,
  includeMeta,
  includeOutputRows,
  includeOverrideRows,
  inputsBoxMore,
  inputsBoxRows,
  listLabel,
  outputsBoxRows,
  overrideFeeds,
  ownInputRows,
  recipeInputRows,
  rootInputRows,
  rowTag,
  rowValue,
  shortTitle,
  stepOutputRows,
  type ViewNode,
  type ViewOptions,
} from '../src/diagram/buildView';
import { edgeTooltip } from '../src/diagram/useLayout';
import hb1603Json from './fixtures/hb1603.graph.json';
import mappedJson from './fixtures/mapped.graph.json';
import uc1Json from './fixtures/uc1.graph.json';
import { declaredGraph, mappedInputGraph, mappedOverInputGraph, nestedInputGraph, withSubGraph } from './helpers';

const mappedGraph = mappedJson as unknown as Graph;
const uc1Graph = uc1Json as unknown as Graph;
const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';
const LVL0 = 'inc:sub_recipes/processing_lvl_0_user.yaml';
const SURVEY = 'inc:sub_recipes/processing_lvl_2_survey.yaml';
const PLAN = 'inc:sub_recipes/dive_plan.yaml';
const LVL2 = 'inc:sub_recipes/processing_lvl_2_user.yaml';
const LVL1 = 'inc:sub_recipes/processing_lvl_1_per_file.yaml';
const LVL0_FILE = 'sub_recipes/processing_lvl_0_user.yaml';

function options(overrides: Partial<ViewOptions> = {}): ViewOptions {
  return {
    drill: [],
    connected: false,
    selected: null,
    expandedSteps: new Set<string>(),
    expandedBoxes: new Set<string>(),
    ...overrides,
  };
}

function ids(nodes: ViewNode[]): string[] {
  return nodes.map((node) => node.id);
}

function node<K extends ViewNode['kind']>(nodes: ViewNode[], id: string, kind?: K): Extract<ViewNode, { kind: K }> {
  const found = nodes.find((candidate) => candidate.id === id);
  if (!found) throw new Error(`no node ${id} in ${ids(nodes).join(', ')}`);
  if (kind && found.kind !== kind) throw new Error(`${id} is a ${found.kind}, not a ${kind}`);
  return found as Extract<ViewNode, { kind: K }>;
}

describe('buildView', () => {
  it('draws a collapsed include as one box with folded edges', () => {
    const view = buildView(withSubGraph, options());
    expect(ids(view.nodes)).toEqual(expect.arrayContaining(['seed', CHAIN, 'total']));
    expect(ids(view.nodes)).not.toContain('chain_inc');
    expect(ids(view.nodes)).not.toContain('leaf_add');
    expect(node(view.nodes, CHAIN).kind).toBe('include');

    const into = view.edges.find((edge) => edge.source === 'seed' && edge.target === CHAIN);
    expect(into?.label).toBe('items');
    expect(into?.names).toEqual(['items']);
    expect(into?.targetHandle).toBe('in:x@seed');
    expect(into?.bus).toBeNull();
    const out = view.edges.find((edge) => edge.source === CHAIN && edge.target === 'total');
    expect(out?.label).toBe('out');
    expect(out?.sourceHandle).toBe('out:leaf_add.out');
    expect(view.breadcrumb).toEqual([{ label: 'client_with_sub', drill: [] }]);
  });

  it('lists the ports an include box exposes', () => {
    const box = node(buildView(withSubGraph, options()).nodes, CHAIN, 'include');
    expect(box.data.inPorts.map((port) => port.id)).toEqual(['in:x@seed']);
    expect(box.data.outPorts.map((port) => port.id)).toEqual(['out:leaf_add.out']);
    expect(box.data.stepCount).toBe(2);
    expect(box.data.overrideCount).toBe(2);
  });

  it('shows consumed outputs only, merges inbound ports and names repeated ports by step', () => {
    const graph = structuredClone(withSubGraph);
    const edge = (source: string, target: string, kind: GraphEdge['kind'], output: string, input: string | null) =>
      ({ source, target, kind, source_output: output, target_input: input, label: output, type: 'int' }) as GraphEdge;
    // total also reads chain_inc.out, an unused output leaves the box, and leaf_add reads seed.items too.
    graph.edges.push(edge('chain_inc', 'total', 'data', 'out', 'values'));
    graph.includes.find((inc) => inc.id === CHAIN)!.consumed.push({ step: 'chain_inc', output: 'out', by: ['total'] });
    graph.nodes.push({
      id: 'out:chain_inc.extra',
      kind: 'output',
      label: 'chain_inc.extra',
      type: 'int',
      declared: false,
      from: { step: 'chain_inc', output: 'extra' },
    });
    graph.edges.push(edge('chain_inc', 'out:chain_inc.extra', 'exit', 'extra', null));
    graph.edges.push(edge('seed', 'leaf_add', 'data', 'items', 'x'));

    const view = buildView(graph, options());
    const box = node(view.nodes, CHAIN, 'include');
    expect(box.data.outPorts.map((port) => port.label)).toEqual(['leaf_add.out', 'chain_inc.out']);
    expect(box.data.inPorts).toEqual([{ id: 'in:x@seed', label: 'x', title: 'chain_inc.x, leaf_add.x' }]);
    // At the top level only recipe outputs get a box, so the unused one has none.
    expect(ids(view.nodes)).not.toContain(`cout:${CHAIN}`);
  });

  it('never shows _item in edge labels', () => {
    const drilled = buildView(withSubGraph, options({ drill: [CHAIN, LEAF] }));
    const view = buildView(withSubGraph, options({ drill: [CHAIN] }));
    expect(view.edges.find((e) => e.target === 'chain_inc' && !e.bus)?.label).toBe('item of items -> x');
    expect([...view.edges, ...drilled.edges].every((e) => !e.label.includes('_item'))).toBe(true);
  });

  it('folds recipe inputs into one inputs box with a line per target naming what it sends', () => {
    const view = buildView(withSubGraph, options({ inputValues: { n: 7 } }));
    expect(ids(view.nodes)).not.toContain('in:n');
    const box = node(view.nodes, 'inputs', 'inputsBox');
    expect(box.data).toMatchObject({ id: 'inputs', title: 'Recipe inputs', include: null, expanded: false });
    expect(box.data.rows).toEqual([
      {
        name: 'n',
        value: 7,
        set: true,
        type: 'int',
        description: 'Length of the seed list.',
        inputId: 'in:n',
        from: [],
        usedBy: ['seed'],
        source: 'own',
        setBy: 'run',
        declaredIn: 'with_sub.yaml',
      },
    ]);
    expect(box.data.more).toBe(0);
    expect(view.edges.find((edge) => edge.source === 'inputs')).toMatchObject({
      id: 'e:inputs->seed',
      target: 'seed',
      sourceHandle: 'bus',
      targetHandle: 'bus-in',
      names: ['n'],
      label: 'n',
      bus: 'in',
    });
  });

  it('lists a map over input once, naming its line item of the input', () => {
    const view = buildView(mappedInputGraph(), options());
    const box = node(view.nodes, 'inputs', 'inputsBox');
    expect(box.data.rows.map((row) => [row.name, row.usedBy])).toEqual([['files', ['proc', 'seg']]]);
    expect(view.edges.find((edge) => edge.id === 'e:inputs->proc')?.names).toEqual(['item of files']);
    expect(view.edges.find((edge) => edge.id === 'e:inputs->seg')?.names).toEqual(['files']);
  });

  it('draws no outputs box at the top level without declared recipe outputs', () => {
    const view = buildView(withSubGraph, options());
    expect(view.nodes.some((n) => n.kind === 'outputsBox')).toBe(false);
    expect(view.edges.some((edge) => edge.bus === 'out')).toBe(false);
    expect(buildView(uc1Graph, options()).nodes.some((n) => n.kind === 'outputsBox')).toBe(false);
  });

  it('boxes a declared recipe output at the top level', () => {
    const view = buildView(declaredGraph(), options());
    expect(ids(view.nodes)).not.toContain('out:total.total');
    const box = node(view.nodes, 'cout:total', 'outputsBox');
    expect(box.data).toMatchObject({ owner: 'total', ownerLabel: 'total', expanded: false, muted: false });
    expect(box.data.summary).toBe('1 recipe output');
    expect(box.data.rows).toEqual([
      { name: 'total', type: 'int', kind: 'final', by: [], outputId: 'out:total.total' },
    ]);
    expect(view.edges.find((edge) => edge.target === 'cout:total')).toMatchObject({
      id: 'e:total->cout:total',
      source: 'total',
      sourceHandle: 'bus-out',
      targetHandle: 'bus',
      names: ['total'],
      label: 'total',
      bus: 'out',
      muted: false,
    });
  });
  it('lists what leaves a drilled step, used later first, without _item rows', () => {
    const view = buildView(uc1Graph, options({ drill: [LVL0] }));
    const query = node(view.nodes, 'cout:query_ncei', 'outputsBox');
    expect(query.data.rows.map((row) => [row.name, row.kind])).toEqual([
      ['raw_urls', 'exit'],
      ['ncei_results', 'unused'],
      ['query_label', 'unused'],
    ]);
    expect(query.data.rows[0].by).toEqual(['extract_cal_params', 'read_raw']);
    expect(query.data.summary).toBe('1 used later, 2 unused');
    const rows = view.nodes.flatMap((n) => (n.kind === 'outputsBox' ? n.data.rows : []));
    expect(rows.some((row) => row.name.includes('_item'))).toBe(false);

    const standardize = node(view.nodes, 'cout:standardize_cal', 'outputsBox');
    expect(standardize.data.muted).toBe(true);
    const muted = view.edges.find((edge) => edge.target === 'cout:standardize_cal')!;
    expect(muted).toMatchObject({ muted: true, label: 'channel_count, skipped, single_channel_data' });
    expect(view.edges.find((edge) => edge.target === 'cout:build_cal_mapping')?.label).toBe(
      'calibration_dict, mapping_dict',
    );
  });

  it('mutes a drilled step whose outputs are all unused', () => {
    const view = buildView(uc1Graph, options({ drill: [SURVEY] }));
    expect(node(view.nodes, 'cout:crop_survey_range', 'outputsBox').data.muted).toBe(true);
  });

  it('labels a nested include box with its exit names, without the step prefix', () => {
    const drilled = buildView(withSubGraph, options({ drill: [CHAIN] }));
    expect(drilled.edges.find((edge) => edge.target === `cout:${LEAF}`)).toMatchObject({
      label: 'out',
      names: ['leaf_add.out'],
    });
  });
  it('names an include outputs box by its title without the cruise prefix', () => {
    // Declare an output of a lvl_0 step so the top level boxes the include.
    const graph = structuredClone(uc1Graph);
    const include = graph.includes.find((inc) => inc.id === LVL0)!;
    const output = graph.nodes.find((n) => n.kind === 'output' && include.steps.includes(n.from.step));
    if (output?.kind !== 'output') throw new Error('no output of lvl_0');
    output.declared = true;
    const box = node(buildView(graph, options()).nodes, `cout:${LVL0}`, 'outputsBox');
    expect(box.data.ownerLabel).toBe('processing_lvl_0_user');
    expect(box.data.ownerTitle).toBe('hb1603_processing_lvl_0_user');
    expect(shortTitle(withSubGraph, 'client_leaf')).toBe('client_leaf');
  });

  it('gives an include box its override rows, listed when expanded', () => {
    const view = buildView(withSubGraph, options({ expandedBoxes: new Set([CHAIN]) }));
    const box = node(view.nodes, CHAIN, 'include');
    expect(box.data.expanded).toBe(true);
    expect(box.data.rows).toEqual(includeOverrideRows(withSubGraph, CHAIN));
    expect(box.data.rows.map((row) => [row.name, row.from, row.usedBy])).toEqual([
      // The loader's bindings: the chain and its included leaf both map over source.
      ['source', [{ step: 'seed', output: 'items' }], ['chain_inc', 'leaf_add']],
      ['k', [], ['leaf_add']],
    ]);
    expect(node(buildView(withSubGraph, options()).nodes, CHAIN, 'include').data.expanded).toBe(false);
  });

  it('takes the steps an override feeds from the include bindings', () => {
    const dive = includeOverrideRows(uc1Graph, PLAN).find((row) => row.name === 'dive_labels')!;
    expect(dive.usedBy).toEqual(['plan_dives']);
    const drilled = buildView(uc1Graph, options({ drill: [PLAN] }));
    expect(drilled.edges.find((edge) => edge.id === `e:overrides:${PLAN}->plan_dives`)?.names).toContain('dive_labels');
    // An override that drives the include's per file map over is used by every mapped step.
    const perFile = includeOverrideRows(uc1Graph, LVL2).find((row) => row.name === 'per_file_source')!;
    expect(perFile.usedBy).toEqual(expect.arrayContaining(['dive_sv', 'dive_remove_noise']));
  });

  it('prefers bindings to matching values, and matches values only without them', () => {
    const graph = structuredClone(withSubGraph);
    const chain = graph.includes.find((inc) => inc.id === CHAIN)!;
    chain.bindings!.k = [{ step: 'chain_inc', slot: 'params.anything' }];
    expect(includeOverrideRows(graph, CHAIN).find((row) => row.name === 'k')!.usedBy).toEqual(['chain_inc']);
    delete chain.bindings;
    expect(includeOverrideRows(graph, CHAIN).find((row) => row.name === 'k')!.usedBy).toEqual(['leaf_add']);
  });

  it('shows the value of the parent input an override passes on', () => {
    const ping = (values?: Record<string, unknown>) =>
      includeOverrideRows(uc1Graph, LVL2, values).find((row) => row.name === 'ping_time_bin')!;
    expect(ping().resolved).toBe('10s');
    expect(rowValue(ping({ ping_time_bin: '5s' }))).toBe('${inputs.ping_time_bin} = 5s');
    // Through a nested include: leaf's k is chain's k, which the recipe sets to 5.
    expect(includeOverrideRows(withSubGraph, LEAF).find((row) => row.name === 'k')!.resolved).toBe(5);
    // An override that references a step has nothing to resolve.
    expect(includeOverrideRows(withSubGraph, CHAIN).find((row) => row.name === 'source')!.resolved).toBeUndefined();
  });

  it('lists exits and recipe outputs, not unused ones, in a nested include box', () => {
    const graph = structuredClone(withSubGraph);
    const output = (name: string, declared: boolean) => {
      graph.nodes.push({
        id: `out:leaf_add.${name}`,
        kind: 'output',
        label: `leaf_add.${name}`,
        type: 'int',
        declared,
        from: { step: 'leaf_add', output: name },
      });
      graph.edges.push({
        source: 'leaf_add',
        target: `out:leaf_add.${name}`,
        kind: 'exit',
        source_output: name,
        target_input: null,
        label: name,
        type: 'int',
      });
    };
    output('out', true);
    output('extra', false);
    const box = node(buildView(graph, options({ drill: [CHAIN] })).nodes, `cout:${LEAF}`, 'outputsBox');
    expect(box.data.rows.map((row) => [row.name, row.kind])).toEqual([
      ['leaf_add.out', 'exit'],
      ['leaf_add.out', 'final'],
    ]);
  });

  it('boxes a declared output that is also read inside, and keeps its core line', () => {
    const graph = declaredGraph();
    graph.nodes.push({
      id: 'out:seed.items',
      kind: 'output',
      label: 'seed.items',
      type: 'list',
      declared: true,
      from: { step: 'seed', output: 'items' },
    });
    graph.edges.push({
      source: 'seed',
      target: 'out:seed.items',
      kind: 'exit',
      source_output: 'items',
      target_input: null,
      label: 'items',
      type: 'list',
    });
    const view = buildView(graph, options());
    expect(node(view.nodes, 'cout:seed', 'outputsBox').data.rows.map((row) => [row.name, row.kind])).toEqual([
      ['items', 'final'],
    ]);
    expect(view.edges.some((edge) => edge.source === 'seed' && edge.target === CHAIN && !edge.bus)).toBe(true);
  });

  it('tells where each step output goes', () => {
    expect(stepOutputRows(withSubGraph, 'seed')).toEqual([
      { step: 'seed', name: 'items', type: 'list', final: false, by: ['chain_inc'] },
    ]);
    expect(stepOutputRows(declaredGraph(), 'total')).toEqual([
      { step: 'total', name: 'total', type: 'int', final: true, by: [] },
    ]);
    const outputs = includeOutputRows(uc1Graph, LVL0);
    const query = outputs.rows.filter((row) => row.step === 'query_ncei');
    expect(query.map((row) => [row.name, row.kind])).toEqual([
      ['ncei_results', 'unused'],
      ['query_label', 'unused'],
      ['raw_urls', 'exit'],
    ]);
    expect(outputs.inside).toBeGreaterThan(0);
  });
  it('shows ghosts, the inputs from the parent and outputs used outside when drilled in', () => {
    const view = buildView(withSubGraph, options({ drill: [CHAIN] }));
    const kinds = Object.fromEntries(view.nodes.map((n) => [n.id, n.kind]));
    expect(kinds).toEqual({
      'ghost:seed': 'ghost',
      chain_inc: 'step',
      [LEAF]: 'include',
      [`overrides:${CHAIN}`]: 'inputsBox',
      [`cout:${LEAF}`]: 'outputsBox',
    });

    const inputs = node(view.nodes, `overrides:${CHAIN}`, 'inputsBox');
    expect(inputs.data.title).toBe('Inputs');
    expect(inputs.data.include?.id).toBe(CHAIN);
    expect(inputs.data.rows.map((row) => [row.name, row.value, row.set, row.from])).toEqual([
      ['source', '${seed.items}', true, [{ step: 'seed', output: 'items' }]],
      ['k', 5, true, []],
    ]);
    const lines = view.edges.filter((edge) => edge.source === inputs.id);
    expect(lines.map((edge) => [edge.target, edge.names, edge.bus])).toEqual([
      ['chain_inc', ['source'], 'in'],
      [LEAF, ['source', 'k'], 'in'],
    ]);

    const outputs = node(view.nodes, `cout:${LEAF}`, 'outputsBox');
    expect(outputs.data.ownerLabel).toBe('client_leaf');
    expect(outputs.data.rows).toEqual([
      { name: 'leaf_add.out', type: 'int', kind: 'exit', by: ['total'], outputId: null },
    ]);
    expect(view.breadcrumb).toEqual([
      { label: 'client_with_sub', drill: [] },
      { label: 'client_chain', drill: [CHAIN] },
    ]);
  });

  it('draws a line from a ghost into the inputs from the parent when the override feeds no step', () => {
    // Without a binding the ghost has no direct edge to a step the override feeds.
    const graph = structuredClone(withSubGraph);
    graph.includes.find((inc) => inc.id === CHAIN)!.bindings!.source = [];
    const view = buildView(graph, options({ drill: [CHAIN] }));
    const pairs = view.edges.map((edge) => `${edge.source}>${edge.target}`);
    expect(pairs).toContain('ghost:seed>chain_inc');
    expect(view.edges.find((edge) => edge.id === `e:ghost:seed->overrides:${CHAIN}`)).toMatchObject({
      sourceHandle: 'bus-out',
      targetHandle: 'bus',
      names: ['items'],
      bus: 'in',
    });
  });

  it('draws no ghost line into the box when the ghost is wired to the step the override feeds', () => {
    const bound = buildView(withSubGraph, options({ drill: [CHAIN] })).edges.map((edge) => edge.id);
    expect(bound).not.toContain(`e:ghost:seed->overrides:${CHAIN}`);

    // chain_inc reads seed.items directly, which the include's `source` override also names.
    const graph = structuredClone(withSubGraph);
    const chainInc = graph.nodes.find((n) => n.id === 'chain_inc');
    if (chainInc?.kind !== 'step') throw new Error('no chain_inc');
    chainInc.inputs[0].value = '${seed.items}';

    const view = buildView(graph, options({ drill: [CHAIN] }));
    const pairs = view.edges.map((edge) => `${edge.source}>${edge.target}`);
    expect(pairs).toContain('ghost:seed>chain_inc');
    expect(pairs).toContain(`overrides:${CHAIN}>chain_inc`);
    expect(pairs).not.toContain(`ghost:seed>overrides:${CHAIN}`);
    const row = node(view.nodes, `overrides:${CHAIN}`, 'inputsBox').data.rows[0];
    expect(row.from).toEqual([{ step: 'seed', output: 'items' }]);
  });

  it('gives every node the selection a click on it means', () => {
    const view = buildView(declaredGraph(), options());
    expect(node(view.nodes, 'seed').selection).toEqual({ kind: 'step', id: 'seed' });
    expect(node(view.nodes, CHAIN).selection).toEqual({ kind: 'include', id: CHAIN });
    expect(node(view.nodes, 'inputs').selection).toEqual({ kind: 'inputsBox', id: 'inputs' });
    expect(node(view.nodes, 'cout:total').selection).toEqual({ kind: 'outputsBox', id: 'total' });
    const drilled = buildView(withSubGraph, options({ drill: [CHAIN] }));
    expect(node(drilled.nodes, 'ghost:seed').selection).toEqual({ kind: 'step', id: 'seed' });
    expect(node(drilled.nodes, `overrides:${CHAIN}`).selection).toEqual({
      kind: 'inputsBox',
      id: `overrides:${CHAIN}`,
    });
    expect(node(drilled.nodes, `cout:${LEAF}`).selection).toEqual({ kind: 'outputsBox', id: LEAF });
  });

  it('marks a selected box', () => {
    const view = buildView(declaredGraph(), options({ selected: { kind: 'outputsBox', id: 'total' } }));
    expect(node(view.nodes, 'cout:total').selected).toBe(true);
    expect(node(view.nodes, 'total').selected).toBe(false);
  });

  it('expands steps and boxes that are listed as expanded', () => {
    const view = buildView(
      declaredGraph(),
      options({ expandedSteps: new Set(['seed']), expandedBoxes: new Set(['inputs', 'cout:total']) }),
    );
    const seed = node(view.nodes, 'seed', 'step');
    expect(seed.data.expanded).toBe(true);
    expect(seed.data.params.map((param) => [param.name, param.value, param.set])).toEqual([
      ['n', '${inputs.n}', true],
    ]);
    expect(node(view.nodes, 'total', 'step').data.expanded).toBe(false);
    expect(node(view.nodes, 'inputs', 'inputsBox').data.expanded).toBe(true);
    expect(node(view.nodes, 'cout:total', 'outputsBox').data.expanded).toBe(true);
  });

  it('gives the box rows helpers the same rows as the boxes', () => {
    const view = buildView(withSubGraph, options({ inputValues: { n: 4 } }));
    expect(inputsBoxRows(withSubGraph, [], { n: 4 })).toEqual(node(view.nodes, 'inputs', 'inputsBox').data.rows);
    expect(outputsBoxRows(withSubGraph, [], 'total')).toEqual([]);
    const declared = declaredGraph();
    expect(outputsBoxRows(declared, [], 'total')).toEqual(
      node(buildView(declared, options()).nodes, 'cout:total', 'outputsBox').data.rows,
    );
    const drilled = buildView(withSubGraph, options({ drill: [CHAIN] }));
    expect(inputsBoxRows(withSubGraph, [CHAIN])).toEqual(
      node(drilled.nodes, `overrides:${CHAIN}`, 'inputsBox').data.rows,
    );
    expect(outputsBoxRows(withSubGraph, [CHAIN], LEAF)).toEqual(
      node(drilled.nodes, `cout:${LEAF}`, 'outputsBox').data.rows,
    );
    expect(outputsBoxRows(withSubGraph, [], 'nobody')).toEqual([]);
  });

  it('dims nodes not connected to the selection in connected mode', () => {
    const view = buildView(withSubGraph, options({ connected: true, selected: { kind: 'step', id: 'seed' } }));
    expect(node(view.nodes, 'seed').selected).toBe(true);
    expect(node(view.nodes, 'seed').dimmed).toBe(false);
    expect(node(view.nodes, CHAIN).dimmed).toBe(false);
    expect(node(view.nodes, 'inputs').dimmed).toBe(false);
    expect(node(view.nodes, 'total').dimmed).toBe(true);
    expect(view.edges.find((edge) => edge.source === CHAIN)?.dimmed).toBe(true);
  });

  it('focuses an outputs box selection on the box', () => {
    const selected = { kind: 'outputsBox', id: 'total' } as const;
    const view = buildView(declaredGraph(), options({ connected: true, selected }));
    expect(node(view.nodes, 'cout:total').dimmed).toBe(false);
    expect(node(view.nodes, 'total').dimmed).toBe(false);
    expect(node(view.nodes, 'seed').dimmed).toBe(true);
  });

  it('dims nothing when connected mode is off', () => {
    const view = buildView(withSubGraph, options({ selected: { kind: 'step', id: 'seed' } }));
    expect(view.nodes.some((n) => n.dimmed)).toBe(false);
  });

  it('draws a mapped group around its members when they are all visible steps', () => {
    const view = buildView(mappedGraph, options());
    const groups = view.nodes.filter((n) => n.kind === 'mapGroup');
    expect(groups).toHaveLength(1);
    const members = view.nodes.filter((n) => n.parentId === groups[0].id).map((n) => n.id);
    expect(members).toEqual(expect.arrayContaining(['proc', 'proc2']));
    expect(ids(view.nodes).indexOf(groups[0].id)).toBeLessThan(ids(view.nodes).indexOf('proc'));
  });

  it('does not draw a mapped group whose members are folded into an include', () => {
    expect(buildView(withSubGraph, options()).nodes.some((n) => n.kind === 'mapGroup')).toBe(false);
  });

  it('marks steps and include boxes matching the find query', () => {
    const view = buildView(withSubGraph, options({ findQuery: 'leaf' }));
    expect(node(view.nodes, CHAIN).match).toBe(true);
    expect(node(view.nodes, 'seed').match).toBe(false);
  });
});

describe('inputs boxes mirror the files', () => {
  const inputsBox = (graph: Graph, drill: string[] = [], extra: Partial<ViewOptions> = {}) => {
    const id = drill.length ? `overrides:${drill.at(-1)}` : 'inputs';
    return node(buildView(graph, options({ drill, ...extra })).nodes, id, 'inputsBox');
  };
  const names = (rows: { name: string }[]) => rows.map((row) => row.name);
  const declared = (graph: Graph) => graph.nodes.filter((n) => n.kind === 'input' && n.declared).length;

  it('lists only the root file inputs at the root, used or not, and counts the rest', () => {
    const box = inputsBox(uc1Graph);
    expect(names(box.data.rows)).toEqual([
      'cal_input_folder',
      'calibration_output_dir',
      'crop_max_range_m',
      'ping_time_bin',
    ]);
    expect(box.data.rows.every((row) => row.source === 'own' && row.declaredIn === 'dive_analysis.yaml')).toBe(true);
    expect(box.data.rows).toEqual(rootInputRows(uc1Graph));
    expect(box.data.more).toBe(declared(uc1Graph) - 4);
    expect(inputsBoxMore(uc1Graph, [])).toBe(box.data.more);
  });

  it('names on a line only what the box lists', () => {
    const view = buildView(uc1Graph, options());
    const into = (target: string) => view.edges.find((edge) => edge.id === `e:inputs->${target}`);
    expect([...into(LVL0)!.names].sort()).toEqual(['cal_input_folder', 'calibration_output_dir']);
    expect(into(SURVEY)!.names).toEqual(['crop_max_range_m']);
    // processing_lvl_1_per_file reads only sonar_model, which its own file declares.
    expect(into(LVL1)).toBeUndefined();
  });

  it('gives a sub-recipe box its overrides, then the inputs its file declares', () => {
    const { overrides, own } = includeInputRows(uc1Graph, LVL0);
    expect(overrides).toEqual(includeOverrideRows(uc1Graph, LVL0));
    expect(overrides.every((row) => row.source === 'override' && row.setBy === 'parent')).toBe(true);
    expect(names(own)).toEqual([
      'cruise_name',
      'file_time_start',
      'file_time_end',
      'cruise_id',
      'record_author',
      'max_scan_bytes',
      'match_tolerances',
    ]);
    expect(own.every((row) => row.source === 'own' && row.setBy === 'default' && !row.set)).toBe(true);
    expect(own.every((row) => row.declaredIn === LVL0_FILE)).toBe(true);
    expect(own[0]).toMatchObject({ value: 'HB1603', inputId: 'in:cruise_name', usedBy: ['query_ncei'] });

    const box = node(buildView(uc1Graph, options()).nodes, LVL0, 'include');
    expect(box.data).toMatchObject({ overrideCount: 2, inputCount: 7 });
    expect(box.data.rows).toEqual([...overrides, ...own]);
    expect(includeMeta(box.data)).toBe('5 steps, 2 overrides, 7 inputs');
    expect(includeMeta(node(buildView(uc1Graph, options()).nodes, SURVEY, 'include').data)).toBe(
      '5 steps, 4 overrides',
    );
  });

  it('marks an own input the run sets, with its source when known', () => {
    const values = { cruise_name: 'HB1604' };
    const config = ownInputRows(uc1Graph, LVL0, values, { cruise_name: 'config' })[0];
    expect(config).toMatchObject({ value: 'HB1604', setBy: 'run', runSource: 'config', set: true });
    expect(rowTag(config)).toBe('run config');
    const unknown = ownInputRows(uc1Graph, LVL0, values)[0];
    expect(unknown).toMatchObject({ setBy: 'run', set: true });
    expect(unknown.runSource).toBeUndefined();
    expect(rowTag(unknown)).toBe('set');
    // The root box is unchanged by a run value for a sub-recipe input.
    expect(names(inputsBox(uc1Graph, [], { inputValues: values }).data.rows)).not.toContain('cruise_name');
  });

  it('lists a drilled sub-recipe box in two sections, lines as before', () => {
    const box = inputsBox(uc1Graph, [LVL0]);
    expect(box.data.title).toBe('Inputs');
    expect(box.data.rows.map((row) => row.source)).toEqual([...Array(2).fill('override'), ...Array(7).fill('own')]);
    expect(box.data.more).toBe(0);
    const view = buildView(uc1Graph, options({ drill: [LVL0] }));
    expect(view.edges.find((edge) => edge.id === `e:overrides:${LVL0}->query_ncei`)?.names).toEqual([
      'file_time_start',
      'file_time_end',
      'cruise_name',
    ]);
    expect(view.edges.find((edge) => edge.id === `e:overrides:${LVL0}->scan_raw_config`)?.names).toEqual([
      'max_scan_bytes',
    ]);
  });

  it('counts an input declared in a nested sub-recipe until the view reaches its file', () => {
    const graph = nestedInputGraph();
    expect(inputsBox(graph).data.more).toBe(1);
    expect(names(inputsBox(graph).data.rows)).toEqual(['n']);
    expect(buildView(graph, options()).edges.some((edge) => edge.names.includes('offset'))).toBe(false);

    const chain = inputsBox(graph, [CHAIN]);
    expect(chain.data.more).toBe(1);
    expect(names(chain.data.rows)).toEqual(['source', 'k']);

    const leaf = inputsBox(graph, [CHAIN, LEAF]);
    expect(leaf.data.more).toBe(0);
    expect(leaf.data.rows.map((row) => [row.name, row.source])).toEqual([
      ['value', 'override'],
      ['k', 'override'],
      ['offset', 'own'],
    ]);
    expect(leaf.data.rows[2]).toMatchObject({
      value: 2,
      setBy: 'default',
      declaredIn: 'sub/leaf.yaml',
      usedBy: ['leaf_add'],
    });
    const view = buildView(graph, options({ drill: [CHAIN, LEAF] }));
    expect(view.edges.find((edge) => edge.id === `e:overrides:${LEAF}->leaf_add`)?.names).toContain('offset');
  });

  it('takes an own input value from an include further up that overrides it', () => {
    const graph = nestedInputGraph();
    const ownOfChain = ownInputRows(graph, CHAIN);
    graph.includes.find((inc) => inc.id === CHAIN)!.overrides.offset = 7;
    const offset = ownInputRows(graph, LEAF).find((row) => row.name === 'offset')!;
    expect(offset).toMatchObject({ value: 7, setBy: 'parent', set: true, source: 'own' });
    expect(rowTag(offset)).toBe('parent');
    expect(ownInputRows(graph, CHAIN)).toEqual(ownOfChain);
    expect(names(includeOverrideRows(graph, CHAIN))).toEqual(['source', 'k', 'offset']);
  });

  it('keeps an input the parent overrides with a literal under the overrides', () => {
    const { overrides, own } = includeInputRows(withSubGraph, CHAIN);
    expect(overrides.find((row) => row.name === 'k')).toMatchObject({ value: 5, source: 'override', type: 'int' });
    expect(own).toEqual([]);
    expect(node(buildView(withSubGraph, options()).nodes, CHAIN, 'include').data.inputCount).toBe(0);
  });

  it('marks a required input with no value unset', () => {
    const graph = nestedInputGraph();
    const offset = graph.nodes.find((n) => n.id === 'in:offset');
    if (offset?.kind !== 'input') throw new Error('no in:offset');
    Object.assign(offset, { required: true, default: null });
    Object.assign(graph.includes.find((inc) => inc.id === LEAF)!.inputs!.at(-1)!, { required: true, default: null });
    const row = ownInputRows(graph, LEAF).find((candidate) => candidate.name === 'offset')!;
    expect(row).toMatchObject({ setBy: 'unset', set: false });
    expect(rowValue(row)).toBe('unset');
    expect(rowTag(row)).toBe('required');
    expect(ownInputRows(graph, LEAF, {}, { offset: 'unset' })[0].setBy).toBe('unset');
  });

  it('lists an input of another file a step in view reads by name', () => {
    const graph = nestedInputGraph();
    const offset = graph.nodes.find((n) => n.id === 'in:offset');
    if (offset?.kind !== 'input') throw new Error('no in:offset');
    // chain.yaml declares it instead of leaf.yaml, and seed at the top level also reads it.
    offset.declared_in = 'sub/chain.yaml';
    const leafInclude = graph.includes.find((inc) => inc.id === LEAF)!;
    const chainInclude = graph.includes.find((inc) => inc.id === CHAIN)!;
    chainInclude.inputs = [...chainInclude.inputs!, leafInclude.inputs!.pop()!];
    graph.edges.push({ ...graph.edges.at(-1)!, target: 'seed' });
    const root = inputsBox(graph);
    expect(root.data.rows.map((row) => [row.name, row.source, row.declaredIn, row.usedBy])).toEqual([
      ['n', 'own', 'with_sub.yaml', ['seed']],
      ['offset', 'other', 'sub/chain.yaml', ['seed']],
    ]);
    expect(root.data.more).toBe(0);
    const view = buildView(graph, options());
    expect(view.edges.find((edge) => edge.id === 'e:inputs->seed')?.names).toEqual(['n', 'offset']);
    expect(view.edges.find((edge) => edge.id === `e:inputs->${CHAIN}`)).toBeUndefined();

    // Drilled into leaf, an input of the enclosing chain.yaml is read by name.
    const leaf = inputsBox(graph, [CHAIN, LEAF]);
    expect(leaf.data.rows.at(-1)).toMatchObject({ name: 'offset', source: 'other', declaredIn: 'sub/chain.yaml' });
  });

  /** An edge from the recipe input n into a step, as the loader leaves one after substituting it. */
  const fromN = (target: string, input: string): GraphEdge => ({
    source: 'in:n',
    target,
    kind: 'param',
    source_output: null,
    target_input: input,
    label: 'n',
    type: 'int',
  });

  it('credits an input passed on by two overrides only to the override each step takes', () => {
    // chain's entry sets m and m2 both to ${inputs.n}; chain_inc takes m and a second step chain_b takes m2.
    const graph = structuredClone(withSubGraph);
    const chainInc = graph.nodes.find((n) => n.id === 'chain_inc') as StepNode;
    graph.nodes.push({ ...structuredClone(chainInc), id: 'chain_b', group: null, mapped: false });
    const chain = graph.includes.find((inc) => inc.id === CHAIN)!;
    chain.steps.push('chain_b');
    Object.assign(chain.overrides, { m: '${inputs.n}', m2: '${inputs.n}' });
    chain.bindings = {
      ...chain.bindings,
      m: [{ step: 'chain_inc', slot: 'params.m' }],
      m2: [{ step: 'chain_b', slot: 'params.m2' }],
    };
    graph.edges.push(fromN('chain_inc', 'm'), fromN('chain_b', 'm2'));
    const view = buildView(graph, options({ drill: [CHAIN] }));
    const line = (target: string) => view.edges.find((edge) => edge.id === `e:overrides:${CHAIN}->${target}`)?.names;
    expect(line('chain_inc')).toEqual(['source', 'm']);
    expect(line('chain_b')).toEqual(['m2']);
    const rows = node(view.nodes, `overrides:${CHAIN}`, 'inputsBox').data.rows;
    expect(rows.find((row) => row.name === 'm')?.usedBy).toEqual(['chain_inc']);
    expect(rows.find((row) => row.name === 'm2')?.usedBy).toEqual(['chain_b']);
  });

  it('names a recipe input a nested entry passes on, not an override that passes the same input elsewhere', () => {
    // The root sets chain's m to ${inputs.n}; chain's entry of leaf passes n on as offset.
    const graph = structuredClone(withSubGraph);
    const chain = graph.includes.find((inc) => inc.id === CHAIN)!;
    const leaf = graph.includes.find((inc) => inc.id === LEAF)!;
    chain.overrides.m = '${inputs.n}';
    chain.bindings = { ...chain.bindings, m: [{ step: 'chain_inc', slot: 'params.m' }] };
    leaf.overrides.offset = '${inputs.n}';
    leaf.bindings = { ...leaf.bindings, offset: [{ step: 'leaf_add', slot: 'params.offset' }] };
    graph.edges.push(fromN('chain_inc', 'm'), fromN('leaf_add', 'offset'));
    const view = buildView(graph, options({ drill: [CHAIN] }));
    expect(view.edges.find((edge) => edge.id === `e:overrides:${CHAIN}->${LEAF}`)?.names).toEqual(['source', 'k', 'n']);
    expect(view.edges.find((edge) => edge.id === `e:overrides:${CHAIN}->chain_inc`)?.names).toEqual(['source', 'm']);
    const rows = node(view.nodes, `overrides:${CHAIN}`, 'inputsBox').data.rows;
    expect(rows.find((row) => row.name === 'm')?.usedBy).toEqual(['chain_inc']);
    expect(rows.at(-1)).toMatchObject({ name: 'n', source: 'other', declaredIn: 'with_sub.yaml', usedBy: ['leaf_add'] });
  });

  it('never names on a line into a sub-recipe box an input declared below it, whatever the edge order', () => {
    for (const first of [true, false]) {
      const graph = nestedInputGraph();
      const intoLeaf = graph.edges.pop()!;
      const intoSeed = { ...intoLeaf, target: 'seed' };
      graph.edges.push(...(first ? [intoSeed, intoLeaf] : [intoLeaf, intoSeed]));
      const view = buildView(graph, options());
      expect(view.edges.find((edge) => edge.id === `e:inputs->${CHAIN}`), String(first)).toBeUndefined();
      expect(view.edges.find((edge) => edge.id === 'e:inputs->seed')?.names).toEqual(['n', 'offset']);
      const offset = inputsBox(graph).data.rows.find((row) => row.name === 'offset');
      expect(offset, String(first)).toMatchObject({ source: 'other', usedBy: ['seed'] });
    }
  });

  it('reads an older graph as every input the root file declares', () => {
    const graph = structuredClone(uc1Graph);
    for (const n of graph.nodes) if (n.kind === 'input') delete n.declared_in;
    for (const include of graph.includes) delete include.inputs;
    const box = inputsBox(graph);
    expect(names(box.data.rows)).toEqual(names(recipeInputRows(graph)));
    expect(box.data.rows).toHaveLength(declared(graph));
    expect(box.data.more).toBe(0);
    expect(node(buildView(graph, options()).nodes, LVL0, 'include').data.inputCount).toBe(0);
    // Drilled in, inputs read directly are rows as before, now marked as another file's.
    const drilled = inputsBox(graph, [LVL0]).data.rows;
    expect(drilled.map((row) => row.source)).toEqual(['override', 'override', ...Array(7).fill('other')]);
    // The graph does not say which file declares them, so no row claims one.
    expect(drilled.some((row) => row.declaredIn !== undefined)).toBe(false);
  });

  it('lists an own input an include further up passes a recipe input to once', () => {
    // The root sets offset on chain's entry to ${inputs.n}; the loader substituted it, so n reaches leaf_add.
    const graph = nestedInputGraph();
    graph.nodes = graph.nodes.filter((n) => n.id !== 'in:offset');
    const edge = graph.edges.find((candidate) => candidate.source === 'in:offset')!;
    edge.source = 'in:n';
    graph.includes.find((inc) => inc.id === CHAIN)!.overrides.offset = '${inputs.n}';
    const view = buildView(graph, options({ drill: [CHAIN, LEAF], inputValues: { n: 3 } }));
    const box = node(view.nodes, `overrides:${LEAF}`, 'inputsBox');
    expect(box.data.rows.map((row) => [row.name, row.source])).toEqual([
      ['value', 'override'],
      ['k', 'override'],
      ['offset', 'own'],
    ]);
    const offset = box.data.rows[2];
    expect(rowValue(offset)).toBe('${inputs.n} = 3');
    expect(rowTag(offset)).toBe('parent');
    const line = view.edges.find((candidate) => candidate.id === `e:overrides:${LEAF}->leaf_add`)!;
    expect(line.names).toContain('offset');
    expect(line.names).not.toContain('n');
  });

  it('names a nested sub-recipe input that a step of the drilled file reads', () => {
    const graph = nestedInputGraph();
    graph.edges.push({ ...graph.edges.at(-1)!, target: 'chain_inc' });
    const view = buildView(graph, options({ drill: [CHAIN] }));
    const box = node(view.nodes, `overrides:${CHAIN}`, 'inputsBox');
    expect(box.data.more).toBe(0);
    expect(box.data.rows.at(-1)).toMatchObject({ name: 'offset', source: 'other', declaredIn: 'sub/leaf.yaml' });
    expect(box.data).toMatchObject({ overrideCount: 2, inputCount: 1 });
    expect(view.edges.find((edge) => edge.id === `e:overrides:${CHAIN}->chain_inc`)?.names).toEqual([
      'source',
      'offset',
    ]);
  });

  it('resolves an override that passes on an input no include above sets', () => {
    // chain.yaml declares offset and leaf's entry passes it on as k.
    const graph = nestedInputGraph();
    const offset = graph.nodes.find((n) => n.id === 'in:offset');
    if (offset?.kind !== 'input') throw new Error('no in:offset');
    offset.declared_in = 'sub/chain.yaml';
    const leaf = graph.includes.find((inc) => inc.id === LEAF)!;
    const chain = graph.includes.find((inc) => inc.id === CHAIN)!;
    chain.inputs = [...chain.inputs!, leaf.inputs!.pop()!];
    leaf.overrides.k = '${inputs.offset}';
    expect(includeOverrideRows(graph, LEAF, { n: 3, offset: 2 }).find((row) => row.name === 'k')?.resolved).toBe(2);
    expect(includeOverrideRows(graph, LEAF).find((row) => row.name === 'k')?.resolved).toBe(2);
  });

  it('takes the root declaration of an input a sub-recipe also declares', () => {
    // The root declares offset with no default; leaf.yaml's default 2 is not the one that applies.
    const graph = nestedInputGraph();
    const offset = graph.nodes.find((n) => n.id === 'in:offset');
    if (offset?.kind !== 'input') throw new Error('no in:offset');
    Object.assign(offset, { declared_in: 'with_sub.yaml', default: null, required: true });
    const row = ownInputRows(graph, LEAF).find((candidate) => candidate.name === 'offset')!;
    expect(row).toMatchObject({ setBy: 'unset', set: false });
    expect(row.value).toBeNull();
  });

  it('gives the box rows helper the rows with sources too', () => {
    const sources = { cruise_name: 'session' } as const;
    expect(inputsBoxRows(uc1Graph, [LVL0], { cruise_name: 'X' }, sources)).toEqual(
      inputsBox(uc1Graph, [LVL0], { inputValues: { cruise_name: 'X' }, inputSources: sources }).data.rows,
    );
    expect(inputsBoxRows(uc1Graph, [LVL0], {}, sources).find((row) => row.name === 'cruise_name')?.runSource).toBe(
      'session',
    );
  });
});

describe('view helpers', () => {
  it('joins at most three labels', () => {
    expect(listLabel(['a', 'b'])).toBe('a, b');
    expect(listLabel(['a', 'b', 'c', 'd', 'e'])).toBe('a, b, c +2 more');
  });

  it('finds steps by id or op, exact ids first', () => {
    expect(findSteps(withSubGraph, 'total').map((step) => step.id)).toEqual(['total']);
    expect(findSteps(withSubGraph, 'CUSTOM')).toHaveLength(4);
    expect(findSteps(withSubGraph, ' ')).toEqual([]);
  });

  it('matches include overrides to the slots they feed', () => {
    expect(overrideFeeds('sv_source', '${a.ds_Sv}', 'ds_Sv', '${a.ds_Sv}')).toBe(true);
    expect(overrideFeeds('masks', '${m.mask}', 'masks', ['${m.mask}', '${n.mask}'])).toBe(true);
    expect(overrideFeeds('noise_snr_threshold', '6.0dB', 'SNR_threshold', '6.0dB')).toBe(true);
    expect(overrideFeeds('noise_ping_num', 5, 'ping_num', 5)).toBe(true);
    expect(overrideFeeds('surface_depth_m', 10.0, 'range_sample_num', 10)).toBe(false);
    expect(overrideFeeds('k', 5, 'k', 6)).toBe(false);
  });
});

// hb1603.graph.json is the docs site's graph of the HB1603 dive analysis, trimmed:
//   node scripts/trim-graph.mjs < ../docs/data/recipes/hb1603-dive-analysis.json > test/fixtures/hb1603.graph.json
// Its 18 step per file run spans levels 1 to 3, and every level maps over the
// per_file_source override, bound to each member's map_over slot.
describe('a level draws its part of a per file run', () => {
  const H_LVL2 = 'inc:sub_recipes/processing_lvl_2_combined.yaml';
  const H_LVL2_BOX = `overrides:${H_LVL2}`;
  const LVL2_STEPS = [
    'extract_cal_params',
    'compute_sv',
    'compute_transducer_depth',
    'ep_add_depth',
    'crop_survey_range',
    'dive_sv',
    'dive_surface_mask',
    'dive_frequency_mask',
    'dive_seabed_line',
    'dive_seabed_mask',
    'dive_combine_masks',
    'dive_apply_mask',
    'dive_remove_noise',
  ];
  const hb1603 = () => structuredClone(hb1603Json as unknown as Graph);
  /** The names on each line out of a view's inputs box, by target. */
  const lanes = (view: ReturnType<typeof buildView>, box: string) =>
    Object.fromEntries(view.edges.filter((edge) => edge.source === box).map((edge) => [edge.target, edge.names]));

  it('draws a group around the steps of the run the level holds, named for the input it maps over', () => {
    const view = buildView(hb1603(), options({ drill: [H_LVL2] }));
    const groups = view.nodes.filter((n) => n.kind === 'mapGroup');
    expect(groups).toHaveLength(1);
    const group = node(view.nodes, 'group:1', 'mapGroup');
    expect(view.nodes.filter((n) => n.parentId === 'group:1').map((n) => n.id)).toEqual(LVL2_STEPS);
    expect(group.data.label).toBe('Runs once per item of per_file_source');
    expect(group.data.title).toBe(
      'Part of a run of 18 steps once per item. The others are in processing_lvl_1_per_file and processing_lvl_3_user.',
    );
  });

  it('sends the mapped input on one line into the group and leaves it off the step lines', () => {
    const view = buildView(hb1603(), options({ drill: [H_LVL2] }));
    const naming = view.edges.filter((edge) => edge.names.includes('per_file_source'));
    expect(naming.map((edge) => [edge.source, edge.target, edge.names])).toEqual([
      [H_LVL2_BOX, 'group:1', ['per_file_source']],
    ]);
    expect(lanes(view, H_LVL2_BOX)).toEqual({
      'group:1': ['per_file_source'],
      extract_cal_params: ['calibration_dict', 'mapping_dict'],
      crop_survey_range: ['crop_max_range_m'],
      dive_sv: ['dive_windows', 'ping_time_bin'],
      dive_surface_mask: ['surface_depth_m'],
      dive_frequency_mask: ['frequencies_to_mask'],
      dive_seabed_line: ['seabed_lines_dir', 'seabed_min_coverage'],
      dive_seabed_mask: ['seafloor_buffer_m'],
      dive_remove_noise: ['noise_range_sample_num', 'noise_ping_num', 'noise_snr_threshold', 'noise_background_max'],
    });
  });

  it('gives the line into the group the value of the input', () => {
    const view = buildView(hb1603(), options({ drill: [H_LVL2] }));
    const line = view.edges.find((edge) => edge.id === `e:${H_LVL2_BOX}->group:1`)!;
    expect(edgeTooltip(view, line)).toBe('per_file_source = ${query_ncei.raw_urls}');
  });

  it('leaves the top level as it was: no group, the same lines', () => {
    const view = buildView(hb1603(), options());
    expect(view.nodes.some((n) => n.kind === 'mapGroup')).toBe(false);
    expect(lanes(view, 'inputs')).toEqual({
      'inc:sub_recipes/processing_lvl_0_user.yaml': ['calibration_output_dir', 'cal_input_folder'],
      [H_LVL2]: ['crop_max_range_m', 'ping_time_bin'],
      'inc:sub_recipes/processing_lvl_3_user.yaml': ['ping_time_bin'],
      'inc:sub_recipes/processing_lvl_5_sv_codes.yaml': ['ping_time_bin'],
    });
  });

  it('keeps the input on the line into a member that also reads it another way', () => {
    const graph = hb1603();
    findInclude(graph, H_LVL2)!.bindings!.per_file_source.push({ step: 'compute_sv', slot: 'source_name' });
    const view = buildView(graph, options({ drill: [H_LVL2] }));
    const found = lanes(view, H_LVL2_BOX);
    expect(found['group:1']).toEqual(['per_file_source']);
    expect(found.compute_sv).toEqual(['per_file_source']);
    expect(found.ep_add_depth).toBeUndefined();
  });

  it('draws no group around a single visible member, which keeps its own line', () => {
    const graph = hb1603();
    graph.groups.find((group) => group.id === 'group:1')!.members = ['read_raw', 'combine_raw', 'compute_sv'];
    const view = buildView(graph, options({ drill: [H_LVL2] }));
    expect(view.nodes.some((n) => n.kind === 'mapGroup')).toBe(false);
    expect(view.nodes.some((n) => n.parentId)).toBe(false);
    expect(lanes(view, H_LVL2_BOX).compute_sv).toEqual(['per_file_source']);
    expect(lanes(view, H_LVL2_BOX)['group:1']).toBeUndefined();
  });

  it('leaves groups drawn whole, and ones it cannot draw, as they were', () => {
    const mapped = buildView(mappedGraph, options());
    const group = node(mapped.nodes, 'group:0', 'mapGroup');
    expect(group.data.label).toBe('Runs once per item of seg.items');
    expect(group.data.title).toBeNull();
    expect(mapped.edges.some((edge) => edge.target === 'group:0')).toBe(false);
    // with_sub's chain_inc and leaf_add: one is folded into a sub-recipe box, or out of the view.
    for (const drill of [[], [CHAIN], [CHAIN, LEAF]]) {
      expect(buildView(withSubGraph, options({ drill })).nodes.some((n) => n.kind === 'mapGroup')).toBe(false);
    }
  });

  it('at the top level sends a recipe input the group maps over on one line into it', () => {
    const view = buildView(mappedOverInputGraph(), options());
    expect(node(view.nodes, 'group:0', 'mapGroup').data.label).toBe('Runs once per item of files');
    // proc also reads files as a param, so it keeps its own line.
    expect(lanes(view, 'inputs')).toEqual({ 'group:0': ['files'], seg: ['files'], proc: ['files'] });
    const line = view.edges.find((edge) => edge.id === 'e:inputs->group:0')!;
    expect(edgeTooltip(view, line)).toBe('files = ["a","b"]');
  });

  it('says when other members of the run are at the top level', () => {
    const graph = hb1603();
    graph.groups.find((group) => group.id === 'group:1')!.members.push('plan_dives');
    findStep(graph, 'plan_dives')!.include = null;
    const title = node(buildView(graph, options({ drill: [H_LVL2] })).nodes, 'group:1', 'mapGroup').data.title;
    expect(title).toBe(
      'Part of a run of 19 steps once per item. The others are in processing_lvl_1_per_file and processing_lvl_3_user and at the top level.',
    );
  });

  it('highlights the line into the group with a member and the members with the inputs box', () => {
    const member = buildView(
      hb1603(),
      options({ drill: [H_LVL2], connected: true, selected: { kind: 'step', id: 'compute_sv' } }),
    );
    expect(member.edges.find((edge) => edge.target === 'group:1')?.dimmed).toBe(false);
    expect(node(member.nodes, H_LVL2_BOX).dimmed).toBe(false);
    expect(node(member.nodes, 'dive_sv').dimmed).toBe(true);

    const box = buildView(
      hb1603(),
      options({ drill: [H_LVL2], connected: true, selected: { kind: 'inputsBox', id: H_LVL2_BOX } }),
    );
    expect(box.edges.find((edge) => edge.target === 'group:1')?.dimmed).toBe(false);
    for (const id of LVL2_STEPS) expect(node(box.nodes, id).dimmed, id).toBe(false);
  });
});
