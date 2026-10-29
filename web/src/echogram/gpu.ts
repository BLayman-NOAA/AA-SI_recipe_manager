/** The page's one WebGPU context, shared by every time the echogram pane opens. */
import { createGpuContext, type GpuContext } from 'aa-si-echogram-gl';

let context: Promise<GpuContext> | null = null;

/** Create the context once; a failure is remembered, since retrying will not help. */
export function getGpuContext(): Promise<GpuContext> {
  context ??= createGpuContext();
  return context;
}

/** Forget the context, for tests. */
export function resetGpuContext(): void {
  context = null;
}
