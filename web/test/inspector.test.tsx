import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Graph, StepNode } from '../src/api/graph';
import { opReferenceUrl, paramRows } from '../src/inspector/details';
import { InputsPane } from '../src/inputs/InputsPane';
import { Inspector } from '../src/inspector/Inspector';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import { declaredGraph, makeSession, mockApi, renderWithClient, withSubGraph } from './helpers';

const SID = '20261013-153012-a1b2c3';
const CHAIN = 'inc:sub/chain.yaml';

/** with_sub with `seed` dressed up as a registered op, as real recipes have. */
function registeredGraph(): Graph {
  const graph = structuredClone(withSubGraph);
  const seed = graph.nodes.find((node) => node.id === 'seed') as StepNode;
  Object.assign(seed, {
    op: 'compute_sv',
    op_url: 'ops/compute_sv/',
    custom: false,
    callable_path: null,
    category: 'level 2',
    implementation: { key: 'default', callable_path: 'echopype.calibrate.compute_Sv', dependency: 'echopype' },
    checkpoint: 'always',
  });
  seed.params.push({
    name: 'waveform_mode',
    type: 'str',
    units: null,
    description: 'CW or FM.',
    default: 'CW',
    set: false,
    value: null,
  });
  return graph;
}

function openSession(graph: Graph) {
  mockApi({ [`GET /api/sessions/${SID}`]: makeSession({ graph }) });
  useSessionStore.getState().setSid(SID);
}

beforeEach(() => {
  useSessionStore.getState().setSid(null);
  useView.getState().resetScope();
});

describe('Inspector', () => {
  it('asks for a selection when nothing is selected', async () => {
    openSession(withSubGraph);
    renderWithClient(<Inspector />);
    expect(await screen.findByText(/Select a step, sub-recipe, inputs or outputs box/)).toBeInTheDocument();
  });

  it('shows a step overview, parameters, ports and the cache placeholder', async () => {
    openSession(registeredGraph());
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('Make a list of n numbers.')).toBeInTheDocument();
    expect(screen.getByText('compute_sv')).toBeInTheDocument();
    expect(screen.getByText('level 2').closest('td')).toHaveTextContent(/^level 2$/);
    expect(document.querySelector('.rc-inspector .rc-swatch')).toBeNull();
    expect(screen.getByText('echopype.calibrate.compute_Sv')).toBeInTheDocument();
    expect(screen.getByText('with_sub.yaml')).toBeInTheDocument();

    const link = screen.getByRole('link', { name: /Op reference for compute_sv/ });
    expect(link).toHaveAttribute('href', 'https://blayman-noaa.github.io/AA-SI_recipe_manager/ops/compute_sv/');
    expect(link).toHaveAttribute('target', '_blank');

    const n = screen.getByText('${inputs.n}').closest('tr')!;
    expect(n).toHaveTextContent('set');
    const mode = screen.getByText('waveform_mode').closest('tr')!;
    expect(mode).toHaveTextContent('CW');
    expect(mode).toHaveTextContent('default');

    expect(screen.getByText('items')).toBeInTheDocument();
    expect(screen.getByText(/once recipes can run from the client/)).toBeInTheDocument();
    expect(screen.getByText('not computed')).toBeInTheDocument();
  });

  it('shows an include with its inputs, where its outputs go, and drills in', async () => {
    const user = userEvent.setup();
    openSession(withSubGraph);
    useView.getState().select({ kind: 'include', id: CHAIN });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('client_chain')).toBeInTheDocument();
    expect(screen.getByText('Per item increment followed by the leaf sub-recipe.')).toBeInTheDocument();
    expect(screen.getByText('sub/chain.yaml')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Inputs' })).toBeInTheDocument();
    expect(screen.getByText('Values the parent sets where it includes this sub-recipe.')).toBeInTheDocument();
    const inputs = [...document.querySelectorAll('details.rc-row-details summary')].map((item) => item.textContent);
    expect(inputs).toEqual(['source${seed.items}', 'k5']);
    expect(screen.getByRole('heading', { name: 'Outputs' })).toBeInTheDocument();
    const out = screen.getByText('out').closest('tr')!;
    expect(out).toHaveTextContent('used by total');
    expect(screen.getByText('1 output is used inside the sub-recipe.')).toBeInTheDocument();

    expect(screen.queryByRole('button', { name: 'Expand' })).toBeNull();
    await user.click(screen.getByRole('button', { name: 'Drill in' }));
    expect(useView.getState().drill).toEqual([CHAIN]);
  });

  it('shows none for a parameter without a value', async () => {
    const graph = registeredGraph();
    const seed = graph.nodes.find((node) => node.id === 'seed') as StepNode;
    seed.params.push({
      name: 'save_dir',
      type: 'path',
      units: null,
      description: null,
      default: null,
      set: false,
      value: null,
    });
    openSession(graph);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('save_dir')).toBeInTheDocument();
    expect(screen.getByText('save_dir').closest('tr')).toHaveTextContent('none');
  });

  it('shows the parent value an override passes on', async () => {
    openSession(withSubGraph);
    useView.getState().select({ kind: 'include', id: 'inc:sub/leaf.yaml' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('client_leaf')).toBeInTheDocument();
    const summaries = [...document.querySelectorAll('details.rc-row-details summary')].map((item) => item.textContent);
    expect(summaries).toContain('k${inputs.k} = 5');
  });

  it('shows a recipe input with its session value', async () => {
    openSession(withSubGraph);
    useView.getState().select({ kind: 'input', id: 'in:n' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('Length of the seed list.')).toBeInTheDocument();
    expect(screen.getByText('recipe input')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'seed' })).toBeInTheDocument();
  });

  it('lists the recipe inputs once, each row holding its control, with one Apply', async () => {
    const user = userEvent.setup();
    const put = vi.fn((_init: RequestInit | undefined) => makeSession());
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`PUT /api/sessions/${SID}/inputs`]: put,
    });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'inputsBox', id: 'inputs' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('Recipe inputs')).toBeInTheDocument();
    expect(screen.getByText('1 input')).toBeInTheDocument();
    const rows = document.querySelectorAll<HTMLDetailsElement>('details.rc-row-details');
    expect(rows).toHaveLength(1);
    const row = rows[0];
    expect(row.querySelector('summary')).toHaveTextContent('n');
    expect(row.querySelector('summary')).toHaveTextContent('default');
    expect(row.open).toBe(false);
    await user.click(row.querySelector('summary')!);
    expect(row.open).toBe(true);
    expect(row).toHaveTextContent('Length of the seed list.');
    expect(row).toHaveTextContent('used by');
    expect(screen.queryByRole('heading', { name: 'Edit values' })).toBeNull();
    expect(screen.getAllByRole('button', { name: 'Apply' })).toHaveLength(1);

    const control = within(row).getByLabelText('n');
    await user.clear(control);
    await user.type(control, '5');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    expect(put).toHaveBeenCalledTimes(1);
    expect(JSON.parse(String(put.mock.calls[0][0]?.body))).toEqual({ values: { n: 5 } });
  });

  it('shows unset for an input without a value', async () => {
    const session = makeSession();
    session.inputs.declared[0] = { ...session.inputs.declared[0], value: null, source: 'unset' };
    session.inputs.values = { n: null };
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'inputsBox', id: 'inputs' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('Recipe inputs')).toBeInTheDocument();
    expect(document.querySelector('details.rc-row-details summary')).toHaveTextContent('nunset');
  });

  it('gives each form its own field ids when the inspector and the Inputs pane are both open', async () => {
    openSession(withSubGraph);
    useView.getState().select({ kind: 'inputsBox', id: 'inputs' });
    renderWithClient(
      <>
        <Inspector />
        <InputsPane />
      </>,
    );
    expect(await screen.findByText('Recipe inputs')).toBeInTheDocument();
    const ids = [...document.querySelectorAll('input, textarea')].map((element) => element.id).filter(Boolean);
    expect(ids).toHaveLength(2);
    expect(new Set(ids).size).toBe(2);
  });

  it('lists the inputs from the parent with the steps they come from', async () => {
    openSession(withSubGraph);
    useView.getState().select({ kind: 'inputsBox', id: `overrides:${CHAIN}` });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('Inputs from the parent')).toBeInTheDocument();
    expect(screen.getByText('2 inputs')).toBeInTheDocument();
    const rows = [...document.querySelectorAll('details.rc-row-details')];
    expect(rows.map((row) => row.querySelector('summary')!.textContent)).toEqual(['source${seed.items}', 'k5']);
    expect(rows[0]).toHaveTextContent('from');
    expect(rows[0].querySelector('button')).toHaveTextContent('seed');
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
  });

  it('lists an outputs box with where each output goes', async () => {
    openSession(declaredGraph());
    useView.getState().select({ kind: 'outputsBox', id: 'total' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('1 output')).toBeInTheDocument();
    const row = document.querySelector('details.rc-row-details')!;
    expect(row.querySelector('summary')).toHaveTextContent('total');
    expect(row).toHaveTextContent('recipe output');
  });

  it('says so when no output of a drilled step is used later', async () => {
    const graph = structuredClone(withSubGraph);
    graph.nodes.push({
      id: 'out:chain_inc.extra',
      kind: 'output',
      label: 'chain_inc.extra',
      type: 'int',
      declared: false,
      from: { step: 'chain_inc', output: 'extra' },
    });
    graph.edges.push({
      source: 'chain_inc',
      target: 'out:chain_inc.extra',
      kind: 'exit',
      source_output: 'extra',
      target_input: null,
      label: 'extra',
      type: 'int',
    });
    openSession(graph);
    useView.setState({ drill: [CHAIN] });
    useView.getState().select({ kind: 'outputsBox', id: 'chain_inc' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('No output of this step is used later.')).toBeInTheDocument();
    expect(document.querySelector('details.rc-row-details')).toHaveTextContent('not used');
  });

  it('tells where each output of a step goes', async () => {
    openSession(declaredGraph());
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('items')).toBeInTheDocument();
    expect(screen.getByText('items').closest('tr')).toHaveTextContent('used by chain_inc');
  });
});

describe('inspector details', () => {
  it('resolves op links against the docs site and keeps absolute ones', () => {
    const step = { op_url: 'ops/compute_sv/' } as StepNode;
    expect(opReferenceUrl(step, 'https://example.org/docs/')).toBe('https://example.org/docs/ops/compute_sv/');
    expect(opReferenceUrl({ op_url: 'https://x.org/a/' } as StepNode, 'https://example.org/')).toBe('https://x.org/a/');
    expect(opReferenceUrl({ op_url: null } as StepNode, 'https://example.org/')).toBeNull();
  });

  it('shows the value of a set param and the default of an unset one', () => {
    const rows = paramRows(registeredGraph().nodes.find((node) => node.id === 'seed') as StepNode);
    expect(rows.map((row) => [row.name, row.value, row.set])).toEqual([
      ['n', '${inputs.n}', true],
      ['waveform_mode', 'CW', false],
    ]);
  });
});
