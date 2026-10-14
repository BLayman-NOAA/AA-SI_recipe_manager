// The UC1 fixture is trimmed of descriptions and hashes; regenerate it from the
// UC1 folder (AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1) with
//   aa-recipe graph dive_analysis.yaml --json | node ../../../../AA-SI_recipe_manager/web/scripts/trim-graph.mjs > ../../../../AA-SI_recipe_manager/web/test/fixtures/uc1.graph.json
// ELK runs in thread through elkjs's bundled build, as in layout.test.ts.
import ELK from 'elkjs/lib/elk.bundled.js';
import { describe, expect, it } from 'vitest';
import type { Graph } from '../src/api/graph';
import { buildView } from '../src/diagram/buildView';
import { placeBuses, routeBuses, segmentHitsRect } from '../src/diagram/buses';
import type { View } from '../src/diagram/buildView';
import { elkGraph, layoutRequest, readLayout, type LayoutRequest } from '../src/diagram/layout';
import mappedJson from './fixtures/mapped.graph.json';
import uc1Json from './fixtures/uc1.graph.json';
import { declaredGraph, withSubGraph } from './helpers';
import { layoutProblems, problemCounts } from './layoutChecks';

const uc1Graph = uc1Json as unknown as Graph;
const mappedGraph = mappedJson as unknown as Graph;
const LVL2 = 'inc:sub_recipes/processing_lvl_2_user.yaml';
const LVL0 = 'inc:sub_recipes/processing_lvl_0_user.yaml';
const SURVEY = 'inc:sub_recipes/processing_lvl_2_survey.yaml';
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
  ['uc1 drilled into processing_lvl_2_survey', uc1Graph, [SURVEY], false],
  ['uc1 drilled into processing_lvl_2_survey, everything expanded', uc1Graph, [SURVEY], true],
  ['with_sub with a declared output', declaredGraph(), [], false],
  ['with_sub with every sub-recipe box expanded', withSubGraph, [], true],
  ['with_sub collapsed', withSubGraph, [], false],
  ['with_sub drilled into chain', withSubGraph, ['inc:sub/chain.yaml'], false],
  ['mapped', mappedGraph, [], false],
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
        (Math.abs(end.y - (box.y + box.height)) < 0.5 || Math.abs(end.x - (box.x + box.width)) < 0.5) &&
        end.x >= box.x &&
        end.x <= box.x + box.width &&
        end.y >= box.y &&
        end.y <= box.y + box.height;
      expect(onEdge, `${direction} ends at ${JSON.stringify(end)}`).toBe(true);
    }
  });

  it('leaves the bottom edge of the inputs box left to right, however tall the box', async () => {
    for (const expanded of [false, true]) {
      const { layout } = await laidOut(uc1Graph, [], 'RIGHT', expanded);
      const box = layout.positions.inputs;
      for (const [id, route] of Object.entries(layout.routes).filter(([id]) => id.startsWith('e:inputs->'))) {
        expect(route.points[0].y, id).toBeCloseTo(box.y + box.height);
      }
    }
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
    // The lower track enters the box further left, so neither line crosses the other.
    expect(second.at(-1)!.x).toBeLessThan(first.at(-1)!.x);
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
