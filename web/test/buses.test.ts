// The UC1 fixture is trimmed of descriptions and hashes; regenerate it from the
// UC1 folder (AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1) with
//   aa-recipe graph dive_analysis.yaml --json | node ../../../../AA-SI_recipe_manager/web/scripts/trim-graph.mjs > ../../../../AA-SI_recipe_manager/web/test/fixtures/uc1.graph.json
// The HB1603 fixture is the docs site's graph, trimmed the same way:
//   node scripts/trim-graph.mjs < ../docs/data/recipes/hb1603-dive-analysis.json > test/fixtures/hb1603.graph.json
// ELK runs in thread through elkjs's bundled build, as in layout.test.ts.
import ELK from 'elkjs/lib/elk.bundled.js';
import { describe, expect, it } from 'vitest';
import type { Graph } from '../src/api/graph';
import { buildView } from '../src/diagram/buildView';
import { placeBuses, routeBuses, segmentHitsRect } from '../src/diagram/buses';
import type { View } from '../src/diagram/buildView';
import { elkGraph, groupLabelWidth, layoutRequest, readLayout, type LayoutRequest } from '../src/diagram/layout';
import hb1603Json from './fixtures/hb1603.graph.json';
import mappedJson from './fixtures/mapped.graph.json';
import uc1Json from './fixtures/uc1.graph.json';
import { declaredGraph, mappedOverInputGraph, nestedInputGraph, withSubGraph } from './helpers';
import { layoutProblems, problemCounts } from './layoutChecks';

const uc1Graph = uc1Json as unknown as Graph;
const mappedGraph = mappedJson as unknown as Graph;
const hb1603Graph = hb1603Json as unknown as Graph;
const H_LVL2 = 'inc:sub_recipes/processing_lvl_2_combined.yaml';
const LVL2 = 'inc:sub_recipes/processing_lvl_2_user.yaml';
const LVL0 = 'inc:sub_recipes/processing_lvl_0_user.yaml';
const SURVEY = 'inc:sub_recipes/processing_lvl_2_survey.yaml';
const LEAF = 'inc:sub/leaf.yaml';
/** How far an output label may be from its box, along the line into it. */
const LABEL_NEAR_BOX = 40;

const NONE = {
  coreHits: 0,
  busHits: 0,
  labelHits: 0,
  lineLabelHits: 0,
  crossings: 0,
  placement: 0,
  missing: 0,
  labelMatches: 0,
};

/** A view with nothing expanded, or with every step, box and sub-recipe box expanded. */
function viewOf(graph: Graph, drill: string[], expanded: boolean) {
  const options = {
    drill,
    connected: false,
    selected: null,
    expandedSteps: new Set<string>(),
    expandedBoxes: new Set<string>(),
  };
  const view = buildView(graph, options);
  if (!expanded) return view;
  const ids = (kinds: string[]) =>
    new Set(view.nodes.filter((node) => kinds.includes(node.kind)).map((node) => node.id));
  return buildView(graph, {
    ...options,
    expandedSteps: ids(['step']),
    expandedBoxes: ids(['include', 'inputsBox', 'outputsBox']),
  });
}

async function laidOut(graph: Graph, drill: string[], direction: 'RIGHT' | 'DOWN', expanded = false) {
  const view = viewOf(graph, drill, expanded);
  const request = layoutRequest(view, direction);
  const started = performance.now();
  const core = readLayout(await new ELK().layout(elkGraph(request)));
  const elkDone = performance.now();
  const layout = placeBuses(request, core);
  const busesDone = performance.now();
  return { view, request, layout, elkMs: elkDone - started, busMs: busesDone - elkDone };
}

const CASES: [string, Graph, string[], boolean][] = [
  ['uc1 collapsed', uc1Graph, [], false],
  ['uc1 collapsed, everything expanded', uc1Graph, [], true],
  ['uc1 drilled into processing_lvl_2_user', uc1Graph, [LVL2], false],
  ['uc1 drilled into processing_lvl_2_user, everything expanded', uc1Graph, [LVL2], true],
  ['uc1 drilled into processing_lvl_0_user', uc1Graph, [LVL0], false],
  ['uc1 drilled into processing_lvl_0_user, everything expanded', uc1Graph, [LVL0], true],
  ['with_sub with a nested input, everything expanded', nestedInputGraph(), [], true],
  ['with_sub with a nested input drilled into leaf', nestedInputGraph(), ['inc:sub/chain.yaml', LEAF], true],
  ['uc1 drilled into processing_lvl_2_survey', uc1Graph, [SURVEY], false],
  ['uc1 drilled into processing_lvl_2_survey, everything expanded', uc1Graph, [SURVEY], true],
  ['with_sub with a declared output', declaredGraph(), [], false],
  ['with_sub with every sub-recipe box expanded', withSubGraph, [], true],
  ['with_sub collapsed', withSubGraph, [], false],
  ['with_sub drilled into chain', withSubGraph, ['inc:sub/chain.yaml'], false],
  ['mapped', mappedGraph, [], false],
  ['hb1603 drilled into processing_lvl_2, its part of the per file run grouped', hb1603Graph, [H_LVL2], false],
  ['hb1603 drilled into processing_lvl_2, everything expanded', hb1603Graph, [H_LVL2], true],
  ['mapped over a recipe input, with a line into the group', mappedOverInputGraph(), [], false],
];

describe('box placement and routing', () => {
  for (const [name, graph, drill, expanded] of CASES) {
    for (const direction of ['RIGHT', 'DOWN'] as const) {
      it(`keeps lines and labels clear in ${name}, ${direction}`, async () => {
        const { view, request, layout, elkMs, busMs } = await laidOut(graph, drill, direction, expanded);
        console.log(`${name} ${direction}: ELK ${elkMs.toFixed(0)} ms, boxes ${busMs.toFixed(2)} ms`);
        const problems = layoutProblems(view, request, layout);
        expect(problemCounts(problems), JSON.stringify(problems)).toEqual(NONE);
        for (const edge of view.edges.filter((candidate) => candidate.bus)) {
          expect(layout.routes[edge.id], edge.id).toBeDefined();
        }
      });
    }
  }

  it('routes the ghost line into the inputs from the parent clear of every node', async () => {
    for (const direction of ['RIGHT', 'DOWN'] as const) {
      const { view, layout } = await laidOut(uc1Graph, [LVL2], direction);
      const ghostLine = view.edges.find((edge) => edge.id === `e:ghost:query_ncei->overrides:${LVL2}`);
      expect(ghostLine?.bus).toBe('in');
      const route = layout.routes[ghostLine!.id];
      expect(route.points.length).toBeGreaterThan(2);
      const box = layout.positions[`overrides:${LVL2}`];
      const end = route.points.at(-1)!;
      const onEdge =
        (Math.abs(end.y - box.y) < 0.5 ||
          Math.abs(end.y - (box.y + box.height)) < 0.5 ||
          Math.abs(end.x - (box.x + box.width)) < 0.5) &&
        end.x >= box.x &&
        end.x <= box.x + box.width &&
        end.y >= box.y &&
        end.y <= box.y + box.height;
      expect(onEdge, `${direction} ends at ${JSON.stringify(end)}`).toBe(true);
    }
  });

  it('names on the lane into a sub-recipe only the root inputs it is sent', async () => {
    const { request } = await laidOut(uc1Graph, [], 'RIGHT');
    const lane = request.edges.find((edge) => edge.id === `e:inputs->${LVL0}`);
    expect(lane?.label?.text.split(', ').sort()).toEqual(['cal_input_folder', 'calibration_output_dir']);
    expect(request.edges.some((edge) => edge.id === 'e:inputs->inc:sub_recipes/processing_lvl_1_per_file.yaml')).toBe(
      false,
    );
  });

  it('leaves the top edge of the inputs box left to right, however tall the box', async () => {
    for (const expanded of [false, true]) {
      const { layout } = await laidOut(uc1Graph, [], 'RIGHT', expanded);
      const box = layout.positions.inputs;
      for (const [id, route] of Object.entries(layout.routes).filter(([id]) => id.startsWith('e:inputs->'))) {
        expect(route.points[0].y, id).toBeCloseTo(box.y);
      }
    }
  });

  it('puts the inputs box beside the core left to right, clear of every lane label', async () => {
    for (const drill of [[], [LVL2], [SURVEY]]) {
      const { view, layout } = await laidOut(uc1Graph, drill, 'RIGHT');
      const boxId = view.nodes.find((node) => node.kind === 'inputsBox')!.id;
      const box = layout.positions[boxId];
      const core = view.nodes
        .filter((node) => !node.parentId && node.kind !== 'inputsBox' && node.kind !== 'outputsBox')
        .map((node) => layout.positions[node.id]);
      const coreLeft = Math.min(...core.map((position) => position.x));
      // Beside the core rather than above it: the box reaches down past its top row.
      expect(box.y + box.height, String(drill)).toBeGreaterThan(Math.min(...core.map((position) => position.y)));
      const gap = coreLeft - (box.x + box.width);
      expect(gap, String(drill)).toBeGreaterThanOrEqual(24);
      expect(gap, String(drill)).toBeLessThan(200);
      for (const [id, route] of Object.entries(layout.routes).filter(([id]) => id.startsWith(`e:${boxId}->`))) {
        expect(route.label!.x, id).toBeGreaterThan(route.points[0].x);
      }
    }
  });

  it('ends the line into a group on its edge beside its header label, clear of it', async () => {
    const label = groupLabelWidth('Runs once per item of per_file_source');
    for (const direction of ['RIGHT', 'DOWN'] as const) {
      const { request, layout } = await laidOut(hb1603Graph, [H_LVL2], direction);
      expect(request.nodes.find((node) => node.id === 'group:1')?.labelWidth).toBe(label);
      const group = layout.positions['group:1'];
      const end = layout.routes[`e:overrides:${H_LVL2}->group:1`].points.at(-1)!;
      // The header label sits 10 px in from the left and 8 px down from the top.
      if (direction === 'RIGHT') {
        expect(end.y, direction).toBeCloseTo(group.y);
        expect(end.x, direction).toBeGreaterThan(group.x + 10 + label);
        expect(end.x, direction).toBeLessThan(group.x + 10 + label + 100);
      } else {
        expect(end.x, direction).toBeCloseTo(group.x);
        expect(end.y, direction).toBeGreaterThan(group.y + 8);
        expect(end.y, direction).toBeLessThan(group.y + 28);
      }
    }
  });

  it('drops right of a column when every drop left of it is blocked', () => {
    // cap covers t's top, so no straight drop; wall fills the gap left of t.
    const core = {
      cap: { x: -10, y: 0, width: 180, height: 40 },
      wall: { x: -140, y: 0, width: 135, height: 200 },
      t: { x: 0, y: 100, width: 160, height: 60 },
    };
    const result = routeBuses({
      direction: 'RIGHT',
      core,
      containers: {},
      coreRoutes: [],
      inputsBox: { id: 'inputs', size: { width: 220, height: 44 }, targets: [{ id: 't', label: 'n' }], feeders: [] },
      outputBoxes: [],
    });
    const points = result.routes['e:inputs->t'].points;
    // The drop is the one vertical leg right of t that ends above it.
    const drops = points.filter((p, i) => i > 0 && points[i - 1].x === p.x && p.x > 160);
    expect(drops.length, JSON.stringify(points)).toBeGreaterThan(0);
    for (const [a, b] of points.slice(1).map((p, i) => [points[i], p] as const)) {
      expect(segmentHitsRect(a, b, core.cap, 0.5) || segmentHitsRect(a, b, core.wall, 0.5)).toBe(false);
    }
  });

  it('never drops along the side of a group', () => {
    // The group's header covers the member's top, so the line drops beside it.
    const result = routeBuses({
      direction: 'RIGHT',
      core: { m: { x: 0, y: 0, width: 168, height: 60 } },
      containers: { g: { box: { x: -16, y: -40, width: 200, height: 116 }, labelWidth: 300 } },
      coreRoutes: [],
      inputsBox: { id: 'inputs', size: { width: 220, height: 44 }, targets: [{ id: 'm', label: 'n' }], feeders: [] },
      outputBoxes: [],
    });
    const points = result.routes['e:inputs->m'].points;
    for (const [a, b] of points.slice(1).map((p, i) => [points[i], p] as const)) {
      const alongSide = a.x === b.x && Math.abs(a.x - -16) < 8 && Math.max(a.y, b.y) > -40;
      expect(alongSide, JSON.stringify(points)).toBe(false);
    }
    expect(points.some((p) => p.x <= -24), JSON.stringify(points)).toBe(true);
  });

  it('nests the lines of two ghosts into the inputs box', () => {
    const result = routeBuses({
      direction: 'RIGHT',
      core: {
        g1: { x: 0, y: 0, width: 140, height: 52 },
        g2: { x: 0, y: 80, width: 140, height: 52 },
        s: { x: 220, y: 0, width: 200, height: 62 },
      },
      containers: {},
      coreRoutes: [],
      inputsBox: {
        id: 'overrides:x',
        size: { width: 220, height: 44 },
        targets: [{ id: 's', label: 'a, b, c' }],
        feeders: [
          { id: 'g1', label: 'windows' },
          { id: 'g2', label: 'raw_urls' },
        ],
      },
      outputBoxes: [],
    });
    const lines = Object.values(result.routes);
    for (const route of lines) {
      for (const [a, b] of route.points.slice(1).map((p, i) => [route.points[i], p] as const)) {
        for (const other of lines) {
          if (other !== route) expect(segmentHitsRect(a, b, other.label!, 0.5)).toBe(false);
        }
      }
    }
    const [first, second] = [result.routes['e:g1->overrides:x'].points, result.routes['e:g2->overrides:x'].points];
    // Left to right the box sits below the tracks and the lower track enters its
    // top further right, so neither line crosses the other.
    expect(second.at(-1)!.x).toBeGreaterThan(first.at(-1)!.x);
    expect(second.at(-2)!.y).toBeGreaterThan(first.at(-2)!.y);
  });

  it('routes the dotted line of a box whose outputs are all unused', async () => {
    const { view, layout } = await laidOut(uc1Graph, [LVL0], 'RIGHT');
    const edge = view.edges.find((candidate) => candidate.target === 'cout:standardize_cal')!;
    expect(edge.muted).toBe(true);
    expect(layout.routes[edge.id].points.length).toBeGreaterThan(1);
  });

  it('puts each output label at its own box, naming only what the box lists', async () => {
    for (const direction of ['RIGHT', 'DOWN'] as const) {
      const { view, request, layout } = await laidOut(uc1Graph, [LVL0], direction);
      const lines = view.edges.filter((edge) => edge.bus === 'out');
      expect(lines.length).toBeGreaterThan(2);
      expect(layoutProblems(view, request, layout).labelMatches).toEqual([]);
      for (const edge of lines) {
        const label = layout.routes[edge.id].label!;
        const box = layout.positions[edge.target];
        // The label ends just before the line turns into the box, not at the producer.
        const end = direction === 'RIGHT' ? box.x + box.width / 2 : box.x;
        const gap = end - (label.x + label.width);
        expect(gap, edge.id).toBeGreaterThanOrEqual(0);
        expect(gap, edge.id).toBeLessThan(LABEL_NEAR_BOX);
      }
    }
  });

  it('still routes a line when every drop is walled off, and the checks report it', () => {
    // A wide node above the target leaves no clear straight drop or drop beside the column.
    const request: LayoutRequest = {
      direction: 'RIGHT',
      nodes: [
        { id: 'wall', width: 4000, height: 40 },
        { id: 't', width: 160, height: 60 },
        { id: 'inputs', width: 220, height: 44, bus: 'in' },
      ],
      edges: [
        { id: 'e:inputs->t', source: 'inputs', target: 't', label: { text: 'n', width: 14.5, height: 16 }, bus: 'in' },
      ],
    };
    const core = {
      positions: { wall: { x: -2000, y: 0, width: 4000, height: 40 }, t: { x: 0, y: 100, width: 160, height: 60 } },
      routes: {},
    };
    const layout = placeBuses(request, core);
    expect(layout.routes['e:inputs->t'].points.length).toBeGreaterThan(2);
    const view = {
      nodes: [],
      edges: [{ id: 'e:inputs->t', source: 'inputs', target: 't', bus: 'in', label: 'n', names: ['n'] }],
      breadcrumb: [],
    } as unknown as View;
    expect(layoutProblems(view, request, layout).busHits).toEqual(['e:inputs->t through wall']);
  });

  it('places the boxes from the core alone and never throws on missing ends', () => {
    const result = routeBuses({
      direction: 'RIGHT',
      core: { a: { x: 0, y: 0, width: 100, height: 40 } },
      containers: {},
      coreRoutes: [],
      inputsBox: {
        id: 'inputs',
        size: { width: 220, height: 44 },
        targets: [
          { id: 'a', label: 'n' },
          { id: 'missing', label: 'k' },
        ],
        feeders: [],
      },
      outputBoxes: [{ id: 'cout:a', owner: 'a', size: { width: 200, height: 40 }, label: 'out' }],
    });
    expect(Object.keys(result.routes).sort()).toEqual(['e:a->cout:a', 'e:inputs->a']);
    expect(result.positions.inputs.x + result.positions.inputs.width).toBeLessThanOrEqual(0);
    expect(result.positions['cout:a'].x).toBeGreaterThanOrEqual(100);
    expect(result.positions['cout:a'].y).toBeGreaterThanOrEqual(40);
  });
});

describe('segmentHitsRect', () => {
  const rect = { x: 0, y: 0, width: 10, height: 10 };
  it('finds segments through a rectangle and ignores ones along its edge', () => {
    expect(segmentHitsRect({ x: -5, y: 5 }, { x: 15, y: 5 }, rect)).toBe(true);
    expect(segmentHitsRect({ x: -5, y: 0 }, { x: 15, y: 0 }, rect, 0.5)).toBe(false);
    expect(segmentHitsRect({ x: 12, y: -5 }, { x: 12, y: 15 }, rect)).toBe(false);
  });
});
