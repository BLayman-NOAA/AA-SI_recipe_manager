import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import type { DockviewApi } from 'dockview-react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { findInclude, type Graph, type GraphParam, type StepNode } from '../src/api/graph';
import type { SessionDocument } from '../src/api/models';
import { STALE_VERSION_MESSAGE } from '../src/api/queries';
import { useEditingStore } from '../src/editing/store';
import { opReferenceUrl, paramRows } from '../src/inspector/details';
import { SURVEY_BANNER } from '../src/inspector/StepParamForm';
import { InputsPane } from '../src/inputs/InputsPane';
import { DocsEnvContext } from '../src/env';
import { Inspector } from '../src/inspector/Inspector';
import { InspectorBody } from '../src/inspector/InspectorBody';
import { useRunStore } from '../src/runs/store';
import { useDock } from '../src/shell/dock';
import { useSessionStore } from '../src/state/session';
import { createViewStore, useView, ViewStoreContext } from '../src/state/view';
import artifactsFixture from './fixtures/artifacts.sv_mapped.json';
import {
  apiErrorResponse,
  declaredGraph,
  editableSession,
  makePreview,
  makeSession,
  mockApi,
  nestedInputGraph,
  previewStep,
  renderWithClient,
  runConfigBlock,
  testQueryClient,
  withSubGraph,
} from './helpers';

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
    expect(screen.getByText('predicted').closest('tr')).toHaveTextContent('loaded from user cache');
    expect(screen.getByText('last run').closest('tr')).toHaveTextContent('computed in 2.5 s (user)');
    expect(screen.getByText('this run').closest('tr')).toHaveTextContent('saved, pinned as a target');
    expect(screen.getByText('The hash changed since the last run.')).toBeInTheDocument();

    act(() => useView.getState().select({ kind: 'step', id: 'total' }));
    expect(await screen.findByText('RuntimeError: boom')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Open log' }));
    expect(useRunStore.getState().logStep).toBe('total');
    expect(useRunStore.getState().logFocus).toBeGreaterThan(0);
  });

  it('words what becomes of the step in this run', async () => {
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession(),
      [`GET /api/sessions/${SID}/preview?targets=seed`]: makePreview([previewStep('seed', { checkpoint: 'memory-only' })]),
    });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);
    await waitFor(() => expect(screen.getByText('this run').closest('tr')).toHaveTextContent('kept in memory'));
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

  it('shows the run config file, its inputs with what becomes of each and its settings for the box', async () => {
    const base = makeSession();
    const session = makeSession({
      inputs: { declared: [{ ...base.inputs.declared[0], source: 'session', value: 7 }], values: { n: 7 } },
      run_config: runConfigBlock({
        contents: { inputs: { n: 5, stray: 'x' }, settings: { user_cache_dir: './u', temp_dir: null } },
        unused_inputs: ['stray'],
        shared_with: ['C:/recipes/other.yaml'],
      }),
    });
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'runConfig', id: 'runConfig' });
    renderWithClient(<Inspector />);

    const title = await screen.findByText('with_sub.config.yaml');
    const pane = title.closest('.rc-inspector') as HTMLElement;
    expect(pane).toHaveTextContent('C:/recipes/with_sub.config.yaml');
    expect(pane).toHaveTextContent('as the CLI finds it');
    expect(within(pane).getByTitle('C:/recipes/other.yaml')).toHaveTextContent('other.yaml');
    const row = (name: string) => within(pane).getByText(name, { selector: 'th' }).closest('tr')!;
    expect(row('n')).toHaveTextContent('5 overridden in session');
    expect(row('n')).toHaveTextContent('The session uses 7.');
    expect(row('stray')).toHaveTextContent('not used by this recipe');
    expect(row('user_cache_dir')).toHaveTextContent('./u');
    expect(row('temp_dir')).toHaveTextContent('null');
    expect(within(pane).getByRole('button', { name: 'Open the Run Config pane' })).toBeInTheDocument();
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

const EDITS = `POST /api/sessions/${SID}/edits`;

function edited(session: SessionDocument) {
  return { session, changed: true, changed_hashes: [], entries: [] };
}

/** Open an editable session on a graph, answering edits with the same session. */
function openEditable(graph: Graph, routes: Record<string, unknown> = {}) {
  const session = editableSession({ graph, version: 2 });
  const post = vi.fn((_init: RequestInit | undefined) => edited(session));
  const fetchMock = mockApi({ [`GET /api/sessions/${SID}`]: session, [EDITS]: post, ...routes });
  useSessionStore.getState().setSid(SID);
  return { post, fetchMock };
}

function sent(post: ReturnType<typeof vi.fn>, call = 0): unknown {
  return JSON.parse(String((post.mock.calls[call][0] as RequestInit | undefined)?.body));
}

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

/** with_sub with seed given a parameter of each kind next to its bound n. */
function typesGraph(): Graph {
  const graph = structuredClone(withSubGraph);
  const seed = graph.nodes.find((node) => node.id === 'seed') as StepNode;
  seed.params.push(
    param('gain', 'float', 5, { units: 'dB', description: 'Gain to add.' }),
    param('count', 'int', undefined, { default: 2 }),
    param('flag', 'bool', undefined, { default: false }),
    param('items', 'list', [1]),
    param('out', 'path', '/a', { required: true, constraints: { must_exist: true } }),
    param('label', 'str', 'old'),
    param('extra', null, 3, { fingerprint_mode: null }),
    param('rate', 'float', '.nan'),
  );
  return graph;
}

/** The rows of an Overrides section. */
function overrideRows(section: Element): HTMLDetailsElement[] {
  return [...section.querySelectorAll<HTMLDetailsElement>('details.rc-row-details')];
}

/** nestedInputGraph with leaf_add reading `offset` as a param. */
function offsetGraph(): Graph {
  const graph = nestedInputGraph();
  const leaf = graph.nodes.find((node) => node.id === 'leaf_add') as StepNode;
  leaf.params.push(param('offset', 'int', '${inputs.offset}'));
  return graph;
}

describe('Inspector edit forms', () => {
  it('edits a bound param where its value is set, with no choice to make', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(withSubGraph);
    useView.getState().select({ kind: 'step', id: 'leaf_add' });
    renderWithClient(<Inspector />);

    const value = await screen.findByLabelText('k');
    const field = value.closest('.rc-field') as HTMLElement;
    expect(screen.queryByRole('combobox')).toBeNull();
    expect(value).toHaveValue(5);
    expect(within(field).getByText('Set in with_sub.yaml (override of sub/chain.yaml)')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
    await user.clear(value);
    await user.type(value, '7');
    await user.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(sent(post)).toEqual({
      operations: [{ kind: 'set_include_override', include: 'sub/chain.yaml', name: 'k', value: 7, position: null }],
      version: 2,
    });
    await waitFor(() => expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled());
  });

  it('edits an input default the parent overrides at that override', async () => {
    const user = userEvent.setup();
    const graph = offsetGraph();
    // As the server gives it: the loader puts the override into the step and binds it there.
    const leafInclude = findInclude(graph, LEAF)!;
    leafInclude.overrides.offset = 4;
    leafInclude.bindings = { ...leafInclude.bindings, offset: [{ step: 'leaf_add', slot: 'params.offset' }] };
    const leaf = graph.nodes.find((node) => node.id === 'leaf_add') as StepNode;
    leaf.params.find((field) => field.name === 'offset')!.value = 4;
    const { post } = openEditable(graph);
    useView.getState().select({ kind: 'step', id: 'leaf_add' });
    renderWithClient(<Inspector />);

    const value = await screen.findByLabelText('offset');
    expect(value).toHaveValue(4);
    expect(value.closest('.rc-field')).toHaveTextContent('Set in sub/chain.yaml (override of sub/leaf.yaml)');
    await user.clear(value);
    await user.type(value, '6');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(sent(post)).toMatchObject({
      operations: [{ kind: 'set_include_override', include: 'sub/leaf.yaml', name: 'offset', value: 6 }],
    });
  });

  it('changes a value this session sets through the session inputs', async () => {
    const user = userEvent.setup();
    const session = editableSession({ graph: withSubGraph, version: 2 });
    session.inputs.declared[0] = { ...session.inputs.declared[0], source: 'session', value: 6 };
    session.inputs.values = { n: 6 };
    const post = vi.fn((_init: RequestInit | undefined) => edited(session));
    const put = vi.fn((_init: RequestInit | undefined) => session);
    mockApi({ [`GET /api/sessions/${SID}`]: session, [EDITS]: post, [`PUT /api/sessions/${SID}/inputs`]: put });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const value = await screen.findByLabelText('n');
    expect(value).toHaveValue(6);
    expect(value.closest('.rc-field')).toHaveTextContent('Set in this session');
    await user.clear(value);
    await user.type(value, '8');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(sent(put)).toEqual({ values: { n: 8 } });
    expect(post).not.toHaveBeenCalled();
  });

  it('shows a value the run config sets read only, with a way to the Run Config pane', async () => {
    const user = userEvent.setup();
    const session = editableSession({ graph: withSubGraph, version: 2, run_config: runConfigBlock() });
    session.inputs.declared[0] = { ...session.inputs.declared[0], source: 'config', value: 4 };
    session.inputs.values = { n: 4 };
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    const setActive = vi.fn();
    useDock.setState({ api: { getPanel: () => ({ api: { setActive } }) } as unknown as DockviewApi });
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const line = await screen.findByText(/Set in with_sub\.config\.yaml; edit it in the Run Config pane/);
    const field = line.closest('.rc-field') as HTMLElement;
    expect(field).toHaveTextContent('4');
    expect(field.querySelector('input, textarea')).toBeNull();
    await user.click(within(field).getByRole('button', { name: 'Open Run Config' }));
    expect(setActive).toHaveBeenCalledTimes(1);
    useDock.setState({ api: null });
  });

  it('asks before changing a value other steps use, listing them, and Cancel sends nothing', async () => {
    const user = userEvent.setup();
    const graph = structuredClone(withSubGraph);
    const total = graph.nodes.find((node) => node.id === 'total') as StepNode;
    total.params.push(param('n', 'int', '${inputs.n}'));
    graph.edges.push({ ...graph.edges.find((edge) => edge.source === 'in:n')!, target: 'total' });
    const { post } = openEditable(graph);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const value = await screen.findByLabelText('n');
    expect(value.closest('.rc-field')).toHaveTextContent('Default of input n in with_sub.yaml');
    await user.clear(value);
    await user.type(value, '4');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    const dialog = await screen.findByRole('dialog', { name: 'Shared value' });
    expect(dialog).toHaveTextContent('This value also feeds 1 other step:');
    expect(within(dialog).getByText('Top level')).toBeInTheDocument();
    expect(within(dialog).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['total']);
    expect(within(dialog).getByRole('button', { name: 'Change for all 2 steps' })).toHaveFocus();
    const cancel = within(dialog).getByRole('button', { name: 'Cancel' });
    await user.click(cancel);
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(post).not.toHaveBeenCalled();
    expect(screen.getByLabelText('n')).toHaveValue(4);

    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await user.click(await screen.findByRole('button', { name: 'Change for all 2 steps' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(sent(post)).toMatchObject({
      operations: [{ kind: 'set_recipe_input', document: 'with_sub.yaml', name: 'n', field: 'default', value: 4 }],
    });
  });

  it('asks before a sub-recipe default change only when steps outside it use the value', async () => {
    const user = userEvent.setup();
    const graph = nestedInputGraph();
    graph.edges.push({ ...graph.edges.find((edge) => edge.source === 'in:offset')!, target: 'total' });
    const { post } = openEditable(graph);
    useView.getState().select({ kind: 'include', id: LEAF });
    renderWithClient(<Inspector />);

    const inputs = (await screen.findByRole('heading', { name: 'Inputs' })).closest('section')!;
    const row = inputs.querySelector<HTMLDetailsElement>('details.rc-row-details')!;
    await user.click(row.querySelector('summary')!);
    const offset = within(row).getByLabelText('offset');
    await user.clear(offset);
    await user.type(offset, '3');
    await user.click(within(inputs).getByRole('button', { name: 'Apply' }));
    const dialog = await screen.findByRole('dialog', { name: 'Shared value' });
    expect(dialog).toHaveTextContent('This value also feeds 1 step outside client_leaf:');
    expect(within(dialog).getAllByRole('listitem').map((item) => item.textContent)).toEqual(['total']);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(post).not.toHaveBeenCalled();

    await user.click(within(inputs).getByRole('button', { name: 'Apply' }));
    await user.click(await screen.findByRole('button', { name: 'Change for all 2 steps' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(sent(post)).toMatchObject({
      operations: [{ kind: 'set_recipe_input', document: 'sub/leaf.yaml', name: 'offset', field: 'default', value: 3 }],
    });
  });

  it('gives each type its control, shows units, required, constraints and an undeclared param, and applies once', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(typesGraph());
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const gain = await screen.findByLabelText('gain');
    const field = (name: string) => screen.getByLabelText(name).closest('.rc-field') as HTMLElement;
    expect(gain).toHaveAttribute('type', 'number');
    expect(gain).toHaveValue(5);
    expect(field('gain')).toHaveTextContent('dB');
    expect(field('gain')).toHaveTextContent('Gain to add.');
    expect(within(field('gain')).getByText('set')).toBeInTheDocument();
    expect(within(field('count')).getByText('default')).toBeInTheDocument();
    expect(screen.getByLabelText('count')).toHaveValue(2);
    expect(screen.getByLabelText('flag')).toHaveAttribute('type', 'checkbox');
    expect(screen.getByLabelText('items').tagName).toBe('TEXTAREA');
    expect(screen.getByLabelText('out')).toHaveAttribute('type', 'text');
    expect(within(field('out')).getByText('required')).toBeInTheDocument();
    expect(field('out')).toHaveTextContent('The path must exist.');
    expect(within(field('out')).getByText('fingerprint off')).toHaveAttribute(
      'title',
      'Only the path text goes into the step hash.',
    );
    expect(within(field('extra')).getByText('not declared by the op')).toBeInTheDocument();
    expect(screen.getByLabelText('extra')).toHaveAttribute('type', 'number');
    expect(screen.getByLabelText('rate')).toHaveAttribute('type', 'text');
    expect(field('rate')).toHaveTextContent('NaN and infinity can only be written in the YAML pane.');

    // A whole number typed for a float that holds it changes nothing.
    await user.clear(gain);
    await user.type(gain, '5.0');
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();

    await user.clear(gain);
    await user.type(gain, '2.5');
    await user.clear(screen.getByLabelText('count'));
    await user.type(screen.getByLabelText('count'), '4');
    await user.click(screen.getByLabelText('flag'));
    fireEvent.change(screen.getByLabelText('items'), { target: { value: '[1, 2]' } });
    await user.clear(screen.getByLabelText('out'));
    await user.type(screen.getByLabelText('out'), '/b');
    await user.click(within(field('label')).getByRole('button', { name: 'Reset' }));
    expect(field('label')).toHaveTextContent('reset on apply');
    await user.clear(screen.getByLabelText('extra'));
    await user.type(screen.getByLabelText('extra'), '9');
    await user.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(sent(post)).toEqual({
      operations: [
        { kind: 'set_param', step: 'seed', name: 'gain', value: 2.5, path: [], position: null },
        { kind: 'set_param', step: 'seed', name: 'count', value: 4, path: [], position: null },
        { kind: 'set_param', step: 'seed', name: 'flag', value: true, path: [], position: null },
        { kind: 'set_param', step: 'seed', name: 'items', value: [1, 2], path: [], position: null },
        { kind: 'set_param', step: 'seed', name: 'out', value: '/b', path: [], position: null },
        { kind: 'unset_param', step: 'seed', name: 'label', path: [] },
        { kind: 'set_param', step: 'seed', name: 'extra', value: 9, path: [], position: null },
      ],
      version: 2,
    });
  });

  it('blocks Apply while a draft does not parse, and refuses NaN', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(typesGraph());
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const count = await screen.findByLabelText('count');
    await user.clear(count);
    await user.type(count, '2.5');
    expect(screen.getByText('Enter a whole number.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
    await user.clear(count);
    await user.type(count, '2');
    await user.type(screen.getByLabelText('rate'), '.inf');
    expect(screen.getByText('Enter a number.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();
    fireEvent.submit(screen.getByRole('button', { name: 'Apply' }).closest('form')!);
    expect(post).not.toHaveBeenCalled();
  });

  it('shows the problems of a refused edit inline', async () => {
    const user = userEvent.setup();
    const refused = apiErrorResponse(422, 'EditRejected', 'k must be positive\ntotal: bad input', {
      problems: ['k must be positive', 'total: bad input'],
    });
    openEditable(withSubGraph, { [EDITS]: refused });
    useView.getState().select({ kind: 'step', id: 'leaf_add' });
    renderWithClient(<Inspector />);

    await user.clear(await screen.findByLabelText('k'));
    await user.type(screen.getByLabelText('k'), '-1');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    const items = await screen.findAllByRole('listitem');
    expect(items.map((item) => item.textContent)).toEqual(
      expect.arrayContaining(['k must be positive', 'total: bad input']),
    );
    // The edit is kept for another try.
    expect(screen.getByLabelText('k')).toHaveValue(-1);
  });

  it('shows the validation problems naming a param beside it', async () => {
    const graph = typesGraph();
    const session = editableSession({
      graph,
      validation: {
        valid: false,
        problems: [
          { severity: 'error', step: 'seed', message: "param 'gain' must be below 3" },
          { severity: 'error', step: 'total', message: 'gain is not used' },
        ],
      },
    });
    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);
    const gain = (await screen.findByLabelText('gain')).closest('.rc-field')!;
    expect(gain).toHaveTextContent("param 'gain' must be below 3");
    expect(gain).not.toHaveTextContent('gain is not used');
  });

  it('puts the survey banner above the form of a survey backed step', async () => {
    openEditable(withSubGraph, {
      [`GET /api/sessions/${SID}/preview?targets=leaf_add`]: makePreview([
        previewStep('leaf_add', { held_by: ['survey'], planned: 'load-survey', tier: 'survey' }),
      ]),
      [`GET /api/sessions/${SID}/preview?targets=seed`]: makePreview([previewStep('seed', { held_by: ['user'] })]),
    });
    useView.getState().select({ kind: 'step', id: 'leaf_add' });
    renderWithClient(<Inspector />);

    const banner = await screen.findByText(SURVEY_BANNER);
    const form = document.querySelector('.rc-inspector form')!;
    expect(banner.compareDocumentPosition(form) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    act(() => useView.getState().select({ kind: 'step', id: 'seed' }));
    expect(await screen.findByText('Default of input n in with_sub.yaml')).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText(SURVEY_BANNER)).toBeNull());
  });

  it('keeps the read only table in a read only session', async () => {
    openSession(withSubGraph);
    useView.getState().select({ kind: 'step', id: 'leaf_add' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('k')).toBeInTheDocument();
    expect(screen.getByText('k').closest('tr')).toHaveTextContent('set');
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
  });

  it('gives each override row its control, then its facts, with one Apply for the section', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(withSubGraph);
    useView.getState().select({ kind: 'include', id: CHAIN });
    renderWithClient(<Inspector />);

    const overrides = (await screen.findByRole('heading', { name: 'Overrides' })).closest('section')!;
    expect(screen.getAllByRole('heading', { name: 'Overrides' })).toHaveLength(1);
    expect(screen.getAllByRole('heading', { name: 'Inputs' })).toHaveLength(1);
    expect(screen.queryByRole('heading', { name: 'Edit overrides' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Edit defaults' })).toBeNull();
    expect(within(overrides).getByText(/written in/)).toHaveTextContent(
      'Values the parent sets where it includes this sub-recipe, written in with_sub.yaml.',
    );
    const rows = overrideRows(overrides);
    expect(rows.map((row) => row.querySelector('summary')!.textContent)).toEqual(['source${seed.items}', 'k5']);
    expect(document.querySelectorAll('details.rc-row-details')).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Apply' })).toHaveLength(1);
    expect(within(overrides).getByRole('button', { name: 'Apply' })).toBeDisabled();

    const [source, k] = rows;
    await user.click(source.querySelector('summary')!);
    expect(source).toHaveTextContent('Wired from another step');
    expect(source.querySelector('.rc-field')!.querySelector('button')).toBeNull();
    expect(within(source).getByRole('button', { name: 'seed' })).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: 'Add an override' })).toBeNull();

    await user.click(k.querySelector('summary')!);
    const control = within(k).getByLabelText('k');
    const facts = k.querySelector('.rc-facts')!;
    expect(control.compareDocumentPosition(facts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(facts).toHaveTextContent('used by');
    await user.clear(control);
    await user.type(control, '7');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    // An override reaches only steps inside the sub-recipe, so nothing is asked.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(sent(post)).toMatchObject({
      operations: [{ kind: 'set_include_override', include: 'sub/chain.yaml', name: 'k', value: 7, position: null }],
    });

    const kField = within(k).getByLabelText('k').closest('.rc-field') as HTMLElement;
    await user.click(within(kField).getByRole('button', { name: 'Remove' }));
    expect(kField).toHaveTextContent('removed on apply');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(sent(post, 1)).toMatchObject({
      operations: [{ kind: 'unset_include_override', include: 'sub/chain.yaml', name: 'k' }],
    });
  });

  it('adds an override for a declared input the entry leaves alone, below the rows', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(nestedInputGraph());
    useView.getState().select({ kind: 'inputsBox', id: `overrides:${LEAF}` });
    renderWithClient(<Inspector />);

    const add = await screen.findByRole('combobox', { name: 'Add an override' });
    const overrides = screen.getByRole('heading', { name: 'Overrides' }).closest('section')!;
    expect(overrides).toContainElement(add);
    for (const row of overrideRows(overrides)) {
      expect(row.compareDocumentPosition(add) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }
    expect(within(add).getAllByRole('option').map((option) => option.textContent)).toEqual([
      'Add an override',
      'offset',
    ]);
    // One Apply per section: the overrides and the defaults.
    expect(screen.getAllByRole('button', { name: 'Apply' })).toHaveLength(2);
    // A passed on parent input stays editable as text.
    expect(within(overrides).getByLabelText('k')).toHaveValue('${inputs.k}');
    await user.selectOptions(add, 'offset');
    const added = overrideRows(overrides).find((row) => row.querySelector('summary')!.textContent!.includes('new'))!;
    expect(added.open).toBe(true);
    expect(added.querySelector('summary')).toHaveTextContent('new');
    expect(within(added).getByLabelText('offset')).toHaveValue(2);
    await user.click(within(overrides).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(sent(post)).toMatchObject({
      operations: [{ kind: 'set_include_override', include: 'sub/leaf.yaml', name: 'offset', value: 2 }],
    });
  });

  it('puts the control for a default a sub-recipe declares inside its Inputs row', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(nestedInputGraph());
    useView.getState().select({ kind: 'include', id: LEAF });
    renderWithClient(<Inspector />);

    const inputs = (await screen.findByRole('heading', { name: 'Inputs' })).closest('section')!;
    expect(screen.queryByRole('heading', { name: 'Edit defaults' })).toBeNull();
    const rows = [...inputs.querySelectorAll<HTMLDetailsElement>('details.rc-row-details')];
    expect(rows.map((row) => row.querySelector('summary')!.textContent)).toEqual(['offset2default']);
    const [row] = rows;
    await user.click(row.querySelector('summary')!);
    const offset = within(row).getByLabelText('offset');
    expect(offset).toHaveValue(2);
    const facts = row.querySelector('.rc-facts')!;
    expect(offset.compareDocumentPosition(facts) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(facts).toHaveTextContent('declared insub/leaf.yaml');
    expect(row).toHaveTextContent('Added after k.');
    await user.clear(offset);
    await user.type(offset, '3');
    await user.click(within(inputs).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(sent(post)).toMatchObject({
      operations: [
        { kind: 'set_recipe_input', document: 'sub/leaf.yaml', name: 'offset', field: 'default', value: 3, position: null },
      ],
    });
  });

  it('says a default the parent overrides changes nothing and switches to that override', async () => {
    const user = userEvent.setup();
    const graph = nestedInputGraph();
    findInclude(graph, CHAIN)!.overrides.offset = 4;
    openEditable(graph);
    useView.getState().select({ kind: 'include', id: LEAF });
    renderWithClient(<Inspector />);

    const inputs = (await screen.findByRole('heading', { name: 'Inputs' })).closest('section')!;
    const row = inputs.querySelector<HTMLDetailsElement>('details.rc-row-details')!;
    await user.click(row.querySelector('summary')!);
    const note = within(row).getByText(/The parent sets this input where it includes the file \(override: 4\)/);
    expect(note.closest('.rc-field')).toContainElement(within(row).getByLabelText('offset'));
    await user.click(within(row).getByRole('button', { name: 'Edit the override' }));
    expect(useView.getState().selected).toEqual({ kind: 'include', id: CHAIN });
  });

  it('keeps the read only rows of a sub-recipe in a read only session', async () => {
    openSession(nestedInputGraph());
    useView.getState().select({ kind: 'include', id: LEAF });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('Values the parent sets where it includes this sub-recipe.')).toBeInTheDocument();
    expect(document.querySelector('form')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
    expect(screen.queryByRole('combobox', { name: 'Add an override' })).toBeNull();
  });

  it('shows a description behind an info icon that Escape and an outside click close', async () => {
    const user = userEvent.setup();
    openEditable(typesGraph());
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const gain = (await screen.findByLabelText('gain')).closest('.rc-field') as HTMLElement;
    const count = screen.getByLabelText('count').closest('.rc-field') as HTMLElement;
    expect(count.querySelector('.rc-info')).toBeNull();
    expect(gain.querySelector('.rc-field-help')).toBeNull();
    const info = gain.querySelector<HTMLDetailsElement>('details.rc-info')!;
    const icon = within(gain).getByLabelText('Description of gain');
    expect(icon).toHaveAttribute('title', 'Description of gain');
    expect(icon.closest('.rc-field-head')).toBeInTheDocument();
    expect(info.open).toBe(false);

    await user.click(icon);
    expect(info.open).toBe(true);
    expect(info.querySelector('.rc-info-card')).toHaveTextContent('Gain to add.');
    const shortcut = vi.fn();
    window.addEventListener('keydown', shortcut);
    await user.keyboard('{Escape}');
    window.removeEventListener('keydown', shortcut);
    expect(info.open).toBe(false);
    // The window's shortcuts (Escape clears the selection) do not see it.
    expect(shortcut).not.toHaveBeenCalled();

    await user.click(icon);
    expect(info.open).toBe(true);
    await user.click(info.querySelector('.rc-info-card')!);
    expect(info.open).toBe(true);
    await user.click(screen.getByLabelText('count'));
    expect(info.open).toBe(false);
    await user.click(icon);
    await user.click(icon);
    expect(info.open).toBe(false);
  });

  it('closes a description card on Escape and an outside click in a popout window', async () => {
    openEditable(typesGraph());
    useView.getState().select({ kind: 'step', id: 'seed' });
    const frame = document.createElement('iframe');
    document.body.appendChild(frame);
    const doc = frame.contentDocument!;
    const container = doc.body.appendChild(doc.createElement('div'));
    render(
      <QueryClientProvider client={testQueryClient()}>
        <Inspector />
      </QueryClientProvider>,
      { container },
    );

    const icon = await within(container).findByLabelText('Description of gain');
    const info = icon.closest('details')!;
    fireEvent.click(icon);
    expect(info.open).toBe(true);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(info.open).toBe(true);
    fireEvent.keyDown(doc.body, { key: 'Escape' });
    expect(info.open).toBe(false);
    fireEvent.click(icon);
    expect(info.open).toBe(true);
    fireEvent.pointerDown(within(container).getByLabelText('count'));
    expect(info.open).toBe(false);
    frame.remove();
  });

  it('marks both of a mutually exclusive pair being edited and blocks Apply until one is reset', async () => {
    const user = userEvent.setup();
    const graph = structuredClone(withSubGraph);
    const seed = graph.nodes.find((node) => node.id === 'seed') as StepNode;
    seed.params.push(
      param('size', 'int', 10, { constraints: { exclusive_with: ['fraction'] } }),
      param('fraction', 'float', 0.01, { constraints: { exclusive_with: ['size'] } }),
    );
    openEditable(graph);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const size = (await screen.findByLabelText('size')).closest('.rc-field') as HTMLElement;
    const fraction = screen.getByLabelText('fraction').closest('.rc-field') as HTMLElement;
    expect(size).toHaveTextContent('Mutually exclusive with fraction.');
    expect(fraction).toHaveTextContent('Mutually exclusive with size.');
    expect(size).not.toHaveTextContent('Set only one of');

    await user.clear(within(size).getByLabelText('size'));
    await user.type(within(size).getByLabelText('size'), '12');
    expect(within(size).getByText('Set only one of size and fraction.')).toHaveClass('rc-field-error');
    expect(within(fraction).getByText('Set only one of size and fraction.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Apply' })).toBeDisabled();

    await user.click(within(fraction).getByRole('button', { name: 'Reset' }));
    expect(size).not.toHaveTextContent('Set only one of');
    expect(screen.getByRole('button', { name: 'Apply' })).toBeEnabled();
  });

  it('sends a recipe input default to its file before the session values', async () => {
    const user = userEvent.setup();
    const put = vi.fn((_init: RequestInit | undefined) => editableSession());
    const { post, fetchMock } = openEditable(withSubGraph, { [`PUT /api/sessions/${SID}/inputs`]: put });
    useView.getState().select({ kind: 'inputsBox', id: 'inputs' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('Recipe inputs')).toBeInTheDocument();
    const row = document.querySelector<HTMLDetailsElement>('details.rc-row-details')!;
    await user.click(row.querySelector('summary')!);
    await user.click(within(row).getByRole('button', { name: 'Change the default in with_sub.yaml' }));
    const fallback = within(row).getByLabelText('default in with_sub.yaml');
    expect(fallback).toHaveValue(3);
    await user.clear(fallback);
    await user.type(fallback, '4');
    const value = within(row).getByLabelText('n');
    await user.clear(value);
    await user.type(value, '5');
    await user.click(screen.getByRole('button', { name: 'Apply' }));

    await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
    expect(sent(post)).toMatchObject({
      operations: [
        { kind: 'set_recipe_input', document: 'with_sub.yaml', name: 'n', field: 'default', value: 4, position: null },
      ],
    });
    expect(sent(put)).toEqual({ values: { n: 5 } });
    const order = fetchMock.mock.calls
      .map(([url, init]) => `${init?.method ?? 'GET'} ${url}`)
      .filter((key) => key === EDITS || key.startsWith('PUT'));
    expect(order).toEqual([EDITS, `PUT /api/sessions/${SID}/inputs`]);
  });
});

/** with_sub with total also reading input n, whose default is `n`. */
function sharedGraph(n = 3): Graph {
  const graph = structuredClone(withSubGraph);
  const total = graph.nodes.find((node) => node.id === 'total') as StepNode;
  total.params.push(param('n', 'int', '${inputs.n}'));
  graph.edges.push({ ...graph.edges.find((edge) => edge.source === 'in:n')!, target: 'total' });
  const input = graph.nodes.find((node) => node.id === 'in:n');
  if (input?.kind === 'input') input.default = n;
  return graph;
}

/** An editable session on sharedGraph with the default of n at `n`. */
function sharedSession(n: number, version: number): SessionDocument {
  const session = editableSession({ graph: sharedGraph(n), version });
  session.inputs.declared[0] = { ...session.inputs.declared[0], default: n, value: n };
  session.inputs.values = { n };
  return session;
}

/** The field holding a labelled control. */
function fieldOf(label: string, container: HTMLElement = document.body): HTMLElement {
  return within(container).getByLabelText(label).closest('.rc-field') as HTMLElement;
}

function notApplied(field: HTMLElement): HTMLElement | null {
  return within(field).queryByText('not applied');
}

/** Type 4 into the seed step's n and press Apply, which asks first. */
async function applySharedN(user: ReturnType<typeof userEvent.setup>): Promise<HTMLElement> {
  const value = await screen.findByLabelText('n');
  expect(notApplied(fieldOf('n'))).toBeNull();
  await user.clear(value);
  await user.type(value, '4');
  await user.click(screen.getByRole('button', { name: 'Apply' }));
  return screen.findByRole('dialog', { name: 'Shared value' });
}

describe('edits not applied', () => {
  it('focuses Change for all, so Apply then Enter confirms, and updates the other step', async () => {
    const user = userEvent.setup();
    const post = vi.fn((_init: RequestInit | undefined) => edited(sharedSession(4, 3)));
    mockApi({ [`GET /api/sessions/${SID}`]: sharedSession(3, 2), [EDITS]: post });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const dialog = await applySharedN(user);
    expect(within(dialog).getByRole('button', { name: 'Change for all 2 steps' })).toHaveFocus();
    expect(notApplied(fieldOf('n'))).not.toBeNull();
    await user.keyboard('{Enter}');
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('dialog')).toBeNull();
    await waitFor(() => expect(notApplied(fieldOf('n'))).toBeNull());
    expect(screen.getByLabelText('n')).toHaveValue(4);

    act(() => useView.getState().select({ kind: 'step', id: 'total' }));
    await waitFor(() => expect(fieldOf('n')).toHaveTextContent('Default of input n'));
    expect(screen.getByLabelText('n')).toHaveValue(4);
    expect(notApplied(fieldOf('n'))).toBeNull();
  });

  it('keeps the shared value dialog open on a click outside it', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(sharedGraph());
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);

    const dialog = await applySharedN(user);
    fireEvent.mouseDown(dialog.parentElement!);
    fireEvent.click(dialog.parentElement!);
    expect(screen.getByRole('dialog', { name: 'Shared value' })).toBe(dialog);
    expect(post).not.toHaveBeenCalled();
  });

  it.each(['Cancel', 'Escape'])(
    'marks the draft not applied after %s closes the dialog, leaving the other step as it was',
    async (how) => {
      const user = userEvent.setup();
      const { post } = openEditable(sharedGraph());
      useView.getState().select({ kind: 'step', id: 'seed' });
      renderWithClient(<Inspector />);

      const dialog = await applySharedN(user);
      if (how === 'Cancel') await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
      else await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(post).not.toHaveBeenCalled();
      expect(screen.getByLabelText('n')).toHaveValue(4);
      expect(notApplied(fieldOf('n'))).toHaveAttribute('title', 'Apply sends this change.');

      // The marker goes once the draft equals the value again.
      await user.clear(screen.getByLabelText('n'));
      await user.type(screen.getByLabelText('n'), '3');
      expect(notApplied(fieldOf('n'))).toBeNull();
      await user.clear(screen.getByLabelText('n'));
      await user.type(screen.getByLabelText('n'), '4');
      expect(notApplied(fieldOf('n'))).not.toBeNull();
      await user.click(screen.getByRole('button', { name: 'Discard changes' }));
      expect(notApplied(fieldOf('n'))).toBeNull();

      act(() => useView.getState().select({ kind: 'step', id: 'total' }));
      await waitFor(() => expect(fieldOf('n')).toHaveTextContent('Default of input n'));
      expect(screen.getByLabelText('n')).toHaveValue(3);
      expect(notApplied(fieldOf('n'))).toBeNull();
    },
  );

  it('says inline that a stale version was not applied and keeps the draft marked', async () => {
    const user = userEvent.setup();
    openEditable(withSubGraph, {
      [EDITS]: () => apiErrorResponse(409, 'StaleVersion', 'The session is at version 3.', { version: 3 }),
    });
    useView.getState().select({ kind: 'step', id: 'leaf_add' });
    renderWithClient(<Inspector />);

    await user.clear(await screen.findByLabelText('k'));
    await user.type(screen.getByLabelText('k'), '7');
    await user.click(screen.getByRole('button', { name: 'Apply' }));
    const form = screen.getByLabelText('k').closest('form')!;
    expect(
      await within(form).findByText(
        'The recipe changed in another tab; the edit was not applied. Apply again to retry.',
      ),
    ).toHaveClass('rc-field-error');
    expect(useEditingStore.getState().error).toBe(STALE_VERSION_MESSAGE);
    expect(screen.getByLabelText('k')).toHaveValue(7);
    expect(notApplied(fieldOf('k'))).not.toBeNull();
  });

  it('marks a changed or added override not applied until it is applied', async () => {
    const user = userEvent.setup();
    const { post } = openEditable(nestedInputGraph());
    useView.getState().select({ kind: 'inputsBox', id: `overrides:${LEAF}` });
    renderWithClient(<Inspector />);

    const add = await screen.findByRole('combobox', { name: 'Add an override' });
    const overrides = screen.getByRole('heading', { name: 'Overrides' }).closest('section')!;
    const k = within(overrides).getByLabelText('k');
    expect(notApplied(fieldOf('k', overrides))).toBeNull();
    await user.clear(k);
    await user.type(k, '9');
    expect(notApplied(fieldOf('k', overrides))).not.toBeNull();
    await user.clear(k);
    await user.type(k, '${{inputs.k}');
    expect(notApplied(fieldOf('k', overrides))).toBeNull();

    await user.selectOptions(add, 'offset');
    expect(notApplied(fieldOf('offset', overrides))).not.toBeNull();
    await user.click(within(overrides).getByRole('button', { name: 'Apply' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(within(overrides).queryByText('not applied')).toBeNull());
  });

  it('marks a changed sub-recipe default not applied, and Cancel in the dialog keeps the mark', async () => {
    const user = userEvent.setup();
    const graph = nestedInputGraph();
    graph.edges.push({ ...graph.edges.find((edge) => edge.source === 'in:offset')!, target: 'total' });
    const { post } = openEditable(graph);
    useView.getState().select({ kind: 'include', id: LEAF });
    renderWithClient(<Inspector />);

    const inputs = (await screen.findByRole('heading', { name: 'Inputs' })).closest('section')!;
    const row = inputs.querySelector<HTMLDetailsElement>('details.rc-row-details')!;
    await user.click(row.querySelector('summary')!);
    const offset = within(row).getByLabelText('offset');
    expect(notApplied(fieldOf('offset', row))).toBeNull();
    await user.clear(offset);
    await user.type(offset, '3');
    expect(notApplied(fieldOf('offset', row))).not.toBeNull();
    await user.click(within(inputs).getByRole('button', { name: 'Apply' }));
    const dialog = await screen.findByRole('dialog', { name: 'Shared value' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(post).not.toHaveBeenCalled();
    expect(notApplied(fieldOf('offset', row))).not.toBeNull();

    await user.click(within(inputs).getByRole('button', { name: 'Apply' }));
    await user.click(await screen.findByRole('button', { name: 'Change for all 2 steps' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(notApplied(fieldOf('offset', row))).toBeNull());
  });

  it('marks a recipe input draft and a default draft not applied in the inputs box', async () => {
    const user = userEvent.setup();
    openEditable(withSubGraph);
    useView.getState().select({ kind: 'inputsBox', id: 'inputs' });
    renderWithClient(<Inspector />);

    expect(await screen.findByText('Recipe inputs')).toBeInTheDocument();
    const row = document.querySelector<HTMLDetailsElement>('details.rc-row-details')!;
    await user.click(row.querySelector('summary')!);
    await user.click(within(row).getByRole('button', { name: 'Change the default in with_sub.yaml' }));
    expect(notApplied(fieldOf('default in with_sub.yaml', row))).toBeNull();
    await user.clear(within(row).getByLabelText('default in with_sub.yaml'));
    await user.type(within(row).getByLabelText('default in with_sub.yaml'), '4');
    expect(notApplied(fieldOf('default in with_sub.yaml', row))).not.toBeNull();
    expect(notApplied(fieldOf('n', row))).toBeNull();
    await user.clear(within(row).getByLabelText('n'));
    await user.type(within(row).getByLabelText('n'), '5');
    expect(notApplied(fieldOf('n', row))).not.toBeNull();
  });
});

describe('InspectorBody', () => {
  const site = { docsUrl: 'https://site.example/docs/', linkTarget: '_self' as const, pages: {} };

  it('has nothing to show for a run config selection on the site', () => {
    render(<InspectorBody graph={withSubGraph} selected={{ kind: 'runConfig', id: 'runConfig' }} drill={[]} />);
    expect(screen.getByText('The selected node is not in this recipe.')).toBeInTheDocument();
  });

  it('keeps the read only parameter table on the site, with no form', () => {
    render(<InspectorBody graph={withSubGraph} selected={{ kind: 'step', id: 'leaf_add' }} drill={[]} />);
    expect(screen.getByText('k').closest('tr')).toHaveTextContent('5');
    expect(document.querySelector('form')).toBeNull();
  });

  it('keeps the read only Overrides and Inputs rows of a sub-recipe on the site', () => {
    render(<InspectorBody graph={nestedInputGraph()} selected={{ kind: 'include', id: LEAF }} drill={[]} />);
    expect(screen.getByText('Values the parent sets where it includes this sub-recipe.')).toBeInTheDocument();
    expect(screen.getByText(/declares; each shows the value a run would use\./)).toHaveTextContent(
      'What sub/leaf.yaml declares; each shows the value a run would use.',
    );
    const rows = [...document.querySelectorAll('details.rc-row-details')];
    expect(rows.map((row) => row.querySelector('summary')!.textContent)).toEqual([
      'value${chain_inc.out}',
      'k${inputs.k} = 5',
      'offset2default',
    ]);
    expect(rows[2]).toHaveTextContent('Added after k.');
    expect(rows[2]).toHaveTextContent('(the default)');
    expect(document.querySelector('form')).toBeNull();
    expect(document.querySelector('input, select, textarea')).toBeNull();
    expect(document.querySelector('.rc-info')).toBeNull();
    expect(screen.queryByRole('button', { name: 'Apply' })).toBeNull();
  });

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

describe('the Cache section checkpoint toggle', () => {
  beforeEach(() => {
    useSessionStore.getState().setSid(null);
    useView.getState().resetScope();
  });

  it('sets checkpoint always, or removes it, as the save icon does', async () => {
    const user = userEvent.setup();
    const graph = structuredClone(withSubGraph);
    const total = graph.nodes.find((node) => node.id === 'total') as StepNode;
    total.checkpoint = 'always';
    const { post } = openEditable(graph);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);
    await user.click(await screen.findByRole('button', { name: 'Make this a checkpoint' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
    expect(sent(post)).toEqual({
      operations: [{ kind: 'set_checkpoint', step: 'seed', value: 'always', position: null }],
      version: 2,
    });

    act(() => useView.getState().select({ kind: 'step', id: 'total' }));
    await user.click(await screen.findByRole('button', { name: 'Remove the checkpoint' }));
    await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
    expect(sent(post, 1)).toMatchObject({ operations: [{ kind: 'set_checkpoint', step: 'total', value: null }] });
  });

  it('is absent in a read only session', async () => {
    mockApi({ [`GET /api/sessions/${SID}`]: makeSession() });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'seed' });
    renderWithClient(<Inspector />);
    expect(await screen.findByText('Cache')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Make this a checkpoint' })).toBeNull();
  });
});
