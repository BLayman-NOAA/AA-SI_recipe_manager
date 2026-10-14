/** Fetch wrapper for the recipe manager server API. */

/** An error response from the server, or a failed request. */
export class ApiError extends Error {
  readonly status: number;
  readonly error: string;
  readonly details: unknown;
  readonly stepId: string | null;

  constructor(
    status: number,
    error: string,
    message: string,
    details: unknown = null,
    stepId: string | null = null,
  ) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.error = error;
    this.details = details;
    this.stepId = stepId;
  }
}

/** Status of an ApiError for a request that never reached the server. */
export const NETWORK_ERROR_STATUS = 0;

export const NETWORK_ERROR_MESSAGE = 'Cannot reach the recipe server. Is aa-recipe serve running?';

/** A request the server answered with a 4xx: retrying will not help. */
export function isClientError(error: unknown): boolean {
  return error instanceof ApiError && error.status >= 400 && error.status < 500;
}

/** A request that never reached the server. */
export function isNetworkError(error: unknown): boolean {
  return error instanceof ApiError && error.status === NETWORK_ERROR_STATUS;
}

/** fetch, with a network failure (a TypeError) turned into a clear ApiError. */
async function send(path: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(path, init);
  } catch (error) {
    if (error instanceof TypeError) {
      throw new ApiError(NETWORK_ERROR_STATUS, 'network_error', NETWORK_ERROR_MESSAGE);
    }
    throw error;
  }
}

/** HTTP methods that change server state. */
export type MutatingMethod = 'POST' | 'PUT' | 'DELETE' | 'PATCH';

/** GET a JSON document. */
export async function apiGet<T>(path: string): Promise<T> {
  const response = await send(path, { headers: { Accept: 'application/json' } });
  return readResponse<T>(response);
}

/** Send a mutating request with the header the server requires. */
export async function apiSend<T>(
  method: MutatingMethod,
  path: string,
  body?: unknown,
): Promise<T> {
  const response = await send(path, {
    method,
    headers: {
      Accept: 'application/json',
      'Content-Type': 'application/json',
      'X-Recipe-Client': '1',
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return readResponse<T>(response);
}

async function readResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  let data: unknown = null;
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = text;
    }
  }
  if (!response.ok) {
    throw toApiError(response.status, response.statusText, data);
  }
  return data as T;
}

/** Turn an error body into an ApiError; the server sends {error, message, details, step_id}. */
export function toApiError(status: number, statusText: string, data: unknown): ApiError {
  if (data && typeof data === 'object' && 'error' in data) {
    const body = data as {
      error: string;
      message?: string;
      details?: unknown;
      step_id?: string | null;
    };
    return new ApiError(
      status,
      body.error,
      body.message ?? statusText,
      body.details ?? null,
      body.step_id ?? null,
    );
  }
  if (data && typeof data === 'object' && 'detail' in data) {
    const detail = (data as { detail: unknown }).detail;
    const message = typeof detail === 'string' ? detail : statusText || 'Request failed';
    return new ApiError(status, 'http_error', message, detail);
  }
  const message = typeof data === 'string' && data ? data : statusText || 'Request failed';
  return new ApiError(status, 'http_error', message);
}
