import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Graph, StepNode } from '../src/api/graph';
import { opReferenceUrl, paramRows } from '../src/inspector/details';
import { InputsPane } from '../src/inputs/InputsPane';
import { DocsEnvContext } from '../src/env';
import { Inspector } from '../src/inspector/Inspector';
import { InspectorBody } from '../src/inspector/InspectorBody';
import { useRunStore } from '../src/runs/store';
import { useSessionStore } from '../src/state/session';
import { createViewStore, useView, ViewStoreContext } from '../src/state/view';
import artifactsFixture from './fixtures/artifacts.sv_mapped.json';
import { declaredGraph, makeSession, mockApi, nestedInputGraph, renderWithClient, withSubGraph } from './helpers';

const SID = '20261013-153012-a1b2c3';
const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';

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
  useRunStore.getState().resetFor(null);
});

describe('Inspector', () => {
  it('asks for a selection when nothing is selected', async () => {
    openSession(withSubGraph);
    renderWithClient(<Inspector />);
    expect(await screen.findByText(/Select a step, sub-recipe, inputs or outputs box/)).toBeInTheDocument();
  });

  it('shows a step overview, parameters, ports and the cache section', async () => {
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
    expect(screen.getByText('last run').closest('tr')).toHaveTextContent('not run');
    expect(screen.getByText('not computed')).toBeInTheDocument();
  });

  it('lists the saved files when asked, without resolving them for the echogram', async () => {
    const user = userEvent.setup();
    const fetchMock = mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/steps/seed/artifacts?resolve=false`]: {
        ...artifactsFixture,
        step_id: 'seed',
        echogram: { available: true, reason: null, resolution: null },
      },
    });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);
    const summary = await screen.findByText('Saved files');
    // A list output: no eye icon, so no echogram button either.
    expect(screen.queryByRole('button', { name: 'Show in echogram' })).toBeNull();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/artifacts'))).toBe(false);
    await user.click(summary);
    expect(document.querySelector<HTMLDetailsElement>('.rc-saved-files')?.open).toBe(true);
    expect(await screen.findAllByText(/ds_Sv #/)).toHaveLength(3);
    expect(fetchMock).toHaveBeenCalledWith(`/api/sessions/${SID}/steps/seed/artifacts?resolve=false`, expect.anything());
  });

  it('shows the cache status from the preview and the run shown, and opens a failed step log', async () => {
    const user = userEvent.setup();
    const preview = {
      recipe: 'client_with_sub',
      version: '1.0',
      mode: 'to',
      targets: ['seed'],
      force_steps: [],
      tiers: { user: '/c' },
      write_tier: 'user',
      steps: [
        {
          step_id: 'seed',
          step_hash: 'abc123',
          held_by: ['user'],
          planned: 'load-user',
          tier: 'user',
          checkpoint: 'saved',
          pinned: true,
          recipe_checkpoint: null,
          fanned_out: false,
          instances: null,
          instances_held: {},
          note: null,
        },
      ],
      blockers: [],
      warnings: [],
    };
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/preview?targets=seed`]: preview,
    });
    useSessionStore.getState().setSid(SID);
    const store = useRunStore.getState();
    store.resetFor(SID);
    store.begin('r1');
    store.apply('r1', [
      { seq: 1, time: 1, kind: 'step_finished', run_id: 'x', data: { step: 'seed', disposition: 'computed', tier: 'user', seconds: 2.5, hash: 'old999', index: 1, total: 2, instances: null } },
      { seq: 2, time: 2, kind: 'step_failed', run_id: 'x', data: { step: 'total', index: 2, total: 2, seconds: 0.1, error_type: 'RuntimeError', message: 'boom', traceback: 'Traceback: boom', chain: null } },
    ]);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('abc123')).toBeInTheDocument();
    expect(screen.getByText('held by').closest('tr')).toHaveTextContent('user');
    expect(screen.getByText('predicted').closest('tr')).toHaveTextContent('load-user');
    expect(screen.getByText('last run').closest('tr')).toHaveTextContent('computed in 2.5 s (user)');
    expect(screen.getByText('this run').closest('tr')).toHaveTextContent('saved, pinned as a target');
    expect(screen.getByText('The hash changed since the last run.')).toBeInTheDocument();

    act(() => useView.getState().select({ kind: 'step', id: 'total' }));
    expect(await screen.findByText('RuntimeError: boom')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Open log' }));
    expect(useRunStore.getState().logStep).toBe('total');
    expect(useRunStore.getState().logFocus).toBeGreaterThan(0);
  });

  it('shows an include with its inputs, where its outputs go, and drills in', async () => {
    const user = userEvent.setup();
    openSession(withSubGraph);
    useView.getState().select({ kind: 'include', id: CHAIN });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('client_chain')).toBeInTheDocument();
    expect(screen.getByText('Per item increment followed by the leaf sub-recipe.')).toBeInTheDocument();
    expect(screen.getByText('source', { selector: 'th' }).closest('tr')).toHaveTextContent('sub/chain.yaml');
    expect(screen.getByRole('heading', { name: 'Overrides' })).toBeInTheDocument();
    expect(screen.getByText('Values the parent sets where it includes this sub-recipe.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Inputs' })).toBeInTheDocument();
    expect(screen.getByText('No inputs of its own.')).toBeInTheDocument();
    expect(screen.getByText('inputs').closest('tr')).toHaveTextContent('2 declared, 2 overridden');
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

  it('shows a run config value and gives a mapped input one row', async () => {
    const graph = structuredClone(withSubGraph);
    graph.edges.push({
      source: 'in:n',
      target: 'seed',
      kind: 'map_over',
      source_output: null,
      target_input: 'n',
      label: 'n',
      type: 'int',
    });
    const session = makeSession({ graph });
    session.inputs.declared[0] = { ...session.inputs.declared[0], value: 4, source: 'config' };
    session.inputs.values = { n: 4 };
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'inputsBox', id: 'inputs' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('Recipe inputs')).toBeInTheDocument();
    const rows = document.querySelectorAll('details.rc-row-details');
    expect(rows).toHaveLength(1);
    expect(rows[0].querySelector('summary')).toHaveTextContent(/^n4run config$/);
    expect(document.querySelector('.rc-group-head')).toBeNull();
  });

  it('groups the form by declaring file, the root file first', async () => {
    const session = makeSession({ graph: nestedInputGraph() });
    session.inputs.declared.push({
      name: 'offset',
      type: 'int',
      description: 'Added after k.',
      default: 2,
      required: false,
      value: 2,
      source: 'default',
    });
    session.inputs.values = { n: 3, offset: 2 };
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'inputsBox', id: 'inputs' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('2 inputs')).toBeInTheDocument();
    const order = [...document.querySelectorAll('.rc-group-head, details.rc-row-details > summary > :first-child')];
    expect(order.map((item) => item.textContent)).toEqual(['with_sub.yaml', 'n', 'sub/leaf.yaml', 'offset']);
    expect(screen.getAllByRole('button', { name: 'Apply' })).toHaveLength(1);
    const offset = screen.getByLabelText('offset').closest('details')!;
    expect(offset).toHaveTextContent('declared insub/leaf.yaml');
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

  it('lists a drilled sub-recipe box with its overrides and the steps they come from', async () => {
    openSession(withSubGraph);
    useView.getState().select({ kind: 'inputsBox', id: `overrides:${CHAIN}` });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('sub-recipe')).toBeInTheDocument();
    expect(document.querySelector('.rc-inspector-title')).toHaveTextContent(/^Inputssub-recipe$/);
    expect(screen.queryByText('Inputs from the parent')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Overrides' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Inputs' })).toBeInTheDocument();
    const rows = [...document.querySelectorAll('details.rc-row-details')];
    expect(rows.map((row) => row.querySelector('summary')!.textContent)).toEqual(['source${seed.items}', 'k5']);
    expect(rows[0]).toHaveTextContent('from');
    expect(rows[0].querySelector('button')).toHaveTextContent('seed');
    expect(screen.queryByText(/more input/)).toBeNull();
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
  });

  it('lists a nested sub-recipe input under Inputs with the file declaring it', async () => {
    openSession(nestedInputGraph());
    useView.setState({ drill: [CHAIN, LEAF] });
    useView.getState().select({ kind: 'inputsBox', id: `overrides:${LEAF}` });
    renderWithClient(<Inspector />);

    expect(await screen.findByRole('heading', { name: 'Inputs' })).toBeInTheDocument();
    const rows = [...document.querySelectorAll('details.rc-row-details')];
    expect(rows.map((row) => row.querySelector('summary')!.textContent)).toEqual([
      'value${chain_inc.out}',
      'k${inputs.k} = 5',
      'offset2default',
    ]);
    const heading = screen.getByRole('heading', { name: 'Inputs' });
    expect(heading.compareDocumentPosition(rows[2]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(rows[2]).toHaveTextContent('declared insub/leaf.yaml');
    expect(rows[2]).toHaveTextContent('(the default)');
    expect(rows[2]).toHaveTextContent('used byleaf_add');
    expect(screen.getByText(/declares; each shows the value a run would use\./)).toHaveTextContent(
      'What sub/leaf.yaml declares; each shows the value a run would use.',
    );
  });

  it('counts the nested inputs below a drilled sub-recipe and shows a run config value', async () => {
    const session = makeSession({ graph: nestedInputGraph() });
    session.inputs.declared.push({
      name: 'offset',
      type: 'int',
      description: 'Added after k.',
      default: 2,
      required: false,
      value: 9,
      source: 'config',
    });
    session.inputs.values = { n: 3, offset: 9 };
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    useView.setState({ drill: [CHAIN] });
    useView.getState().select({ kind: 'inputsBox', id: `overrides:${CHAIN}` });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('1 more input declared in sub-recipes below this one.')).toBeInTheDocument();

    act(() => useView.getState().select({ kind: 'include', id: LEAF }));
    const offset = (await screen.findByText('offset')).closest('details')!;
    expect(offset.querySelector('summary')).toHaveTextContent('offset9run config');
    expect(offset).toHaveTextContent('(from the run config)');
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

describe('InspectorBody', () => {
  const site = { docsUrl: 'https://site.example/docs/', linkTarget: '_self' as const, pages: {} };

  it('links the op reference in the same tab on the site, with no session or query client', () => {
    render(
      <DocsEnvContext.Provider value={site}>
        <InspectorBody graph={registeredGraph()} selected={{ kind: 'step', id: 'seed' }} drill={[]} />
      </DocsEnvContext.Provider>,
    );
    const link = screen.getByRole('link', { name: /Op reference for compute_sv/ });
    expect(link).toHaveAttribute('href', 'https://site.example/docs/ops/compute_sv/');
    expect(link).not.toHaveAttribute('target');
    expect(screen.queryByText('Cache')).toBeNull();
  });

  it('shows where a sub-recipe is documented and selects its steps in its own store', async () => {
    const user = userEvent.setup();
    const store = createViewStore();
    const pages = { [CHAIN]: { title: 'The chain level', url: 'levels/chain/' } };
    render(
      <DocsEnvContext.Provider value={{ ...site, pages }}>
        <ViewStoreContext.Provider value={store}>
          <InspectorBody graph={withSubGraph} selected={{ kind: 'include', id: CHAIN }} drill={[]} />
        </ViewStoreContext.Provider>
      </DocsEnvContext.Provider>,
    );
    const page = screen.getByRole('link', { name: 'The chain level' });
    expect(page).toHaveAttribute('href', 'https://site.example/docs/levels/chain/');
    expect(screen.getByText('documented at')).toBeInTheDocument();
    await user.click(screen.getAllByRole('button', { name: 'chain_inc' })[0]);
    expect(store.getState().selected).toEqual({ kind: 'step', id: 'chain_inc' });
    expect(store.getState().drill).toEqual([CHAIN]);
    expect(useView.getState().selected).toBeNull();
  });

  it('lists the recipe inputs read only without a form', () => {
    render(<InspectorBody graph={declaredGraph()} selected={{ kind: 'inputsBox', id: 'inputs' }} drill={[]} />);
    expect(screen.getByText('Recipe inputs')).toBeInTheDocument();
    expect(document.querySelectorAll('.rc-row-details').length).toBeGreaterThan(0);
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
    expect(document.querySelector('form')).toBeNull();
  });

  it('groups the recipe inputs on the site by declaring file, the root file first', () => {
    render(<InspectorBody graph={nestedInputGraph()} selected={{ kind: 'inputsBox', id: 'inputs' }} drill={[]} />);
    expect(screen.getByText('2 inputs')).toBeInTheDocument();
    const heads = [...document.querySelectorAll('h3.rc-group-head')].map((head) => head.textContent);
    expect(heads).toEqual(['with_sub.yaml', 'sub/leaf.yaml']);
    const offset = screen.getByText('offset').closest('details')!;
    expect(document.querySelectorAll('h3.rc-group-head')[1].compareDocumentPosition(offset)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    );
    expect(offset).toHaveTextContent('declared insub/leaf.yaml');
    expect(offset).toHaveTextContent('used byleaf_add');
  });

  it('names the file declaring a recipe input', () => {
    render(<InspectorBody graph={nestedInputGraph()} selected={{ kind: 'input', id: 'in:offset' }} drill={[]} />);
    expect(screen.getByText('declared in').closest('tr')).toHaveTextContent('sub/leaf.yaml');
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
