import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { vi } from 'vitest';
import type { Graph } from '../src/api/graph';
import type { SessionDocument } from '../src/api/models';
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
      { path: 'with_sub.yaml', origin: 'C:/recipes/with_sub.yaml', editable: false },
      { path: 'sub/chain.yaml', origin: 'C:/recipes/sub/chain.yaml', editable: false },
      { path: 'sub/leaf.yaml', origin: 'C:/recipes/sub/leaf.yaml', editable: false },
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
    },
    graph: withSubGraph,
    validation: { valid: true, problems: [] },
    ...overrides,
  };
}

/** with_sub with `total.total` declared as a recipe output, so the top level has an outputs box. */
export function declaredGraph(): Graph {
  const graph = structuredClone(withSubGraph);
  const output = graph.nodes.find((node) => node.id === 'out:total.total');
  if (output?.kind === 'output') output.declared = true;
  return graph;
}
