import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { fieldFile } from '../src/api/fields';
import { groupByDocument, type Graph } from '../src/api/graph';
import type { InputField, Problem } from '../src/api/models';
import { controlFor, formValues, inputProblems, parseDraft } from '../src/inputs/inputForm';
import { InputsForm, InputsPane } from '../src/inputs/InputsPane';
import { RunSettingsView } from '../src/runs/RunSettingsPane';
import { useSessionStore } from '../src/state/session';
import { makeSession, mockApi, nestedInputGraph, renderWithClient } from './helpers';

const SID = '20261013-153012-a1b2c3';

/** The names of the declared inputs per declaring file, as the Inputs pane groups them. */
function inputGroups(graph: Graph, declared: InputField[]) {
  return groupByDocument(graph, declared, (field) => fieldFile(graph, field)).map(({ file, items }) => ({
    file,
    names: items.map((field) => field.name),
  }));
}

const DECLARED: InputField[] = [
  { name: 'n', type: 'int', description: 'Count.', default: 3, required: false, value: 2, source: 'session' },
  { name: 'label', type: 'str', description: null, default: 'a', required: false, value: 'a', source: 'default' },
  { name: 'k', type: 'int', description: null, default: 5, required: false, value: 5, source: 'config' },
  { name: 'flag', type: 'bool', description: null, default: false, required: false, value: false, source: 'default' },
  { name: 'items', type: 'list', description: null, default: [1], required: false, value: [1], source: 'default' },
];

describe('InputsForm', () => {
  it('renders an int input with a session value as a number field with the session badge', () => {
    render(<InputsForm declared={DECLARED} problems={[]} onApply={() => {}} />);
    const field = screen.getByLabelText('n');
    expect(field).toHaveAttribute('type', 'number');
    expect(field).toHaveValue(2);
    const head = field.closest('.rc-field')!;
    expect(head.querySelector('.rc-badge-session')).toHaveTextContent('session');
    // The run config reads as the diagram and the inspector name it.
    const k = screen.getByLabelText('k').closest('.rc-field')!;
    expect(k.querySelector('.rc-badge-config')).toHaveTextContent(/^run config$/);
    expect(screen.getByLabelText('flag')).toHaveAttribute('type', 'checkbox');
    expect(screen.getByLabelText('items').tagName).toBe('TEXTAREA');
  });

  it('applies the session values plus the changed ones only', async () => {
    const user = userEvent.setup();
    const onApply = vi.fn();
    render(<InputsForm declared={DECLARED} problems={[]} onApply={onApply} />);

    const apply = screen.getByRole('button', { name: 'Apply' });
    expect(apply).toBeDisabled();

    const k = screen.getByLabelText('k');
    await user.clear(k);
    await user.type(k, '7');
    await user.click(apply);

    expect(onApply).toHaveBeenCalledWith({ n: 2, k: 7 });
  });

  it('blocks Apply while a value does not parse', async () => {
    const user = userEvent.setup();
    render(<InputsForm declared={DECLARED} problems={[]} onApply={() => {}} />);
    const items = screen.getByLabelText('items');
    await user.clear(items);
    await user.type(items, 'not json');
    expect(screen.getByText('Not valid JSON.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
  });

  it('shows input problems under the form', () => {
    render(
      <InputsForm
        declared={DECLARED}
        problems={[
          { severity: 'warning', message: 'Input label is set but not used.', step: null },
          { severity: 'error', message: 'Unrelated step error.', step: 'seed' },
        ]}
        onApply={() => {}}
      />,
    );
    expect(screen.getByText(/label is set but not used/)).toBeInTheDocument();
    expect(screen.queryByText(/Unrelated/)).not.toBeInTheDocument();
  });
});

describe('input groups', () => {
  const offset: InputField = {
    name: 'offset',
    type: 'int',
    description: 'Added after k.',
    default: 2,
    required: false,
    value: 2,
    source: 'default',
  };

  it('orders the root file, then the sub-recipes in graph order, then inputs the graph lacks', () => {
    const graph = nestedInputGraph();
    const chain = graph.includes.find((include) => include.id === 'inc:sub/chain.yaml')!;
    const step = { name: 'step', type: 'int', required: false, default: 1, description: null };
    chain.inputs = [...(chain.inputs ?? []), step];
    graph.nodes.push({
      id: 'in:step',
      kind: 'input',
      label: 'inputs.step',
      type: 'int',
      declared: true,
      required: false,
      default: 1,
      description: null,
      declared_in: 'sub/chain.yaml',
    });
    const declared = [
      { ...offset, name: 'step' },
      offset,
      { ...offset, name: 'ghost' },
      makeSession().inputs.declared[0],
    ];
    expect(inputGroups(graph, declared)).toEqual([
      { file: 'with_sub.yaml', names: ['n'] },
      { file: 'sub/leaf.yaml', names: ['offset'] },
      { file: 'sub/chain.yaml', names: ['step'] },
      { file: null, names: ['ghost'] },
    ]);
  });

  it('reads every input as the root file in an older graph', () => {
    const graph = nestedInputGraph();
    for (const node of graph.nodes) if (node.kind === 'input') delete node.declared_in;
    expect(inputGroups(graph, [makeSession().inputs.declared[0], offset])).toEqual([
      { file: 'with_sub.yaml', names: ['n', 'offset'] },
    ]);
  });

  it('heads each file in the Inputs pane, and nothing for a recipe of one file', async () => {
    const session = makeSession({ graph: nestedInputGraph() });
    session.inputs.declared.push(offset);
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    const { unmount } = renderWithClient(<InputsPane />);
    expect(await screen.findByLabelText('offset')).toBeInTheDocument();
    const order = [...document.querySelectorAll('.rc-group-head, .rc-field-name')].map((item) => item.textContent);
    expect(order).toEqual(['with_sub.yaml', 'n', 'sub/leaf.yaml', 'offset']);
    unmount();

    mockApi({ [`GET /api/sessions/${SID}`]: makeSession() });
    renderWithClient(<InputsPane />);
    expect(await screen.findByLabelText('n')).toBeInTheDocument();
    expect(document.querySelector('.rc-group-head')).toBeNull();
    useSessionStore.getState().setSid(null);
  });
});

describe('form logic', () => {
  it('chooses a control per type', () => {
    expect(controlFor('int')).toBe('number');
    expect(controlFor('float')).toBe('number');
    expect(controlFor('bool')).toBe('checkbox');
    expect(controlFor('dict')).toBe('json');
    expect(controlFor('path')).toBe('text');
    expect(controlFor(null)).toBe('text');
  });

  it('parses drafts by type', () => {
    expect(parseDraft('4', 'int')).toEqual({ ok: true, value: 4 });
    expect(parseDraft('4.5', 'int').ok).toBe(false);
    expect(parseDraft('4.5', 'float')).toEqual({ ok: true, value: 4.5 });
    expect(parseDraft('{"a": 1}', 'dict')).toEqual({ ok: true, value: { a: 1 } });
    expect(parseDraft('[1]', 'dict').ok).toBe(false);
    expect(parseDraft('10s', 'str')).toEqual({ ok: true, value: '10s' });
  });

  it('drops a reset session value and ignores an edit back to the same value', () => {
    const result = formValues(DECLARED, { n: { reset: true }, label: { draft: 'a' } });
    expect(result).toEqual({ values: {}, errors: {}, changed: true });
    expect(formValues(DECLARED, { label: { draft: 'a' } }).changed).toBe(false);
  });
});

describe('inputProblems', () => {
  const declared: InputField[] = [
    { name: 'n', type: 'int', description: null, default: 3, required: false, value: 3, source: 'default' },
    { name: 'ping_time_bin', type: 'str', description: null, default: '10s', required: false, value: '10s', source: 'default' },
  ];
  const problems: Problem[] = [
    { severity: 'error', message: 'Input n must be a positive int.', step: null },
    { severity: 'warning', message: 'Unknown setting in the recipe.', step: null },
    { severity: 'error', message: 'ping_time_bin is not a duration.', step: null },
    { severity: 'error', message: 'Run config: executor dask needs dask_workers.', step: null },
    { severity: 'error', message: 'Input n is wrong here.', step: 'seed' },
    { severity: 'warning', message: 'inputs.k is set but never used.', step: null },
  ];

  it('matches input names as whole words and leaves run config problems out', () => {
    expect(inputProblems(problems, declared).map((problem) => problem.message)).toEqual([
      'Input n must be a positive int.',
      'ping_time_bin is not a duration.',
      'inputs.k is set but never used.',
    ]);
  });

  it('shows run config problems in Run Settings without their prefix', () => {
    render(
      <RunSettingsView
        config={makeSession().run_config}
        problems={problems}
        onSet={() => {}}
      />,
    );
    expect(screen.getByText('executor dask needs dask_workers.')).toBeInTheDocument();
    expect(screen.queryByText(/positive int/)).toBeNull();
  });
});
