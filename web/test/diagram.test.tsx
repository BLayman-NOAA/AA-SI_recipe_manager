// React Flow renders in jsdom with the flowStubs measurement stand-ins; jsdom
// has no Worker, so the diagram takes the grid layout fallback.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { Profiler, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { buildView } from '../src/diagram/buildView';
import { Diagram } from '../src/diagram/Diagram';
import { DiagramView, type DiagramViewProps } from '../src/diagram/DiagramView';
import { eyeLook } from '../src/diagram/nodes/StepNode';
import { estimateSize } from '../src/diagram/layout';
import { arrowPath } from '../src/diagram/edges/RouteEdge';
import { edgeTooltip, keepInPlace, layoutKey, resized } from '../src/diagram/useLayout';
import type { StepBadge } from '../src/runs/badges';
import { useRunStore } from '../src/runs/store';
import { RunControlsContext, useRunControls } from '../src/runs/useRunControls';
import { useSessionStore } from '../src/state/session';
import { createViewStore, useView, ViewStoreContext } from '../src/state/view';
import { laidOut, nodeElement, installFlowStubs } from './flowStubs';
import {
  declaredGraph,
  makeSession,
  mappedInputGraph,
  mockApi,
  nestedInputGraph,
  renderWithClient,
  withSubGraph,
} from './helpers';

const SID = '20261013-153012-a1b2c3';
const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';

installFlowStubs();

/** The run controls the Shell provides. */
function WithRunControls({ children }: { children: ReactNode }) {
  return <RunControlsContext.Provider value={useRunControls()}>{children}</RunControlsContext.Provider>;
}

describe('Diagram', () => {
  beforeEach(() => {
    useSessionStore.getState().setSid(null);
    useView.getState().resetScope();
    useView.setState({ connected: false, direction: 'RIGHT', textSize: 1 });
  });

  it('asks for a recipe when no session is open', () => {
    renderWithClient(<Diagram />);
    expect(screen.getByText(/Open a recipe from the Recipes pane/)).toBeInTheDocument();
  });

  describe('with a session', () => {
    beforeEach(() => {
      mockApi({ [`GET /api/sessions/${SID}`]: makeSession() });
      useSessionStore.getState().setSid(SID);
    });

    it('draws the collapsed recipe with an include box, the boxes and a breadcrumb', async () => {
      renderWithClient(<Diagram />);
      await laidOut();
      expect(nodeElement(CHAIN)).toHaveTextContent('client_chain');
      expect(nodeElement(CHAIN)).toHaveTextContent('2 steps, 2 overrides');
      // No preview yet: no summary element at all, rather than a blank one.
      expect(nodeElement(CHAIN).querySelector('.rc-include-summary')).toBeNull();
      expect(nodeElement('total')).toHaveTextContent('collects');
      expect(nodeElement('inputs')).toHaveTextContent('Recipe inputs');
      expect(nodeElement('inputs')).toHaveTextContent('1 input');
      // with_sub declares no recipe outputs, so the top level has no outputs box.
      expect(document.querySelector('.react-flow__node[data-id="cout:total"]')).toBeNull();
      expect(screen.getByLabelText('Legend')).not.toHaveTextContent('Outputs');
      expect(screen.getByLabelText('Breadcrumb')).toHaveTextContent('client_with_sub');
      expect(screen.queryByRole('button', { name: 'Up one level' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Combine' })).toBeNull();
      expect(screen.queryByRole('button', { name: 'Expand in place' })).toBeNull();
      expect(screen.getByLabelText('Legend')).toHaveTextContent('Inputs');
    });

    it('draws the play icon on every step', async () => {
      renderWithClient(<Diagram />);
      await laidOut();
      expect(within(nodeElement('seed')).getByRole('button', { name: 'Run to here' })).toHaveClass('rc-play');
    });

    it('draws edges along routes, box lines and their labels with their own class', async () => {
      mockApi({ [`GET /api/sessions/${SID}`]: makeSession({ graph: declaredGraph() }) });
      renderWithClient(<Diagram />);
      await laidOut();
      await waitFor(() => expect(document.querySelector('.react-flow__edge.rc-edge-bus-in')).not.toBeNull());
      expect(document.querySelector('.react-flow__edge.rc-edge-bus-out')).not.toBeNull();
      expect(document.querySelector('.rc-edge-label.rc-edge-label-in')).toHaveTextContent('n');
      expect(document.querySelector('.rc-edge-label.rc-edge-label-out')).toHaveTextContent('total');
    });

    it('draws a box and line dotted when no output of the step is used later', async () => {
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
      mockApi({ [`GET /api/sessions/${SID}`]: makeSession({ graph }) });
      useView.setState({ drill: [CHAIN] });
      renderWithClient(<Diagram />);
      await laidOut();
      expect(nodeElement('cout:chain_inc').firstElementChild).toHaveClass('rc-box-muted');
      expect(nodeElement('cout:chain_inc')).toHaveTextContent('1 unused');
      await waitFor(() => expect(document.querySelector('.react-flow__edge.rc-edge-muted')).not.toBeNull());
      expect(document.querySelector('.rc-edge-label.rc-edge-label-muted')).toHaveTextContent('extra');
    });

    it('draws the drill button like the chevron and the text at the chosen size', async () => {
      useView.setState({ textSize: 1.3 });
      renderWithClient(<Diagram />);
      await laidOut();
      const box = within(nodeElement(CHAIN));
      expect(box.getByRole('button', { name: 'Drill in' })).toHaveClass('rc-node-button');
      expect(box.getByRole('button', { name: 'Show inputs' })).toHaveClass('rc-node-button');
      const canvas = document.querySelector<HTMLElement>('.rc-diagram-canvas')!;
      expect(canvas.style.getPropertyValue('--rc-text-scale')).toBe('1.3');
    });

    it('lists a sub-recipe box overrides from its chevron', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();
      await user.click(within(nodeElement(CHAIN)).getByRole('button', { name: 'Show inputs' }));
      expect(useView.getState().expandedBoxes.has(CHAIN)).toBe(true);
      await waitFor(() => expect(nodeElement(CHAIN).querySelector('.rc-box-rows')).not.toBeNull());
      const rows = nodeElement(CHAIN).querySelector('.rc-box-rows')!;
      expect(rows).toHaveTextContent('source');
      expect(rows).toHaveTextContent('${seed.items}');
      expect(rows).toHaveTextContent('from seed');
      expect(rows).toHaveTextContent('k5');
      // Every input of chain.yaml is overridden, so only the Overrides section shows.
      expect([...rows.querySelectorAll('.rc-box-section')].map((item) => item.textContent)).toEqual(['Overrides']);
      expect(nodeElement(CHAIN).querySelector('.rc-include-meta')).toHaveTextContent(/^2 steps, 2 overrides$/);
    });

    it('expands and collapses everything in view from the bar', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();
      await user.click(screen.getByRole('button', { name: 'Expand all' }));
      await waitFor(() => expect(useView.getState().expandedSteps.has('seed')).toBe(true));
      expect([...useView.getState().expandedSteps].sort()).toEqual(['seed', 'total']);
      expect([...useView.getState().expandedBoxes].sort()).toEqual([CHAIN, 'inputs']);
      await user.click(screen.getByRole('button', { name: 'Collapse all' }));
      await waitFor(() => expect(useView.getState().expandedSteps.size).toBe(0));
      expect(useView.getState().expandedBoxes.size).toBe(0);
    });

    it('expands the inputs box from its chevron and selects the box on click', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();
      expect(nodeElement('inputs').querySelector('.rc-box-rows')).toBeNull();
      await user.click(within(nodeElement('inputs')).getByRole('button', { name: 'Show inputs' }));
      expect(useView.getState().expandedBoxes.has('inputs')).toBe(true);
      await waitFor(() => expect(nodeElement('inputs').querySelector('.rc-box-rows')).not.toBeNull());
      expect(nodeElement('inputs').querySelector('.rc-box-name')).toHaveTextContent('n');
      expect(nodeElement('inputs').querySelector('.rc-box-value')).toHaveTextContent('3');
      expect(useView.getState().selected).toBeNull();

      fireEvent.click(nodeElement('inputs'));
      expect(useView.getState().selected).toEqual({ kind: 'inputsBox', id: 'inputs' });
    });

    it('toggles a box on double click', async () => {
      mockApi({ [`GET /api/sessions/${SID}`]: makeSession({ graph: declaredGraph() }) });
      renderWithClient(<Diagram />);
      await laidOut();
      fireEvent.doubleClick(nodeElement('inputs'));
      expect(useView.getState().expandedBoxes.has('inputs')).toBe(true);
      fireEvent.doubleClick(nodeElement('cout:total'));
      expect(useView.getState().expandedBoxes.has('cout:total')).toBe(true);
      fireEvent.doubleClick(nodeElement('inputs'));
      expect(useView.getState().expandedBoxes.has('inputs')).toBe(false);
    });

    it('draws an arrowhead at the target end of every edge', async () => {
      renderWithClient(<Diagram />);
      await laidOut();
      await waitFor(() => expect(document.querySelector('.react-flow__edge')).not.toBeNull());
      for (const edge of document.querySelectorAll('.react-flow__edge')) {
        const arrow = edge.querySelector('path.rc-edge-arrow')?.getAttribute('d');
        expect(arrow, edge.getAttribute('data-id') ?? '').toMatch(/^M /);
      }
    });

    it('lists a step parameters from its chevron or a double click', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();
      await user.click(within(nodeElement('seed')).getByRole('button', { name: 'Show parameters' }));
      await waitFor(() => expect(nodeElement('seed').querySelector('.rc-step-params')).not.toBeNull());
      const params = nodeElement('seed').querySelector('.rc-step-params')!;
      expect(params).toHaveTextContent('n');
      expect(params).toHaveTextContent('${inputs.n}');

      fireEvent.doubleClick(nodeElement('seed'));
      expect(useView.getState().expandedSteps.has('seed')).toBe(false);
      fireEvent.doubleClick(nodeElement('seed'));
      expect(useView.getState().expandedSteps.has('seed')).toBe(true);
    });

    it('selects on click, expands then drills in on double click, and goes up from the breadcrumb', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();

      fireEvent.click(nodeElement('seed'));
      expect(useView.getState().selected).toEqual({ kind: 'step', id: 'seed' });

      fireEvent.doubleClick(nodeElement(CHAIN));
      expect(useView.getState().expandedBoxes.has(CHAIN)).toBe(true);
      expect(useView.getState().drill).toEqual([]);
      await waitFor(() => expect(nodeElement(CHAIN).querySelector('.rc-box-rows')).not.toBeNull());
      fireEvent.doubleClick(nodeElement(CHAIN));
      expect(useView.getState().drill).toEqual([CHAIN]);
      await waitFor(() => nodeElement('ghost:seed'));
      expect(screen.getByLabelText('Breadcrumb')).toHaveTextContent('client_with_sub>client_chain');
      expect(nodeElement(`overrides:${CHAIN}`).querySelector('.rc-box-title')).toHaveTextContent(/^Inputs$/);
      expect(nodeElement(`overrides:${CHAIN}`).querySelector('.rc-box-count')).toHaveTextContent(/^2 overrides$/);
      expect(nodeElement(`cout:${LEAF}`)).toHaveTextContent('Outputs client_leaf');

      await user.click(screen.getByRole('button', { name: 'Up one level' }));
      expect(useView.getState().drill).toEqual([]);

      // Drilling in left the box collapsed, so the first double click expands it again.
      expect(useView.getState().expandedBoxes.has(CHAIN)).toBe(false);
      fireEvent.doubleClick(nodeElement(CHAIN));
      expect(useView.getState().drill).toEqual([]);
      await waitFor(() => expect(nodeElement(CHAIN).querySelector('.rc-box-rows')).not.toBeNull());
      fireEvent.doubleClick(nodeElement(CHAIN));
      await waitFor(() => nodeElement('ghost:seed'));
      await user.click(screen.getByRole('button', { name: 'client_with_sub' }));
      expect(useView.getState().drill).toEqual([]);
    });

    it('finds a step by id, drilling to it and selecting it', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();
      await user.type(screen.getByLabelText('Find step'), 'leaf_add{Enter}');
      expect(useView.getState().selected).toEqual({ kind: 'step', id: 'leaf_add' });
      expect(useView.getState().drill).toEqual([CHAIN, LEAF]);
      await waitFor(() => nodeElement('leaf_add'));
      expect(screen.getByText('1 match')).toBeInTheDocument();
    });

    it('toggles highlight connected from its chip', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();
      await user.click(screen.getByRole('button', { name: 'Highlight connected' }));
      expect(useView.getState().connected).toBe(true);
      act(() => useView.getState().select({ kind: 'step', id: 'seed' }));
      await waitFor(() => expect(nodeElement('total').querySelector('.rc-dimmed')).not.toBeNull());
    });

    it('draws run marks from the preview and the run without moving a node, and opens a failed step log', async () => {
      const preview = {
        recipe: 'client_with_sub',
        version: '1.0',
        mode: 'all',
        targets: [],
        force_steps: [],
        tiers: { user: '/u', survey: '/s' },
        write_tier: 'user',
        steps: ['seed', 'chain_inc', 'leaf_add', 'total'].map((id) => ({
          step_id: id,
          step_hash: `hash-${id}`,
          held_by: id === 'total' ? [] : ['survey'],
          planned: id === 'total' ? 'compute' : 'load-survey',
          tier: id === 'total' ? null : 'survey',
          checkpoint: id === 'total' ? 'will-save' : 'saved',
          pinned: false,
          recipe_checkpoint: null,
          fanned_out: false,
          instances: null,
          instances_held: {},
          note: null,
        })),
        blockers: [],
        warnings: [],
      };
      mockApi({
        [`GET /api/sessions/${SID}`]: makeSession(),
        [`GET /api/sessions/${SID}/preview`]: preview,
      });
      useRunStore.getState().resetFor(SID);
      renderWithClient(<Diagram />);
      await laidOut();
      const step = (id: string) => nodeElement(id).querySelector<HTMLElement>('.rc-step')!;
      await waitFor(() => expect(step('seed')).toHaveClass('rc-pred-survey', 'rc-survey-backed'));
      expect(nodeElement(CHAIN)).toHaveTextContent('2 survey');
      expect(step('total')).toHaveClass('rc-pred-compute');
      expect(step('total').querySelector('.rc-run-pending')).not.toBeNull();
      expect(step('seed').querySelector('.rc-letter-S')).toHaveTextContent('S');
      expect(screen.getByLabelText('Legend')).toHaveTextContent('Survey cache (S)');
      const before = nodeElement('total').style.transform;

      act(() => {
        const store = useRunStore.getState();
        store.begin('r1');
        store.apply('r1', [
          { seq: 1, time: 1, kind: 'step_finished', run_id: 'x', data: { step: 'seed', disposition: 'hit-survey-cache', tier: 'survey', hash: 'hash-seed', index: 1, total: 2 } },
          { seq: 2, time: 2, kind: 'step_failed', run_id: 'x', data: { step: 'total', index: 2, total: 2, error_type: 'RuntimeError', message: 'boom', traceback: 'tb', chain: null } },
          { seq: 3, time: 3, kind: 'run_finished', run_id: 'x', data: { status: 'failed', executed: [], skipped: [], pruned: [], untargeted: [] } },
        ]);
      });
      await waitFor(() => expect(step('total')).toHaveClass('rc-actual-failed'));
      expect(step('seed')).toHaveClass('rc-actual-hit-survey-cache');
      expect(nodeElement('total').style.transform).toBe(before);
      expect(screen.getByLabelText('Legend')).toHaveTextContent('Failed (click for its log)');

      fireEvent.click(step('total'));
      expect(useView.getState().selected).toEqual({ kind: 'step', id: 'total' });
      expect(useRunStore.getState().logStep).toBe('total');
    });

    it("shows a sub-recipe's progress in the run going without moving a node", async () => {
      useRunStore.getState().resetFor(SID);
      renderWithClient(<Diagram />);
      await laidOut();
      const mark = () => nodeElement(CHAIN).querySelector('.rc-include-summary');
      expect(mark()).toBeNull();
      // The layout from estimates is redone once the nodes are measured; wait for it to settle.
      const transforms = () => [CHAIN, 'total'].map((id) => nodeElement(id).style.transform).join(' ');
      let before = '';
      await waitFor(
        () => {
          const now = transforms();
          const settled = now === before;
          before = now;
          expect(settled).toBe(true);
        },
        { interval: 100 },
      );
      const finished = (seq: number, step: string) => ({
        seq,
        time: seq,
        kind: 'step_finished',
        run_id: 'x',
        data: { step, disposition: 'computed', tier: null, hash: `h-${step}`, index: seq, total: 4 },
      });

      act(() => {
        const store = useRunStore.getState();
        store.begin('r1');
        store.apply('r1', [
          finished(1, 'seed'),
          finished(2, 'chain_inc'),
          { seq: 3, time: 3, kind: 'step_started', run_id: 'x', data: { step: 'leaf_add', index: 3, total: 4, instances: null, chain: null } },
        ]);
      });
      await waitFor(() => expect(mark()).toHaveTextContent('1 of 2 done'));
      expect(mark()?.querySelector('.rc-run-progress')).not.toBeNull();

      act(() => useRunStore.getState().apply('r1', [finished(4, 'leaf_add')]));
      await waitFor(() => expect(mark()).toHaveTextContent('complete'));
      expect(mark()?.querySelector('.rc-include-complete')).not.toBeNull();
      expect(transforms()).toBe(before);

      act(() =>
        useRunStore.getState().apply('r1', [
          { seq: 5, time: 5, kind: 'run_finished', run_id: 'x', data: { status: 'completed', executed: [], skipped: [], pruned: [], untargeted: [] } },
        ]),
      );
      await waitFor(() => expect(mark()).toBeNull());
    });

    it('does not render again for a batch of log events', async () => {
      const store = useRunStore.getState();
      store.resetFor(SID);
      store.begin('r1');
      store.apply('r1', [
        { seq: 1, time: 1, kind: 'step_started', run_id: 'x', data: { step: 'seed', index: 1, total: 1, instances: null, chain: null } },
      ]);
      let commits = 0;
      renderWithClient(
        <Profiler id="diagram" onRender={() => (commits += 1)}>
          <Diagram />
        </Profiler>,
      );
      await laidOut();
      await waitFor(() => expect(nodeElement('seed').querySelector('.rc-running')).not.toBeNull());
      expect(within(nodeElement('seed')).getByRole('img', { name: 'Running' })).toHaveClass('rc-run-spinner');
      const before = commits;
      act(() =>
        useRunStore.getState().apply('r1', [
          { seq: 2, time: 2, kind: 'log', run_id: 'x', data: { step: 'seed', stream: 'stdout', text: 'hi', buffered: false } },
        ]),
      );
      expect(useRunStore.getState().current?.logs.map((entry) => entry.stream)).toEqual(['progress', 'stdout']);
      expect(commits).toBe(before);
    });

    it('runs to a step from its play icon', async () => {
      const user = userEvent.setup();
      const fetchMock = mockApi({
        [`GET /api/sessions/${SID}`]: makeSession(),
        [`POST /api/sessions/${SID}/runs`]: { id: 'r5', session: SID, status: 'starting' },
      });
      useRunStore.getState().resetFor(SID);
      renderWithClient(
        <WithRunControls>
          <Diagram />
        </WithRunControls>,
      );
      await laidOut();
      const play = within(nodeElement('seed')).getByRole('button', { name: 'Run to here' });
      await waitFor(() => expect(play).toBeEnabled());
      await user.click(play);
      await waitFor(() => expect(useRunStore.getState().current?.runId).toBe('r5'));
      const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
      expect(JSON.parse(String(post[1]!.body))).toEqual({ mode: 'to', targets: ['seed'], force: false, force_steps: [] });
      expect(useView.getState().selected).toBeNull();
    });
  });
});

describe('DiagramView', () => {
  beforeEach(() => {
    useView.getState().resetScope();
  });

  it('draws without a session, query client or run controls, and no play icon without runToHere', async () => {
    const store = createViewStore();
    store.setState({ textSize: 1.5 });
    const clicked: string[] = [];
    render(
      <ViewStoreContext.Provider value={store}>
        <DiagramView graph={withSubGraph} scope="with_sub" onStepClick={(step) => clicked.push(step.id)} />
      </ViewStoreContext.Provider>,
    );
    await laidOut();
    expect(nodeElement(CHAIN)).toHaveTextContent('client_chain');
    expect(screen.getByLabelText('Breadcrumb')).toHaveTextContent('client_with_sub');
    expect(document.querySelector('.rc-play')).toBeNull();
    expect(document.querySelector('.rc-run-marks')).toBeNull();
    const canvas = document.querySelector<HTMLElement>('.rc-diagram-canvas')!;
    expect(canvas.style.getPropertyValue('--rc-text-scale')).toBe('1.5');

    fireEvent.click(nodeElement('seed').querySelector('.rc-step')!);
    expect(store.getState().selected).toEqual({ kind: 'step', id: 'seed' });
    expect(useView.getState().selected).toBeNull();
    expect(clicked).toEqual(['seed']);
  });

  describe('the eye icon', () => {
    /** with_sub with `total` writing a Dataset, so it is a step the echogram pane may draw. */
    function datasetGraph() {
      const graph = structuredClone(withSubGraph);
      const total = graph.nodes.find((node) => node.id === 'total');
      if (total?.kind === 'step') total.outputs = total.outputs.map((output) => ({ ...output, type: 'Dataset' }));
      return graph;
    }

    const badge: StepBadge = {
      predicted: 'user',
      actual: null,
      progress: null,
      saved: 'U',
      willSave: false,
      checkpointed: false,
      stale: false,
      surveyBacked: false,
      onDisk: true,
      partly: null,
      note: null,
    };

    function renderView(props: Partial<DiagramViewProps>) {
      const store = createViewStore();
      render(
        <ViewStoreContext.Provider value={store}>
          <DiagramView graph={datasetGraph()} scope="with_sub" {...props} />
        </ViewStoreContext.Provider>,
      );
    }

    it('draws on a Dataset step, dimmed when nothing is saved, and shows it on click', async () => {
      const show = vi.fn();
      renderView({ echogram: { show } });
      await laidOut();
      const eye = within(nodeElement('total')).getByRole('button', { name: /^Not saved: show runs to this step/ });
      expect(eye).toHaveClass('rc-eye', 'rc-eye-off');
      expect(nodeElement('seed').querySelector('.rc-eye')).toBeNull();
      await userEvent.setup().click(eye);
      expect(show).toHaveBeenCalledWith('total');
    });

    it('draws filled on a step whose result is saved', async () => {
      renderView({ echogram: { show: vi.fn() }, badges: { total: badge } });
      await laidOut();
      const eye = within(nodeElement('total')).getByRole('button', { name: 'Show in the echogram pane' });
      expect(eye).toHaveClass('rc-eye-saved');
      expect(eye).not.toHaveClass('rc-eye-off');
    });

    it('says when a step is partly saved, or never saved', () => {
      expect(eyeLook({ ...badge, onDisk: true, partly: { count: 2, of: 3 } }, null)).toEqual({
        title: '2 of 3 instances saved: show draws them',
        className: 'rc-eye-saved rc-eye-part',
      });
      expect(eyeLook({ ...badge, onDisk: false }, 'never')).toEqual({
        title: "Never saved (checkpoint: never in the recipe), so it can't be drawn",
        className: 'rc-eye-off',
      });
      expect(eyeLook(null, null).title).toBe('Not saved: show runs to this step first and saves it');
      expect(eyeLook(badge, null)).toEqual({ title: 'Show in the echogram pane', className: 'rc-eye-saved' });
    });

    it('is absent without the echogram action, as on the docs site', async () => {
      renderView({ badges: { total: badge } });
      await laidOut();
      expect(document.querySelector('.rc-eye')).toBeNull();
    });
  });

  it('ends the recipe inputs box with a count of the inputs sub-recipes declare', async () => {
    const store = createViewStore();
    store.setState({ expandedBoxes: new Set(['inputs']) });
    render(
      <ViewStoreContext.Provider value={store}>
        <DiagramView graph={nestedInputGraph()} scope="nested" />
      </ViewStoreContext.Provider>,
    );
    await laidOut();
    const box = nodeElement('inputs');
    expect(box.querySelector('.rc-box-count')).toHaveTextContent(/^1 input$/);
    expect(box.querySelectorAll('.rc-box-name')).toHaveLength(1);
    expect(box.querySelector('.rc-box-section')).toBeNull();
    const more = box.querySelector('.rc-box-more')!;
    expect(more).toHaveTextContent(/^1 more input declared in sub-recipes$/);
    expect(more.getAttribute('title')).toMatch(/^Declared in the sub-recipe files and left to their defaults\./);
  });

  it('tags a value the run sets and a required input with no value', async () => {
    const graph = nestedInputGraph();
    const offset = graph.nodes.find((n) => n.id === 'in:offset');
    if (offset?.kind !== 'input') throw new Error('no in:offset');
    Object.assign(offset, { required: true, default: null });
    Object.assign(graph.includes.find((inc) => inc.id === LEAF)!.inputs!.at(-1)!, { required: true, default: null });
    const store = createViewStore();
    store.setState({ drill: [CHAIN, LEAF], expandedBoxes: new Set([`overrides:${LEAF}`]) });
    render(
      <ViewStoreContext.Provider value={store}>
        <DiagramView graph={graph} scope="nested" inputValues={{ n: 9, offset: null }} inputSources={{ n: 'config' }} />
      </ViewStoreContext.Provider>,
    );
    await laidOut();
    const box = nodeElement(`overrides:${LEAF}`);
    expect(box.querySelector('.rc-box-title')).toHaveTextContent(/^Inputs$/);
    expect(box.querySelector('.rc-box-count')).toHaveTextContent(/^2 overrides, 1 input$/);
    expect(box.querySelector('.rc-box-head')).toHaveAttribute('title', 'sub/leaf.yaml');
    expect([...box.querySelectorAll('.rc-box-section')].map((item) => item.textContent)).toEqual([
      'Overrides',
      'Inputs',
    ]);
    const unset = box.querySelector('.rc-box-value-unset')!;
    expect(unset).toHaveTextContent(/^unsetrequired$/);
    expect(unset.querySelector('.rc-box-tag')).toHaveTextContent('required');
    // leaf's k is chain's k, which the recipe sets to 5: an override, no tag.
    expect(box.querySelectorAll('.rc-box-tag')).toHaveLength(1);

    act(() => store.setState({ drill: [], expandedBoxes: new Set(['inputs']) }));
    await waitFor(() => expect(nodeElement('inputs').querySelector('.rc-box-tag')).toHaveTextContent('run config'));
  });

  it('keeps two diagrams on one page apart', async () => {
    const one = createViewStore();
    const two = createViewStore();
    render(
      <>
        <ViewStoreContext.Provider value={one}>
          <DiagramView graph={withSubGraph} scope="one" />
        </ViewStoreContext.Provider>
        <ViewStoreContext.Provider value={two}>
          <DiagramView graph={withSubGraph} scope="two" />
        </ViewStoreContext.Provider>
      </>,
    );
    await waitFor(() => expect(document.querySelectorAll('.react-flow__node[data-id="seed"]')).toHaveLength(2));
    act(() => one.getState().drillInto(CHAIN));
    await waitFor(() => expect(screen.getAllByLabelText('Breadcrumb')[0]).toHaveTextContent('client_chain'));
    expect(screen.getAllByLabelText('Breadcrumb')[1]).not.toHaveTextContent('client_chain');
    expect(two.getState().drill).toEqual([]);
  });
});

describe('edge and viewport helpers', () => {
  it('puts the arrow tip on the end of the line', () => {
    expect(arrowPath({ x: 0, y: 0 }, { x: 0, y: 20 })).toBe('M 0 20 L -3.5 12 L 3.5 12 Z');
    expect(arrowPath({ x: 5, y: 5 }, { x: 5, y: 5 })).toBe('');
  });

  it('pans so a moved node stays where it was on screen', () => {
    const viewport = { x: 100, y: 50, zoom: 0.5 };
    expect(keepInPlace(viewport, { x: 0, y: 0 }, { x: 0, y: -400 })).toEqual({ x: 100, y: 250, zoom: 0.5 });
  });

  it('shows unset in an input line tooltip for an input without a value', () => {
    const view = buildView(withSubGraph, {
      drill: [],
      connected: false,
      selected: null,
      inputValues: { n: null },
      expandedSteps: new Set<string>(),
      expandedBoxes: new Set<string>(),
    });
    expect(edgeTooltip(view, view.edges.find((edge) => edge.id === 'e:inputs->seed')!)).toBe('n = unset');
  });

  it('gives a per item input line the value of the input it maps over', () => {
    const view = buildView(mappedInputGraph(), {
      drill: [],
      connected: false,
      selected: null,
      expandedSteps: new Set<string>(),
      expandedBoxes: new Set<string>(),
    });
    const line = view.edges.find((edge) => edge.id === 'e:inputs->proc')!;
    expect(line.names).toEqual(['item of files']);
    expect(edgeTooltip(view, line)).toBe('item of files = ["a","b"]');
  });
});

describe('layout after expanding', () => {
  it('keeps the layout key but sees the new size, so the layout runs again', () => {
    const options = { drill: [], connected: false, selected: null, expandedBoxes: new Set<string>() };
    const before = buildView(withSubGraph, { ...options, expandedSteps: new Set<string>() });
    const after = buildView(withSubGraph, { ...options, expandedSteps: new Set(['seed']) });
    expect(layoutKey(after, 'RIGHT')).toBe(layoutKey(before, 'RIGHT'));

    const sizes = (view: typeof before) => new Map(view.nodes.map((node) => [node.id, estimateSize(node)]));
    const used = sizes(before);
    expect(resized(before, used, used)).toBe(false);
    expect(sizes(after).get('seed')!.height).toBeGreaterThan(used.get('seed')!.height);
    expect(resized(after, sizes(after), used)).toBe(true);
  });
});

describe('a sub-recipe opened on its own', () => {
  it('shows a banner that opens the parent recipe', async () => {
    const user = userEvent.setup();
    const entry = (name: string, path: string, includedBy: string[]) => ({
      name,
      description: null,
      version: '1.0',
      path,
      root: 'C:/r',
      relative: path.slice(5),
      modified: '2026-10-13T15:30:12Z',
      included_by: includedBy,
    });
    const fetchMock = mockApi({
      [`GET /api/sessions/${SID}`]: makeSession({ origin: 'C:/r/sub/chain.yaml' }),
      'GET /api/sessions': [],
      'GET /api/recipes': {
        roots: ['C:/r'],
        recipes: [
          entry('client_with_sub', 'C:/r/with_sub.yaml', []),
          entry('client_chain', 'C:/r/sub/chain.yaml', ['C:/r/with_sub.yaml']),
        ],
      },
      'POST /api/sessions': makeSession({ id: 'parent', origin: 'C:/r/with_sub.yaml' }),
    });
    useSessionStore.getState().setSid(SID);
    renderWithClient(<Diagram />);

    const banner = await screen.findByRole('note');
    expect(banner).toHaveTextContent('included by client_with_sub');
    await user.click(screen.getByRole('button', { name: 'open client_with_sub' }));
    await waitFor(() => expect(useSessionStore.getState().sid).toBe('parent'));
    const post = fetchMock.mock.calls.find(([, init]) => init?.method === 'POST')!;
    expect(JSON.parse(String(post[1]!.body))).toEqual({ path: 'C:/r/with_sub.yaml' });
  });
});
