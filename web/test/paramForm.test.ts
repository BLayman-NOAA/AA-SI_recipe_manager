import { describe, expect, it } from 'vitest';
import { ApiError, editProblems } from '../src/api/client';
import { findInclude, type GraphInclude, type GraphParam, type StepNode } from '../src/api/graph';
import { fileEditable } from '../src/api/models';
import type { BindingTarget } from '../src/inspector/bindings';
import {
  constraintNotes,
  controlForParam,
  defaultOperations,
  exclusiveErrors,
  exclusiveWith,
  formValue,
  isUndeclared,
  overrideOperations,
  paramDraft,
  paramKind,
  paramOperations,
  paramProblems,
  parseValue,
  sessionValues,
  valueType,
} from '../src/inspector/paramForm';
import { editableSession, makeSession, withSubGraph } from './helpers';

function param(name: string, type: string | null, value: unknown, overrides: Partial<GraphParam> = {}): GraphParam {
  return {
    name,
    type,
    units: null,
    description: null,
    default: null,
    set: value !== undefined,
    value: value ?? null,
    required: false,
    constraints: {},
    fingerprint_mode: 'off',
    ...overrides,
  };
}

/** The override n of sub/chain.yaml as a binding target. */
function overrideTarget(value: unknown): BindingTarget {
  const include = findInclude(withSubGraph, 'inc:sub/chain.yaml')!;
  return { kind: 'override', include, name: 'n', value };
}

/** The root recipe input n as a binding target of a kind. */
function inputTarget(kind: 'default' | 'session' | 'config', value: unknown): BindingTarget {
  return { kind, name: 'n', declaredIn: 'with_sub.yaml', value };
}

function stepWith(params: GraphParam[]): StepNode {
  const leaf = withSubGraph.nodes.find((node) => node.id === 'leaf_add') as StepNode;
  return { ...leaf, params };
}

describe('controlForParam', () => {
  it('picks a control per type', () => {
    expect(controlForParam('int')).toBe('number');
    expect(controlForParam('float')).toBe('number');
    expect(controlForParam('bool')).toBe('checkbox');
    expect(controlForParam('list')).toBe('json');
    expect(controlForParam('dict')).toBe('json');
    expect(controlForParam('object')).toBe('json');
    expect(controlForParam('path')).toBe('text');
    expect(controlForParam('str')).toBe('text');
    expect(controlForParam(null)).toBe('text');
    expect(controlForParam('DataArray')).toBe('text');
  });
});

describe('parseValue', () => {
  it('parses each type and refuses what does not fit', () => {
    expect(parseValue('2.5', 'float')).toEqual({ ok: true, value: 2.5 });
    expect(parseValue('2.5', 'int')).toEqual({ ok: false, error: 'Enter a whole number.' });
    expect(parseValue(true, 'bool')).toEqual({ ok: true, value: true });
    expect(parseValue('[1, 2]', 'list')).toEqual({ ok: true, value: [1, 2] });
    expect(parseValue('{"a": 1}', 'dict')).toEqual({ ok: true, value: { a: 1 } });
    expect(parseValue('"x"', 'object')).toEqual({ ok: true, value: 'x' });
    expect(parseValue('{', 'object')).toEqual({ ok: false, error: 'Not valid JSON.' });
    expect(parseValue('/data/raw', 'path')).toEqual({ ok: true, value: '/data/raw' });
  });

  it('never gives NaN or infinity', () => {
    expect(parseValue('.nan', 'float').ok).toBe(false);
    expect(parseValue('Infinity', 'float').ok).toBe(false);
    expect(parseValue('1e999', 'float').ok).toBe(false);
    expect(parseValue('[1, ".inf"]', 'list')).toEqual({
      ok: false,
      error: 'NaN and infinity can only be written in the YAML pane.',
    });
  });

  it('keeps a reference as text whatever the type', () => {
    expect(parseValue('${inputs.k}', 'int')).toEqual({ ok: true, value: '${inputs.k}' });
  });
});

describe('paramKind and paramDraft', () => {
  it('shows a wire, a run config value, or a value control for the value or its target', () => {
    expect(paramKind(param('x', 'int', '${seed.items}'))).toBe('wired');
    expect(paramKind(param('x', 'int', '${_item}'))).toBe('wired');
    expect(paramKind(param('t', 'float', '.nan'))).toBe('text');
    expect(paramKind(param('t', 'float', 1.5))).toBe('number');
    const bound = param('k', 'int', '${inputs.k}');
    expect(paramKind(bound, inputTarget('default', 5))).toBe('number');
    expect(paramKind(bound, inputTarget('config', 5))).toBe('config');
    expect(paramKind(bound, overrideTarget('${seed.items}'))).toBe('wired');
    expect(paramKind(bound, overrideTarget('${inputs.y}'))).toBe('text');
    expect(paramDraft(bound, undefined, inputTarget('session', 6))).toBe('6');
  });

  it('shows the value, else the default, else the edit', () => {
    const field = param('t', 'float', undefined, { default: 3 });
    expect(paramDraft(field, undefined)).toBe('3');
    expect(paramDraft(param('t', 'float', 5), undefined)).toBe('5');
    expect(paramDraft(param('t', 'float', 5, { default: 3 }), { reset: true })).toBe('3');
    expect(paramDraft(field, { draft: '9' })).toBe('9');
    expect(paramDraft(param('l', 'list', [1]), undefined)).toBe('[\n  1\n]');
  });
});

describe('an undeclared parameter', () => {
  it('has no fingerprint mode and is edited as the type of its value', () => {
    const field = param('extra', null, 3, { fingerprint_mode: null });
    expect(isUndeclared(field)).toBe(true);
    expect(isUndeclared(param('k', 'int', 5))).toBe(false);
    expect(valueType(null, 3)).toBe('int');
    expect(valueType(null, 0.5)).toBe('float');
    expect(valueType(null, true)).toBe('bool');
    expect(valueType(null, [1])).toBe('list');
    expect(valueType(null, { a: 1 })).toBe('dict');
    expect(valueType(null, 'x')).toBe('str');
    expect(paramKind(field)).toBe('number');
    const step = stepWith([field]);
    expect(paramOperations(step, { extra: { draft: '4' } }).operations).toEqual([
      { kind: 'set_param', step: 'leaf_add', name: 'extra', value: 4, path: [], position: null },
    ]);
  });
});

describe('paramOperations', () => {
  const step = stepWith([
    param('n', 'int', 3),
    param('gain', 'float', 5, { units: 'dB' }),
    param('flag', 'bool', undefined, { default: false }),
    param('items', 'list', [1]),
    param('options', 'dict', { a: 1 }),
    param('thing', 'object', 'x'),
    param('out', 'path', '/a'),
    param('label', 'str', 'old'),
  ]);

  it('sets each changed parameter with a value of its type', () => {
    const plan = paramOperations(step, {
      n: { draft: '7' },
      gain: { draft: '2.5' },
      flag: { draft: true },
      items: { draft: '[1, 2]' },
      options: { draft: '{"b": 2}' },
      thing: { draft: '{"c": [3]}' },
      out: { draft: '/b' },
      label: { draft: 'new' },
    });
    expect(plan.errors).toEqual({});
    expect(plan.operations.map((op) => op.kind === 'set_param' && [op.name, op.value])).toEqual([
      ['n', 7],
      ['gain', 2.5],
      ['flag', true],
      ['items', [1, 2]],
      ['options', { b: 2 }],
      ['thing', { c: [3] }],
      ['out', '/b'],
      ['label', 'new'],
    ]);
  });

  it('sends nothing for a value equal to what is there, a whole float included', () => {
    expect(paramOperations(step, { gain: { draft: '5' }, n: { draft: '3' } }).operations).toEqual([]);
    expect(paramOperations(step, { gain: { draft: '5.0' } }).operations).toEqual([]);
    expect(paramOperations(step, { flag: { draft: false } }).operations).toEqual([]);
  });

  it('resets a set parameter with unset_param, and an unset one with nothing', () => {
    expect(paramOperations(step, { n: { reset: true }, flag: { reset: true } }).operations).toEqual([
      { kind: 'unset_param', step: 'leaf_add', name: 'n', path: [] },
    ]);
  });

  it('blocks a draft that does not parse', () => {
    const plan = paramOperations(step, { n: { draft: '2.5' }, gain: { draft: '' }, items: { draft: '{}' } });
    expect(plan.operations).toEqual([]);
    expect(plan.errors).toEqual({
      n: 'Enter a whole number.',
      gain: 'Enter a number.',
      items: 'Enter a JSON list.',
    });
  });

  it('changes the target of a bound parameter: an override, a default, or a session value', () => {
    const bound = stepWith([param('n', 'int', '${inputs.n}'), param('m', 'int', '${inputs.m}')]);
    const override = { n: overrideTarget(3) };
    expect(paramOperations(bound, { n: { draft: '8' } }, override)).toEqual({
      operations: [{ kind: 'set_include_override', include: 'sub/chain.yaml', name: 'n', value: 8, position: null }],
      inputs: {},
      errors: {},
    });
    expect(paramOperations(bound, { n: { draft: '3' } }, override).operations).toEqual([]);
    expect(paramOperations(bound, { n: { draft: '4' } }, { n: inputTarget('default', 3) }).operations).toEqual([
      { kind: 'set_recipe_input', document: 'with_sub.yaml', name: 'n', field: 'default', value: 4, position: null },
    ]);
    const session = paramOperations(bound, { n: { draft: '4' } }, { n: inputTarget('session', 3) });
    expect(session.operations).toEqual([]);
    expect(session.inputs).toEqual({ n: 4 });
    // A run config value is changed in the Run Config pane, never here.
    expect(paramOperations(bound, { n: { draft: '4' } }, { n: inputTarget('config', 3) }).operations).toEqual([]);
  });

  it('keeps the session values a form changes on top of the others the session sets', () => {
    const declared = makeSession().inputs.declared;
    expect(sessionValues(declared)).toEqual({});
    expect(sessionValues([{ ...declared[0], source: 'session', value: 7 }])).toEqual({ n: 7 });
  });
});

describe('mutually exclusive parameters', () => {
  const SET_ONLY_ONE = 'Set only one of size and fraction.';
  const exclusive = (size: unknown, fraction: unknown) =>
    stepWith([
      param('size', 'int', size, { constraints: { exclusive_with: ['fraction'] } }),
      param('fraction', 'float', fraction),
    ]);
  const bound = (value: unknown): Record<string, BindingTarget> => ({ fraction: overrideTarget(value) });

  it('words the constraint, and works without it', () => {
    expect(constraintNotes({ exclusive_with: ['fraction'] })).toEqual(['Mutually exclusive with fraction.']);
    expect(constraintNotes({ exclusive_with: ['a', 'b'] })).toEqual(['Mutually exclusive with a, b.']);
    expect(constraintNotes({ exclusive_with: [] })).toEqual([]);
    expect(exclusiveWith(param('x', 'int', 1))).toEqual([]);
    expect(exclusiveWith(param('x', 'int', 1, { constraints: undefined }))).toEqual([]);
  });

  it('marks both parameters when both have a value, in the step order, whichever side declares it', () => {
    const plan = paramOperations(exclusive(10, 0.01), { size: { draft: '12' } });
    expect(plan.errors).toEqual({ size: SET_ONLY_ONE, fraction: SET_ONLY_ONE });
    expect(exclusiveErrors(exclusive(10, null), { size: { draft: '12' } })).toEqual({});
    expect(exclusiveErrors(exclusive(undefined, 0.01), { fraction: { draft: '0.2' } })).toEqual({});
    const other = stepWith([
      param('size', 'int', 10),
      param('fraction', 'float', 0.01, { constraints: { exclusive_with: ['size', 'missing'] } }),
    ]);
    expect(exclusiveErrors(other, { fraction: { draft: '0.2' } })).toEqual({
      size: SET_ONLY_ONE,
      fraction: SET_ONLY_ONE,
    });
  });

  it('marks only a pair the form edits', () => {
    const step = stepWith([...exclusive(10, 0.01).params, param('gain', 'float', 1)]);
    expect(paramOperations(step, {}).errors).toEqual({});
    const plan = paramOperations(step, { gain: { draft: '2' } });
    expect(plan.errors).toEqual({});
    expect(plan.operations).toHaveLength(1);
  });

  it('leaves a pair the validation already reports to its problem', () => {
    const problems = [
      {
        severity: 'error' as const,
        step: 'leaf_add',
        message: "step 'leaf_add' sets both 'fraction' and 'size', which op 'x' declares mutually exclusive",
      },
    ];
    const edits = { size: { draft: '12' } };
    expect(paramOperations(exclusive(10, 0.01), edits, {}, problems).errors).toEqual({});
    const elsewhere = [{ ...problems[0], step: 'other' }];
    expect(paramOperations(exclusive(10, 0.01), edits, {}, elsewhere).errors).toEqual({
      size: SET_ONLY_ONE,
      fraction: SET_ONLY_ONE,
    });
  });

  it('counts a value as given the way the dry run does', () => {
    // Only the spec's default: the op is not passed it.
    const defaulted = stepWith([
      param('size', 'int', undefined, { default: 5, constraints: { exclusive_with: ['fraction'] } }),
      param('fraction', 'float', 0.01),
    ]);
    expect(exclusiveErrors(defaulted, { fraction: { draft: '0.2' } })).toEqual({});
    expect(exclusiveErrors(defaulted, { size: { draft: '5' } })).toEqual({ size: SET_ONLY_ONE, fraction: SET_ONLY_ONE });
    expect(exclusiveErrors(exclusive(10, 0.01), { size: { reset: true } })).toEqual({});
    // A whole input reference nothing resolves.
    const unresolved = exclusive(10, '${inputs.fraction}');
    expect(exclusiveErrors(unresolved, { size: { draft: '12' } })).toEqual({});
    expect(exclusiveErrors(unresolved, { size: { draft: '12' } }, bound('${inputs.missing}'))).toEqual({});
    // A wire does give a value.
    expect(Object.keys(exclusiveErrors(exclusive(10, '${seed.items}'), { size: { draft: '12' } }))).toEqual([
      'size',
      'fraction',
    ]);
  });

  it('reads the drafts and resets of the form', () => {
    expect(exclusiveErrors(exclusive(10, null), { fraction: { draft: '0.5' } })).toEqual({
      size: SET_ONLY_ONE,
      fraction: SET_ONLY_ONE,
    });
    expect(exclusiveErrors(exclusive(10, 0.01), { size: { reset: true } })).toEqual({});
    const plan = paramOperations(exclusive(10, 0.01), { size: { reset: true } });
    expect(plan.errors).toEqual({});
    expect(plan.operations).toEqual([{ kind: 'unset_param', step: 'leaf_add', name: 'size', path: [] }]);
    // A draft that does not parse keeps its own error and adds no conflict.
    expect(paramOperations(exclusive(null, 0.01), { size: { draft: '2.5' } }).errors).toEqual({
      size: 'Enter a whole number.',
    });
  });

  it('uses the value the target of a bound parameter holds, and its edit', () => {
    const step = exclusive(10, '${inputs.fraction}');
    const edits = { size: { draft: '12' } };
    expect(exclusiveErrors(step, edits, bound(null))).toEqual({});
    expect(exclusiveErrors(step, edits, bound(0.01))).toEqual({ size: SET_ONLY_ONE, fraction: SET_ONLY_ONE });
    expect(Object.keys(exclusiveErrors(step, { fraction: { draft: '0.2' } }, bound(null)))).toEqual([
      'size',
      'fraction',
    ]);
    expect(formValue(step.params[1], undefined, bound(0.01).fraction)).toBe(0.01);
    expect(formValue(step.params[1], { draft: 'x' }, bound(0.01).fraction)).toBeUndefined();
  });
});

describe('overrideOperations', () => {
  const include: GraphInclude = structuredClone(findInclude(withSubGraph, 'inc:sub/chain.yaml')!);

  it('sets, removes and adds overrides of the include entry', () => {
    const step = { name: 'step', type: 'float', required: false, default: 1, description: null };
    include.inputs = [...(include.inputs ?? []), step];
    const plan = overrideOperations(include, {
      k: { draft: '7' },
      source: { reset: true },
      step: { draft: '0.5' },
    });
    expect(plan.operations).toEqual([
      { kind: 'set_include_override', include: 'sub/chain.yaml', name: 'k', value: 7, position: null },
      { kind: 'unset_include_override', include: 'sub/chain.yaml', name: 'source' },
      { kind: 'set_include_override', include: 'sub/chain.yaml', name: 'step', value: 0.5, position: null },
    ]);
    expect(overrideOperations(include, { k: { draft: '5' } }).operations).toEqual([]);
    expect(overrideOperations(include, { k: { draft: 'x' } }).errors).toEqual({ k: 'Enter a number.' });
  });
});

describe('defaultOperations', () => {
  const fields = [
    { name: 'n', type: 'int', default: 3, document: 'with_sub.yaml' },
    { name: 'offset', type: 'int', default: null, document: 'sub/leaf.yaml' },
  ];

  it('sets a changed default in the declaring file and removes one that is there', () => {
    expect(defaultOperations(fields, { n: { draft: '4' }, offset: { draft: '2' } }).operations).toEqual([
      { kind: 'set_recipe_input', document: 'with_sub.yaml', name: 'n', field: 'default', value: 4, position: null },
      {
        kind: 'set_recipe_input',
        document: 'sub/leaf.yaml',
        name: 'offset',
        field: 'default',
        value: 2,
        position: null,
      },
    ]);
    expect(defaultOperations(fields, { n: { reset: true }, offset: { reset: true } }).operations).toEqual([
      { kind: 'unset_recipe_input', document: 'with_sub.yaml', name: 'n', field: 'default' },
    ]);
    expect(defaultOperations(fields, { n: { draft: '3' } }).operations).toEqual([]);
  });

  it('sends nothing for a NaN default opened and left as it was', () => {
    const nan = [{ name: 'x', type: 'float', default: '.nan', document: 'with_sub.yaml' }];
    expect(defaultOperations(nan, { x: { draft: '.nan' } })).toEqual({ operations: [], errors: {} });
    expect(defaultOperations(nan, { x: { draft: '2.5' } }).operations).toHaveLength(1);
  });
});

describe('form helpers', () => {
  it('finds the problems of a step naming a parameter as a whole word', () => {
    const problems = [
      { severity: 'error' as const, step: 'leaf_add', message: 'k must be positive' },
      { severity: 'error' as const, step: 'leaf_add', message: 'kk is unknown' },
      { severity: 'warning' as const, step: 'total', message: 'k is odd' },
    ];
    expect(paramProblems(problems, 'leaf_add', 'k').map((problem) => problem.message)).toEqual(['k must be positive']);
  });

  it('words the constraints', () => {
    expect(constraintNotes({ must_exist: true })).toEqual(['The path must exist.']);
    expect(constraintNotes({ must_exist: false, create_if_missing: true })).toEqual([
      'The path need not exist yet.',
      'The folder is created if it is missing.',
    ]);
    expect(constraintNotes({})).toEqual([]);
    expect(constraintNotes(undefined)).toEqual([]);
  });

  it('lists the problems of an edit refusal', () => {
    const rejected = new ApiError(422, 'EditRejected', 'a\nb', { problems: ['a', 'b'] });
    expect(editProblems(rejected)).toEqual(['a', 'b']);
    expect(editProblems(new ApiError(400, 'ValueError', 'bad value'))).toEqual(['bad value']);
    expect(editProblems(new Error('boom'))).toEqual(['boom']);
  });

  it('edits only the files of an editable session', () => {
    expect(fileEditable(editableSession(), 'sub/leaf.yaml')).toBe(true);
    expect(fileEditable(editableSession(), 'other.yaml')).toBe(false);
    expect(fileEditable(makeSession(), 'sub/leaf.yaml')).toBe(false);
  });
});
