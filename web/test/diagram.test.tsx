// React Flow renders in jsdom with the flowStubs measurement stand-ins; jsdom
// has no Worker, so the diagram takes the grid layout fallback.
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import { buildView } from '../src/diagram/buildView';
import { Diagram } from '../src/diagram/Diagram';
import { estimateSize } from '../src/diagram/layout';
import { arrowPath } from '../src/diagram/edges/RouteEdge';
import { edgeTooltip, keepInPlace, layoutKey, resized } from '../src/diagram/useLayout';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import { laidOut, nodeElement, installFlowStubs } from './flowStubs';
import { declaredGraph, makeSession, mockApi, renderWithClient, withSubGraph } from './helpers';

const SID = '20261013-153012-a1b2c3';
const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';

installFlowStubs();

describe('Diagram', () => {
  beforeEach(() => {
    useSessionStore.getState().setSid(null);
    useView.getState().resetScope();
    useView.setState({ connected: false, direction: 'RIGHT' });
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
      expect(screen.getByLabelText('Legend')).toHaveTextContent('Recipe inputs');
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

    it('lists a sub-recipe box overrides from its chevron', async () => {
      const user = userEvent.setup();
      renderWithClient(<Diagram />);
      await laidOut();
      await user.click(within(nodeElement(CHAIN)).getByRole('button', { name: 'Show overrides' }));
      expect(useView.getState().expandedBoxes.has(CHAIN)).toBe(true);
      await waitFor(() => expect(nodeElement(CHAIN).querySelector('.rc-box-rows')).not.toBeNull());
      const rows = nodeElement(CHAIN).querySelector('.rc-box-rows')!;
      expect(rows).toHaveTextContent('source');
      expect(rows).toHaveTextContent('${seed.items}');
      expect(rows).toHaveTextContent('from seed');
      expect(rows).toHaveTextContent('k5');
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
      expect(nodeElement(`overrides:${CHAIN}`)).toHaveTextContent('Inputs from the parent');
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
