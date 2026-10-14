/**
 * jsdom stand-ins that let React Flow measure and show nodes.
 *
 * React Flow keeps a node hidden until a ResizeObserver reports its size, and
 * reads the zoom through DOMMatrixReadOnly and edge label sizes through
 * getBBox; jsdom has none of these. Call installFlowStubs() at the top of a
 * test file to install them for that file.
 */
import { waitFor } from '@testing-library/react';
import { afterAll, beforeAll, expect } from 'vitest';

class MeasuringObserver {
  constructor(private callback: ResizeObserverCallback) {}
  observe(target: Element) {
    const contentRect = { width: 800, height: 600 } as DOMRectReadOnly;
    this.callback([{ target, contentRect } as ResizeObserverEntry], this as unknown as ResizeObserver);
  }
  unobserve() {}
  disconnect() {}
}

/** The zoom React Flow reads from the viewport transform; always 1 here. */
class MatrixStub {
  m22 = 1;
}

export function installFlowStubs() {
  const original = {
    observer: globalThis.ResizeObserver,
    matrix: window.DOMMatrixReadOnly,
    bbox: Object.getOwnPropertyDescriptor(SVGElement.prototype, 'getBBox'),
    width: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetWidth'),
    height: Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'offsetHeight'),
  };

  beforeAll(() => {
    globalThis.ResizeObserver = MeasuringObserver as unknown as typeof ResizeObserver;
    window.DOMMatrixReadOnly = MatrixStub as unknown as typeof DOMMatrixReadOnly;
    Object.defineProperty(SVGElement.prototype, 'getBBox', {
      configurable: true,
      value: () => ({ x: 0, y: 0, width: 40, height: 12 }),
    });
    Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => 160 });
    Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => 60 });
  });

  afterAll(() => {
    globalThis.ResizeObserver = original.observer;
    window.DOMMatrixReadOnly = original.matrix;
    const restore = (name: string, descriptor: PropertyDescriptor | undefined, proto: object) => {
      if (descriptor) Object.defineProperty(proto, name, descriptor);
      else delete (proto as Record<string, unknown>)[name];
    };
    restore('getBBox', original.bbox, SVGElement.prototype);
    restore('offsetWidth', original.width, HTMLElement.prototype);
    restore('offsetHeight', original.height, HTMLElement.prototype);
  });
}

/** Wait until the diagram's layout has placed every node. */
export async function laidOut() {
  await waitFor(() => {
    expect(document.querySelector('.react-flow__node')).not.toBeNull();
    expect(document.querySelector('.react-flow__node.rc-unplaced')).toBeNull();
  });
}

/** The rendered React Flow node element for a view node id. */
export function nodeElement(id: string): HTMLElement {
  const element = document.querySelector<HTMLElement>(`.react-flow__node[data-id="${id}"]`);
  if (!element) throw new Error(`no rendered node ${id}`);
  return element;
}
