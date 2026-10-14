// ELK runs here through elkjs's bundled build, in thread; the client runs the
// same graph in elkjs's worker (src/diagram/elk.ts).
import ELK from 'elkjs/lib/elk.bundled.js';
import { describe, expect, it } from 'vitest';
import type { Graph, StepNode } from '../src/api/graph';
import { buildView, type View } from '../src/diagram/buildView';
import {
  coreNodes,
  elkGraph,
  estimateSize,
  gridLayout,
  layoutRequest,
  MAX_WIDTH,
  MIN_WIDTH,
  nodeLines,
  readLayout,
  readPositions,
  SIDE_PAD,
  textWidth,
  type Positions,
} from '../src/diagram/layout';
import mappedJson from './fixtures/mapped.graph.json';
import uc1Json from './fixtures/uc1.graph.json';
import { declaredGraph, withSubGraph } from './helpers';

const mappedGraph = mappedJson as unknown as Graph;
const uc1Graph = uc1Json as unknown as Graph;

function view(graph: Graph, drill: string[] = [], expanded = false): View {
  const options = {
    drill,
    connected: false,
    selected: null,
    expandedSteps: new Set<string>(),
    expandedBoxes: new Set<string>(),
  };
  const plain = buildView(graph, options);
  if (!expanded) return plain;
  const ids = (kinds: string[]) =>
    new Set(plain.nodes.filter((node) => kinds.includes(node.kind)).map((node) => node.id));
  return buildView(graph, {
    ...options,
    expandedSteps: ids(['step']),
    expandedBoxes: ids(['include', 'inputsBox', 'outputsBox']),
  });
}

describe('node sizes', () => {
  it('sizes every node to fit its content between its least and most width', () => {
    const drills = [[], ...uc1Graph.includes.map((include) => [include.id])];
    for (const drill of drills) {
      for (const node of view(uc1Graph, drill, true).nodes) {
        if (!(node.kind in MIN_WIDTH)) continue;
        const kind = node.kind as keyof typeof MIN_WIDTH;
        const { width } = estimateSize(node);
        expect(width, node.id).toBeGreaterThanOrEqual(MIN_WIDTH[kind]);
        expect(width, node.id).toBeLessThanOrEqual(MAX_WIDTH[kind]);
        // Lines that neither wrap nor clip, and every indent, fit inside the node.
        for (const line of nodeLines(node)) {
          const room = width - SIDE_PAD[kind];
          expect(line.indent, `${node.id}: ${line.text}`).toBeLessThanOrEqual(room);
          if (line.overflow === 'none') {
            expect(line.indent + textWidth(line.text), `${node.id}: ${line.text}`).toBeLessThanOrEqual(room);
          }
        }
      }
    }
  });

  it('gives a step with a very long value its most width, and taller as it wraps', () => {
    const graph = structuredClone(withSubGraph);
    const seed = graph.nodes.find((node) => node.id === 'seed') as StepNode;
    const short = estimateSize(view(graph, [], true).nodes.find((node) => node.id === 'seed')!);
    seed.params[0] = { ...seed.params[0], value: 'x'.repeat(273), set: true };
    const long = estimateSize(view(graph, [], true).nodes.find((node) => node.id === 'seed')!);
    expect(long.width).toBe(MAX_WIDTH.step);
    expect(long.height).toBeGreaterThan(short.height);
  });
});

describe('layout request', () => {
  it('leaves the boxes and their lines out of the core', () => {
    const request = layoutRequest(view(declaredGraph()), 'RIGHT');
    expect(request.nodes.find((node) => node.id === 'inputs')?.bus).toBe('in');
    expect(request.nodes.find((node) => node.id === 'cout:total')?.bus).toBe('out');
    const core = coreNodes(request);
    expect(core.nodes.map((node) => node.id)).not.toContain('inputs');
    expect(core.edges.every((edge) => !edge.bus)).toBe(true);
    expect(elkGraph(request).children!.map((child) => child.id)).not.toContain('cout:total');
  });

  it('gives leaf nodes fixed ports where their handles are, and edges labels with sizes', () => {
    const request = layoutRequest(view(withSubGraph), 'RIGHT');
    const chain = request.nodes.find((node) => node.id === 'inc:sub/chain.yaml')!;
    expect(chain.ports).toEqual([
      { id: 'in:x@seed', side: 'in', fraction: 0.5 },
      { id: 'out:leaf_add.out', side: 'out', fraction: 0.5 },
    ]);
    const graph = elkGraph(request);
    const elkChain = graph.children!.find((child) => child.id === chain.id)!;
    expect(elkChain.layoutOptions).toEqual({ 'elk.portConstraints': 'FIXED_POS' });
    expect(elkChain.ports).toContainEqual(
      expect.objectContaining({ id: 'inc:sub/chain.yaml.in:x@seed', x: 0, y: chain.height / 2 - 0.5 }),
    );
    expect(elkChain.ports).toContainEqual(
      expect.objectContaining({
        id: 'inc:sub/chain.yaml.out:leaf_add.out',
        x: chain.width - 1,
        y: chain.height / 2 - 0.5,
      }),
    );
    const edge = graph.edges!.find((candidate) => candidate.id === 'e:seed->inc:sub/chain.yaml')!;
    expect(edge.sources).toEqual(['seed.out:items']);
    expect(edge.targets).toEqual(['inc:sub/chain.yaml.in:x@seed']);
    expect(edge.labels).toEqual([{ text: 'items', width: 'items'.length * 6.5 + 8, height: 16 }]);
  });

  it('puts ports on the top and bottom sides top to bottom', () => {
    const request = layoutRequest(view(withSubGraph), 'DOWN');
    const seed = request.nodes.find((node) => node.id === 'seed')!;
    const port = elkGraph(request).children!.find((child) => child.id === 'seed')!.ports![0];
    expect(port).toMatchObject({ id: 'seed.out:items', x: seed.width / 2 - 0.5, y: seed.height - 1 });
  });
});

describe('ELK layout', () => {
  it('places every core node, containers enclosing their children', async () => {
    const mapped = view(mappedGraph);
    for (const direction of ['RIGHT', 'DOWN'] as const) {
      const request = layoutRequest(mapped, direction);
      const positions: Positions = readPositions(await new ELK().layout(elkGraph(request)));
      for (const node of coreNodes(request).nodes) {
        const box = positions[node.id];
        expect(box, node.id).toBeDefined();
        expect(box.width, node.id).toBeGreaterThan(0);
        if (!node.parent) continue;
        const parent = positions[node.parent];
        expect(box.x + box.width, node.id).toBeLessThanOrEqual(parent.width);
        expect(box.y + box.height, node.id).toBeLessThanOrEqual(parent.height);
      }
    }
  });

  it('reads routes in absolute coordinates that end on the ports', async () => {
    const request = layoutRequest(view(mappedGraph), 'RIGHT');
    const layout = readLayout(await new ELK().layout(elkGraph(request)));
    const group = layout.positions['group:0'];
    const proc = layout.positions.proc;
    const proc2 = layout.positions.proc2;
    // An edge inside the group comes back relative to it and is shifted to absolute.
    const inner = layout.routes['e:proc->proc2'];
    expect(inner.points[0].x).toBeCloseTo(group.x + proc.x + proc.width, 0);
    expect(inner.points.at(-1)!.x).toBeCloseTo(group.x + proc2.x, 0);
    // An edge from the root into a group child.
    const into = layout.routes['e:seg->proc'];
    expect(into.points.at(-1)!.x).toBeCloseTo(group.x + proc.x, 0);
    expect(into.points.at(-1)!.y).toBeCloseTo(group.y + proc.y + proc.height / 2, 0);
    expect(into.label).toMatchObject({ width: expect.any(Number), height: 16 });
  });
});

describe('gridLayout', () => {
  it('places every node, boxes included, containers sized around their children', () => {
    const mapped = view(mappedGraph);
    const positions = gridLayout(layoutRequest(mapped, 'RIGHT'));
    expect(Object.keys(positions).sort()).toEqual(mapped.nodes.map((node) => node.id).sort());
    const group = positions['group:0'];
    const proc2 = positions.proc2;
    expect(group.width).toBeGreaterThan(proc2.x + proc2.width);
  });
});
