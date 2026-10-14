/**
 * Run the ELK layout in a web worker, falling back to a grid.
 *
 * elkjs ships its own worker script; Vite serves it by URL and elk-api talks
 * to it. (elk.bundled.js cannot run inside a module worker of ours: it takes
 * over the worker's onmessage when it sees a worker scope.)
 */
import ELKConstructor, { type ELK } from 'elkjs/lib/elk-api';
import elkWorkerUrl from 'elkjs/lib/elk-worker.min.js?url';
import { elkGraph, gridLayout, readLayout, type Layout, type LayoutRequest } from './layout';

/** A layout slower than this is abandoned for the grid. */
const TIMEOUT_MS = 15000;

let elk: ELK | null = null;

/** Whether this environment can run the layout worker (jsdom cannot). */
export function canUseWorker(): boolean {
  return typeof Worker !== 'undefined';
}

async function elkLayout(request: LayoutRequest): Promise<Layout> {
  if (!canUseWorker()) throw new Error('No Worker in this environment');
  elk ??= new ELKConstructor({ workerUrl: elkWorkerUrl });
  let timer: number | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timer = window.setTimeout(() => reject(new Error('ELK layout timed out')), TIMEOUT_MS);
  });
  try {
    return readLayout(await Promise.race([elk.layout(elkGraph(request)), timeout]));
  } finally {
    window.clearTimeout(timer);
  }
}

/**
 * Lay out the core of a view; a worker failure gives the grid layout instead.
 *
 * The grid places every node, boxes included, and has no routes.
 */
export async function runLayout(request: LayoutRequest): Promise<Layout> {
  try {
    return await elkLayout(request);
  } catch (error) {
    if (canUseWorker()) {
      console.warn('Diagram layout failed, using a grid instead.', error);
      elk?.terminateWorker();
      elk = null;
    }
    return { positions: gridLayout(request), routes: {} };
  }
}
