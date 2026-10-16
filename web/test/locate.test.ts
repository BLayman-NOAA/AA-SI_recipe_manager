import { beforeEach, describe, expect, it } from 'vitest';
import { includeChain, includePath, selectStep } from '../src/diagram/locate';
import { buildView } from '../src/diagram/buildView';
import { useView } from '../src/state/view';
import { withSubGraph } from './helpers';

const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';

beforeEach(() => {
  useView.getState().resetScope();
});

describe('includeChain', () => {
  it('lists the includes around a step, outermost first', () => {
    expect(includeChain(withSubGraph, 'leaf_add')).toEqual([CHAIN, LEAF]);
    expect(includeChain(withSubGraph, 'chain_inc')).toEqual([CHAIN]);
    expect(includeChain(withSubGraph, 'seed')).toEqual([]);
  });

  it('gives the drill path into an include', () => {
    expect(includePath(withSubGraph, LEAF)).toEqual([CHAIN, LEAF]);
    expect(includePath(withSubGraph, 'inc:nope.yaml')).toEqual([]);
  });
});

describe('the drill path that shows a step', () => {
  it('drills into every include around a nested step and needs none for a root step', () => {
    expect(includeChain(withSubGraph, 'leaf_add')).toEqual([CHAIN, LEAF]);
    expect(includeChain(withSubGraph, 'seed')).toEqual([]);
  });

  it('makes the step a visible node of the view', () => {
    const view = buildView(withSubGraph, {
      drill: includeChain(withSubGraph, 'leaf_add'),
      connected: false,
      selected: null,
      expandedSteps: new Set(),
      expandedBoxes: new Set(),
    });
    expect(view.nodes.find((node) => node.id === 'leaf_add')?.kind).toBe('step');
  });
});

describe('selectStep', () => {
  it('sets the drill path and selects the step', () => {
    selectStep(useView, withSubGraph, 'leaf_add');
    expect(useView.getState().drill).toEqual([CHAIN, LEAF]);
    expect(useView.getState().selected).toEqual({ kind: 'step', id: 'leaf_add' });
    selectStep(useView, withSubGraph, 'seed');
    expect(useView.getState().drill).toEqual([]);
  });
});
