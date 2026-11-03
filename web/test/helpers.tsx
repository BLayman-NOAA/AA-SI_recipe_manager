import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import type { Graph, GraphEdge } from '../src/api/graph';
import type { Preview, RunConfigBlock, SessionDocument, StepPreview } from '../src/api/models';
import mapped from './fixtures/mapped.graph.json';
import withSub from './fixtures/with_sub.graph.json';

/** The with_sub fixture graph (aa-recipe graph tests/fixtures/client_recipes/with_sub.yaml --json). */
export const withSubGraph = withSub as unknown as Graph;

export function testQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
}

export function renderWithClient(ui: ReactElement, client = testQueryClient()) {
  return { client, ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>) };
}

type Route = unknown | ((init: RequestInit | undefined) => unknown);

/**
 * Replace fetch with a table of "METHOD /path" routes answering JSON with 200.
 *
 * A route may also give a Response, for error statuses, or a promise (one
 * that never settles keeps the request pending). Unknown routes
 * answer 404 with an error body.
 */
export function mockApi(routes: Record<string, Route>) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString();
    const key = `${init?.method ?? 'GET'} ${url}`;
    if (!(key in routes)) {
      return new Response(JSON.stringify({ error: 'not_found', message: `No route ${key}` }), {
        status: 404,
      });
    }
    const route = routes[key];
    const body: unknown = await (typeof route === 'function' ? route(init) : route);
    if (body instanceof Response) return body;
    return new Response(body === null ? null : JSON.stringify(body), {
      status: body === null ? 204 : 200,
    });
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** An error response with the server's error body. */
export function errorResponse(status: number, message: string): Response {
  return new Response(JSON.stringify({ error: 'test_error', message }), { status });
}

/** A session document around the with_sub graph. */
export function makeSession(overrides: Partial<SessionDocument> = {}): SessionDocument {
  return {
    id: '20261013-153012-a1b2c3',
    created: '2026-10-13T15:30:12Z',
    title: 'with_sub',
    origin: 'C:/recipes/with_sub.yaml',
    read_only: true,
    version: 0,
    files: [
      { path: 'with_sub.yaml', origin: 'C:/recipes/with_sub.yaml', editable: false, dirty: false },
      { path: 'sub/chain.yaml', origin: 'C:/recipes/sub/chain.yaml', editable: false, dirty: false },
      { path: 'sub/leaf.yaml', origin: 'C:/recipes/sub/leaf.yaml', editable: false, dirty: false },
    ],
    inputs: {
      declared: [
        {
          name: 'n',
          type: 'int',
          description: 'Length of the seed list.',
          default: 3,
          required: false,
          value: 3,
          source: 'default',
        },
      ],
      values: { n: 3 },
    },
    run_config: {
      user_cache_dir: null,
      survey_cache_dir: null,
      outputs_dir: null,
      temp_dir: null,
      executor: null,
      dask_scheduler: null,
      dask_workers: null,
      storage_options: null,
      source: null,
      overrides: {},
      path: null,
      chosen: null,
      candidate: '/recipes/with_sub.config.yaml',
      error: null,
      contents: null,
      shared_with: [],
      unused_inputs: [],
    },
    graph: withSubGraph,
    validation: { valid: true, problems: [] },
    dirty: [],
    conflicts: [],
    history: {
      can_undo: false,
      can_redo: false,
      undo_kind: null,
      redo_kind: null,
      seq: 0,
      note: null,
    },
    ...overrides,
  };
}

/** The run config file a test session uses. */
export const CONFIG_PATH = 'C:/recipes/with_sub.config.yaml';

/**
 * A session's run config with a file in use, as WP7 reports it: `contents` as
 * written, empty unless overridden.
 */
export function runConfigBlock(overrides: Partial<RunConfigBlock> = {}): RunConfigBlock {
  return {
    ...makeSession().run_config,
    path: CONFIG_PATH,
    source: CONFIG_PATH,
    candidate: CONFIG_PATH,
    contents: { inputs: {}, settings: {} },
    ...overrides,
  };
}

/** A session the client may edit (schema 2 sessions): every file editable. */
export function editableSession(overrides: Partial<SessionDocument> = {}): SessionDocument {
  const base = makeSession();
  return makeSession({
    read_only: false,
    files: base.files.map((file) => ({ ...file, editable: true })),
    ...overrides,
  });
}

/** One step of a preview, computed and held nowhere unless overridden. */
export function previewStep(id: string, overrides: Partial<StepPreview> = {}): StepPreview {
  return {
    step_id: id,
    step_hash: `hash-${id}`,
    held_by: [],
    planned: 'compute',
    tier: null,
    checkpoint: 'memory-only',
    pinned: false,
    recipe_checkpoint: null,
    fanned_out: false,
    instances: null,
    instances_held: {},
    note: null,
    ...overrides,
  };
}

/** A run all preview of the with_sub recipe with these steps. */
export function makePreview(steps: StepPreview[]): Preview {
  return {
    recipe: 'client_with_sub',
    version: '1.0',
    mode: 'all',
    targets: [],
    force_steps: [],
    tiers: { user: '/u', survey: '/s' },
    write_tier: 'user',
    blockers: [],
    warnings: [],
    steps,
  };
}

/** An error response with a server error code and details. */
export function apiErrorResponse(status: number, error: string, message: string, details: unknown = null): Response {
  return new Response(JSON.stringify({ error, message, details, step_id: null }), { status });
}

/** with_sub with `total.total` declared as a recipe output, so the top level has an outputs box. */
export function declaredGraph(): Graph {
  const graph = structuredClone(withSubGraph);
  const output = graph.nodes.find((node) => node.id === 'out:total.total');
  if (output?.kind === 'output') output.declared = true;
  return graph;
}

/**
 * with_sub with an input `offset` that leaf.yaml declares and nobody
 * overrides, so the loader promotes it to the recipe; leaf_add reads it.
 */
export function nestedInputGraph(): Graph {
  const graph = structuredClone(withSubGraph);
  const declaration = { type: 'int', required: false, default: 2, description: 'Added after k.' };
  const leaf = graph.includes.find((include) => include.id === 'inc:sub/leaf.yaml')!;
  leaf.inputs = [...(leaf.inputs ?? []), { name: 'offset', ...declaration }];
  graph.nodes.unshift({
    id: 'in:offset',
    kind: 'input',
    label: 'inputs.offset',
    declared: true,
    ...declaration,
    declared_in: 'sub/leaf.yaml',
  });
  graph.edges.push({
    source: 'in:offset',
    target: 'leaf_add',
    kind: 'param',
    source_output: null,
    target_input: 'offset',
    label: 'offset',
    type: 'int',
  });
  return graph;
}

/** mapped with a recipe input that seg maps over and proc reads. */
export function mappedInputGraph(): Graph {
  const graph = structuredClone(mapped as unknown as Graph);
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

/** mapped with its group over a recipe input, `files`, which proc also reads as a param. */
export function mappedOverInputGraph(): Graph {
  const graph = mappedInputGraph();
  const group = graph.groups[0];
  group.map_over = '${inputs.files}';
  group.label = 'Runs once per item of inputs.files';
  graph.edges.push({
    source: 'in:files',
    target: 'proc',
    kind: 'param',
    source_output: null,
    target_input: 'names',
    label: 'files',
    type: 'list',
  } as GraphEdge);
  return graph;
}
