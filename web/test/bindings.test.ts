import { describe, expect, it } from 'vitest';
import { findInclude, findStep, type Graph, type StepNode } from '../src/api/graph';
import type { InputSource } from '../src/api/models';
import {
  bindingTarget,
  includeReach,
  includerFile,
  inputReaders,
  overriddenNote,
  paramBinding,
  runSetNote,
  stepReach,
  targetOperation,
  targetReaders,
  targetText,
} from '../src/inspector/bindings';
import { nestedInputGraph, withSubGraph } from './helpers';

const LEAF = 'inc:sub/leaf.yaml';
const CHAIN = 'inc:sub/chain.yaml';

function step(graph: Graph, id: string): StepNode {
  return findStep(graph, id)!;
}

describe('paramBinding', () => {
  it('binds leaf_add.k to the override k of its nearest include, from the bindings', () => {
    const binding = paramBinding(withSubGraph, step(withSubGraph, 'leaf_add'), 'k');
    expect(binding?.kind).toBe('override');
    if (binding?.kind !== 'override') return;
    expect(binding.include.id).toBe(LEAF);
    expect(binding.name).toBe('k');
    expect(binding.include.bindings?.k).toEqual([{ step: 'leaf_add', slot: 'params.k' }]);
    // The leaf entry passes on chain.yaml's input k, which the chain entry sets to 5.
    expect(binding.passesOn?.kind).toBe('override');
    if (binding.passesOn?.kind !== 'override') return;
    expect(binding.passesOn.include.id).toBe(CHAIN);
    expect(binding.passesOn.passesOn).toBeNull();
  });

  it('binds seed.n to the recipe input n of the root file', () => {
    const binding = paramBinding(withSubGraph, step(withSubGraph, 'seed'), 'n', { n: 'config' });
    expect(binding).toEqual({
      kind: 'input',
      name: 'n',
      declaredIn: 'with_sub.yaml',
      source: 'config',
      default: 3,
    });
  });

  it('binds leaf_add.offset to the input offset declared in sub/leaf.yaml', () => {
    const graph = nestedInputGraph();
    const leaf = step(graph, 'leaf_add');
    leaf.params.push({ ...leaf.params[0], name: 'offset', value: '${inputs.offset}' });
    const binding = paramBinding(graph, leaf, 'offset');
    expect(binding).toMatchObject({ kind: 'input', name: 'offset', declaredIn: 'sub/leaf.yaml' });
  });

  it('finds no binding for a parameter holding its own value', () => {
    const graph = structuredClone(withSubGraph);
    const seed = step(graph, 'seed');
    seed.params.push({ ...seed.params[0], name: 'size', value: 4 });
    expect(paramBinding(graph, seed, 'size')).toBeNull();
  });
});

describe('bindingTarget', () => {
  it('follows overrides that pass on an input to the override that holds the value', () => {
    const leaf = step(withSubGraph, 'leaf_add');
    const target = bindingTarget(paramBinding(withSubGraph, leaf, 'k')!);
    expect(target).toMatchObject({ kind: 'override', include: { id: CHAIN }, name: 'k', value: 5 });
    expect(targetText(withSubGraph, target)).toBe('Set in with_sub.yaml (override of sub/chain.yaml)');
    expect(targetOperation(target, 7)).toEqual({
      kind: 'set_include_override',
      include: 'sub/chain.yaml',
      name: 'k',
      value: 7,
      position: null,
    });
    expect(targetReaders(withSubGraph, target)).toEqual(['leaf_add']);
  });

  it('ends at the default, the session value or the run config value of a recipe input', () => {
    const seed = step(withSubGraph, 'seed');
    const target = (source: InputSource) =>
      bindingTarget(paramBinding(withSubGraph, seed, 'n', { n: source })!, { n: 6 });
    const fallback = target('default');
    expect(fallback).toEqual({ kind: 'default', name: 'n', declaredIn: 'with_sub.yaml', value: 3 });
    expect(targetText(withSubGraph, fallback)).toBe('Default of input n in with_sub.yaml');
    expect(targetOperation(fallback, 4)).toEqual({
      kind: 'set_recipe_input',
      document: 'with_sub.yaml',
      name: 'n',
      field: 'default',
      value: 4,
      position: null,
    });
    const session = target('session');
    expect(session).toMatchObject({ kind: 'session', value: 6 });
    expect(targetText(withSubGraph, session)).toBe('Set in this session');
    expect(targetOperation(session, 4)).toBeNull();
    const config = target('config');
    expect(targetText(withSubGraph, config, 'flat.config.yaml')).toBe(
      'Set in flat.config.yaml; edit it in the Run Config pane',
    );
    expect(targetOperation(config, 4)).toBeNull();
  });

  it('follows an override that passes on a recipe input to its default, and counts every step it feeds', () => {
    const graph = structuredClone(withSubGraph);
    findInclude(graph, CHAIN)!.overrides.k = '${inputs.n}';
    const target = bindingTarget(paramBinding(graph, step(graph, 'leaf_add'), 'k')!);
    expect(target).toEqual({ kind: 'default', name: 'n', declaredIn: 'with_sub.yaml', value: 3 });
    expect(targetReaders(graph, target)).toEqual(['seed', 'leaf_add']);
    expect(inputReaders(withSubGraph, 'n', 'with_sub.yaml')).toEqual(['seed']);
  });

  it('keeps the notes for input defaults set elsewhere', () => {
    expect(overriddenNote(4)).toBe(
      'The parent sets this input where it includes the file (override: 4), so the default is not used. ' +
        'Edit the override instead.',
    );
    expect(runSetNote('config')).toBe('The run config sets this input, so its default is not used while it does.');
    expect(runSetNote('default')).toBeNull();
  });
});

describe('includerFile', () => {
  it('names the file holding an include entry', () => {
    expect(includerFile(withSubGraph, findInclude(withSubGraph, CHAIN)!)).toBe('with_sub.yaml');
    expect(includerFile(withSubGraph, findInclude(withSubGraph, LEAF)!)).toBe('sub/chain.yaml');
  });
});

describe('reach', () => {
  it('counts the other steps a change in a step form feeds', () => {
    expect(stepReach('seed', ['seed'])).toEqual({ lead: '0 other steps', others: [], total: 1 });
    expect(stepReach('seed', ['seed', 'total'])).toEqual({ lead: '1 other step', others: ['total'], total: 2 });
    expect(stepReach('seed', ['a', 'b']).lead).toBe('2 other steps');
  });

  it('counts the steps outside a sub-recipe its change feeds', () => {
    const leaf = findInclude(withSubGraph, LEAF)!;
    expect(includeReach(leaf, ['leaf_add'])).toEqual({ lead: '0 steps outside client_leaf', others: [], total: 1 });
    expect(includeReach(leaf, ['leaf_add', 'total'])).toMatchObject({ lead: '1 step outside client_leaf', total: 2 });
  });
});
