// The docs site viewer draws the application's diagram: for the same graph and
// view, every node's markup is the same in both hosts, the play icon aside
// (the site cannot run). Both render with the flowStubs stand-ins and the grid
// layout fallback.
import { act, cleanup, render } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { Diagram } from '../src/diagram/Diagram';
import { useSessionStore } from '../src/state/session';
import { createViewStore, useView, type ViewState } from '../src/state/view';
import { SiteViewer } from '../src/viewer/SiteViewer';
import { installFlowStubs, laidOut } from './flowStubs';
import type { Graph } from '../src/api/graph';
import { declaredGraph, makeSession, mockApi, nestedInputGraph, renderWithClient } from './helpers';
import hb1603Json from './fixtures/hb1603.graph.json';

const SID = '20261013-153012-a1b2c3';
const CHAIN = 'inc:sub/chain.yaml';
const LEAF = 'inc:sub/leaf.yaml';

installFlowStubs();

/** The rendered nodes' markup, without the play icon. */
async function nodeMarkup(): Promise<string[]> {
  await laidOut();
  return [...document.querySelectorAll('.react-flow__node')].map((node) => {
    const copy = node.cloneNode(true) as HTMLElement;
    copy.querySelectorAll('.rc-play').forEach((play) => play.remove());
    return copy.outerHTML;
  });
}

type ViewSetup = Partial<Pick<ViewState, 'drill' | 'expandedSteps' | 'expandedBoxes' | 'direction' | 'textSize'>>;

const CASES: { name: string; view: ViewSetup; graph?: () => Graph; texts?: string[] }[] = [
  { name: 'the whole recipe', view: {} },
  {
    name: 'the whole recipe with every box listed, top to bottom',
    view: { expandedBoxes: new Set(['inputs', CHAIN, 'cout:total']), direction: 'DOWN' },
  },
  {
    name: 'a drilled sub-recipe with its steps expanded, at 130 percent',
    view: { drill: [CHAIN, LEAF], expandedSteps: new Set(['leaf_add']), textSize: 1.3 },
  },
  { name: 'a sub-recipe drilled one level', view: { drill: [CHAIN], expandedBoxes: new Set([`overrides:${CHAIN}`]) } },
  {
    name: 'a sub-recipe box with a nested input below it',
    view: { expandedBoxes: new Set(['inputs', CHAIN]) },
    graph: nestedInputGraph,
    texts: ['1 more input declared in sub-recipes', 'Overrides'],
  },
  {
    name: 'a sub-recipe box with both sections',
    view: { drill: [CHAIN], expandedBoxes: new Set([LEAF, `overrides:${CHAIN}`]) },
    graph: nestedInputGraph,
    texts: ['1 step, 2 overrides, 1 input', 'Overrides', 'Inputs', 'offset2'],
  },
  {
    name: 'a level drawing its part of a per file run as a group',
    view: { drill: ['inc:sub_recipes/processing_lvl_2_combined.yaml'] },
    graph: () => structuredClone(hb1603Json as unknown as Graph),
    texts: ['Runs once per item of per_file_source'],
  },
];

describe('the site viewer and the application draw the same nodes', () => {
  beforeEach(() => {
    useView.getState().resetScope();
    useView.setState({ connected: false, direction: 'RIGHT', textSize: 1, theme: 'light' });
  });

  it.each(CASES)('for $name', async ({ view, graph: makeGraph = declaredGraph, texts = [] }) => {
    const graph = makeGraph();
    const session = makeSession({ graph });

    mockApi({ [`GET /api/sessions/${SID}`]: session });
    useSessionStore.getState().setSid(SID);
    useView.setState(view);
    renderWithClient(<Diagram />);
    const app = await nodeMarkup();
    const plays = document.querySelectorAll('.react-flow__node .rc-play').length;
    const parts = document.querySelectorAll('.react-flow__node li, .react-flow__node div');
    const shown = [...parts].map((item) => item.textContent);
    for (const text of texts) expect(shown).toContain(text);
    cleanup();
    useSessionStore.getState().setSid(null);

    const store = createViewStore();
    act(() => store.setState({ connected: false, direction: 'RIGHT', textSize: 1, theme: 'light', ...view }));
    document.body.dataset.mdColorScheme = 'default';
    render(
      <SiteViewer graph={graph} slug="with_sub" store={store} siteRoot="https://site.test/" recipeDir="" pages={{}} />,
    );
    const site = await nodeMarkup();
    delete document.body.dataset.mdColorScheme;

    expect(app.length).toBeGreaterThan(3);
    expect(plays).toBeGreaterThan(0);
    expect(document.querySelector('.rc-play')).toBeNull();
    expect(site).toEqual(app);
  });
});
