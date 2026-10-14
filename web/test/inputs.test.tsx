import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { InputField, Problem } from '../src/api/models';
import { controlFor, formValues, inputProblems, parseDraft } from '../src/inputs/inputForm';
import { InputsForm } from '../src/inputs/InputsPane';
import { RunSettingsView } from '../src/runs/RunSettingsPane';
import { makeSession } from './helpers';

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
        onSetExecutor={() => {}}
      />,
    );
    expect(screen.getByText('executor dask needs dask_workers.')).toBeInTheDocument();
    expect(screen.queryByText(/positive int/)).toBeNull();
  });
});
