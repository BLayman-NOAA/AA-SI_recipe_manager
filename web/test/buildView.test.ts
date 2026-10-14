// Fixtures: `aa-recipe graph tests/fixtures/client_recipes/<name>.yaml --json`
// written to test/fixtures/<name>.graph.json; regenerate after a graph schema change.
import { describe, expect, it } from 'vitest';
import type { Graph, GraphEdge } from '../src/api/graph';
import {
  buildView,
  findSteps,
  includeOutputRows,
  includeOverrideRows,
  inputsBoxRows,
  listLabel,
  outputsBoxRows,
  overrideFeeds,
  rowValue,
  shortTitle,
  stepOutputRows,
  type ViewNode,
  type ViewOptions,
} from '../src/diagram/buildView';
import mappedJson from './fixtures/mapped.graph.json';
import uc1Json from './fixtures/uc1.graph.json';
import { declaredGraph, withSubGraph } from './helpers';

const mappedGraph = mappedJson as unknown as Graph;
const uc1Graph = uc1Json as unknown as Graph;
const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';
const LVL0 = 'inc:sub_recipes/processing_lvl_0_user.yaml';
const SURVEY = 'inc:sub_recipes/processing_lvl_2_survey.yaml';
const PLAN = 'inc:sub_recipes/dive_plan.yaml';
const LVL2 = 'inc:sub_recipes/processing_lvl_2_user.yaml';

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

/** mapped with a recipe input that seg maps over and proc reads. */
function mappedWithInput(): Graph {
  const graph = structuredClone(mappedGraph);
  graph.nodes.unshift({
    id: 'in:files',
    kind: 'input',
    label: 'inputs.files',
    type: 'list',
    declared: true,
    required: true,
    default: ['a', 'b'],
    description: 'Files to read.',
  });
  const edge = (target: string, kind: GraphEdge['kind'], input: string) =>
    ({
      source: 'in:files',
      target,
      kind,
      source_output: null,
      target_input: input,
      label: 'files',
      type: 'list',
    }) as GraphEdge;
  graph.edges.push(edge('proc', 'map_over', 'x'), edge('seg', 'param', 'files'));
  return graph;
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
      },
    ]);
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

  it('lists a map over input as item of the input', () => {
    const view = buildView(mappedWithInput(), options());
    const box = node(view.nodes, 'inputs', 'inputsBox');
    expect(box.data.rows.map((row) => [row.name, row.usedBy])).toEqual([
      ['item of files', ['proc']],
      ['files', ['seg']],
    ]);
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
    expect(inputs.data.title).toBe('Inputs from the parent');
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
