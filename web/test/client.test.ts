import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  ApiError,
  apiGet,
  apiSend,
  isClientError,
  isNetworkError,
  NETWORK_ERROR_STATUS,
} from '../src/api/client';

function respond(body: string | null, status = 200) {
  const fetchMock = vi.fn(async () => new Response(body, { status }));
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** The ApiError a request rejects with. */
async function failure(request: Promise<unknown>): Promise<ApiError> {
  try {
    await request;
  } catch (error) {
    return error as ApiError;
  }
  throw new Error('The request did not fail.');
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('apiSend', () => {
  it('sends the client header and a JSON body', async () => {
    const fetchMock = respond(JSON.stringify({ id: 'abc' }), 201);
    const result = await apiSend<{ id: string }>('POST', '/api/sessions', { path: 'a.yaml' });

    expect(result).toEqual({ id: 'abc' });
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(path).toBe('/api/sessions');
    expect(init.method).toBe('POST');
    const headers = init.headers as Record<string, string>;
    expect(headers['X-Recipe-Client']).toBe('1');
    expect(headers['Content-Type']).toBe('application/json');
    expect(JSON.parse(String(init.body))).toEqual({ path: 'a.yaml' });
  });

  it('returns null for an empty 204 response', async () => {
    respond(null, 204);
    await expect(apiSend('DELETE', '/api/sessions/x')).resolves.toBeNull();
  });

  it('turns an error body into an ApiError', async () => {
    respond(
      JSON.stringify({
        error: 'RecipeValidationError',
        message: 'Step bump is missing x.',
        details: ['x'],
        step_id: 'bump',
      }),
      422,
    );
    const error = await failure(apiSend('PUT', '/api/sessions/x/inputs', { values: {} }));

    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(422);
    expect(error.error).toBe('RecipeValidationError');
    expect(error.message).toBe('Step bump is missing x.');
    expect(error.details).toEqual(['x']);
    expect(error.stepId).toBe('bump');
  });
});

describe('apiGet', () => {
  it('does not send the mutation header', async () => {
    const fetchMock = respond(JSON.stringify({ status: 'ok' }));
    await expect(apiGet('/api/health')).resolves.toEqual({ status: 'ok' });
    const init = (fetchMock.mock.calls[0] as unknown as [string, RequestInit])[1];
    expect((init.headers as Record<string, string>)['X-Recipe-Client']).toBeUndefined();
  });

  it('reports a non JSON failure as an http_error', async () => {
    respond('Bad gateway', 502);
    const error = await failure(apiGet('/api/health'));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(502);
    expect(error.error).toBe('http_error');
    expect(error.message).toBe('Bad gateway');
  });

  it('reads FastAPI detail bodies', async () => {
    respond(JSON.stringify({ detail: 'Not Found' }), 404);
    const error = await failure(apiGet('/api/nope'));
    expect(error.status).toBe(404);
    expect(error.message).toBe('Not Found');
  });
});

describe('network failures', () => {
  it('become an ApiError that says the server cannot be reached', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => Promise.reject(new TypeError('Failed to fetch'))));
    const error = await failure(apiGet('/api/recipes'));
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(NETWORK_ERROR_STATUS);
    expect(error.message).toBe('Cannot reach the recipe server. Is aa-recipe serve running?');
    expect(isNetworkError(error)).toBe(true);
    expect(isClientError(error)).toBe(false);
    expect(isClientError(new ApiError(409, 'conflict', 'x'))).toBe(true);
  });
});
