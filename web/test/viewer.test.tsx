/// <reference types="node" />
// The docs site viewer mounted into page containers as main.tsx does on a
// MkDocs page, with fetch answering the site's data and recipe files. React
// Flow renders with the flowStubs stand-ins and the grid layout fallback.
import { readFileSync } from 'node:fs';
import { act, fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Graph } from '../src/api/graph';
import { followDeepLink, initViewers, mountViewer, unmountViewers, type Mounted } from '../src/viewer/main';
import type { Manifest } from '../src/viewer/siteData';
import { stepLine } from '../src/yaml/findLine';
import { installFlowStubs } from './flowStubs';
import { withSubGraph } from './helpers';

installFlowStubs();

const SITE = 'https://site.test/docs/';
const CHAIN = 'inc:sub/chain.yaml';
const RECIPES = '../tests/fixtures/client_recipes';
const FILES = {
  'with_sub.yaml': readFileSync(`${RECIPES}/with_sub.yaml`, 'utf8'),
  'sub/chain.yaml': readFileSync(`${RECIPES}/sub/chain.yaml`, 'utf8'),
  'sub/leaf.yaml': readFileSync(`${RECIPES}/sub/leaf.yaml`, 'utf8'),
};

const MANIFEST: Manifest = {
  schema: 2,
  recipes: [
    {
      slug: 'with_sub',
      title: 'With sub',
      path: 'recipes/client/with_sub.yaml',
      page: 'with_sub.md',
      url: 'with_sub/',
      parent: null,
      root: 'with_sub',
      include: null,
      recipe_dir: 'recipes/client',
    },
    {
      slug: 'with-sub-chain',
      title: 'The chain level',
      path: 'recipes/client/sub/chain.yaml',
      page: 'levels/chain.md',
      url: 'levels/chain/',
      parent: 'with_sub',
      root: 'with_sub',
      include: CHAIN,
      recipe_dir: 'recipes/client',
    },
  ],
};

/** The fixture graph as the site writes it: the seed step has an op reference page. */
function siteGraph(): Graph {
  const graph = structuredClone(withSubGraph);
  const seed = graph.nodes.find((node) => node.id === 'seed');
  if (seed?.kind === 'step') seed.op_url = 'ops/custom/';
  return graph;
}

let graph: Graph;
let fetchMock: ReturnType<typeof vi.fn>;

function stubSite() {
  fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = typeof input === 'string' ? input : input.toString();
    if (url === `${SITE}data/manifest.json`) return new Response(JSON.stringify(MANIFEST));
    if (url === `${SITE}data/recipes/with_sub.json`) return new Response(JSON.stringify(graph));
    for (const [path, text] of Object.entries(FILES)) {
      if (url === `${SITE}recipes/client/${path}`) return new Response(text);
    }
    return new Response('not found', { status: 404 });
  });
  vi.stubGlobal('fetch', fetchMock);
}

/** A container as a site partial writes it, with its fallback step list. */
function addContainer(slug: string, drill?: string): HTMLElement {
  const container = document.createElement('div');
  container.className = 'recipe-viewer';
  container.dataset.recipe = 'with_sub';
  container.dataset.slug = slug;
  if (drill) container.dataset.drill = drill;
  container.innerHTML = '<ul class="fallback"><li>seed</li><li>total</li></ul>';
  document.body.appendChild(container);
  return container;
}

/** Put a container in the second of two Material content tabs, the first one shown. */
function inSecondTab(container: HTMLElement): HTMLInputElement[] {
  const set = document.createElement('div');
  set.className = 'tabbed-set tabbed-alternate';
  set.innerHTML = `
    <input type="radio" name="tabs" id="tab1" checked><input type="radio" name="tabs" id="tab2">
    <div class="tabbed-labels"><label for="tab1">YAML</label><label for="tab2">Flow diagram</label></div>
    <div class="tabbed-content"><div class="tabbed-block"><p>yaml</p></div><div class="tabbed-block"></div></div>`;
  document.body.appendChild(set);
  set.querySelectorAll('.tabbed-block')[1].appendChild(container);
  return [...set.querySelectorAll('input')];
}

async function mount(container: HTMLElement): Promise<Mounted> {
  let viewer: Mounted | null = null;
  await act(async () => {
    viewer = await mountViewer(container, SITE);
  });
  if (!viewer) throw new Error('the viewer did not mount');
  return viewer;
}

/** Wait until a container's diagram has placed every node. */
async function laidOutIn(container: HTMLElement) {
  await waitFor(() => {
    expect(container.querySelector('.react-flow__node')).not.toBeNull();
    expect(container.querySelector('.react-flow__node.rc-unplaced')).toBeNull();
  });
}

function nodeIn(container: HTMLElement, id: string): HTMLElement {
  const element = container.querySelector<HTMLElement>(`.react-flow__node[data-id="${id}"]`);
  if (!element) throw new Error(`no rendered node ${id}`);
  return element;
}

function toolbarButton(container: HTMLElement, name: string): HTMLElement {
  return within(container.querySelector<HTMLElement>('.rc-viewer-toolbar')!).getByRole('button', { name });
}

function openSide(container: HTMLElement, tab: 'Inspector') {
  fireEvent.click(toolbarButton(container, tab));
}

/** Switch the main area to the YAML and return it. */
function showYaml(container: HTMLElement): HTMLElement {
  fireEvent.click(toolbarButton(container, 'YAML'));
  const element = container.querySelector<HTMLElement>('.rc-viewer-yaml');
  if (!element) throw new Error('the YAML is not shown');
  return element;
}

function side(container: HTMLElement): HTMLElement {
  const element = container.querySelector<HTMLElement>('.rc-viewer-side');
  if (!element) throw new Error('the side panel is closed');
  return element;
}

describe('the docs site viewer', () => {
  beforeEach(() => {
    graph = siteGraph();
    stubSite();
  });

  afterEach(() => {
    act(() => unmountViewers());
    document.body.innerHTML = '';
    delete document.body.dataset.mdColorScheme;
    window.history.replaceState(null, '', window.location.pathname);
    vi.unstubAllGlobals();
  });

  it('draws the whole recipe read only and keeps the fallback list', async () => {
    const container = addContainer('with_sub');
    await mount(container);
    await laidOutIn(container);
    expect(container.dataset.rvState).toBe('ready');
    expect(container.querySelector('ul.fallback')).not.toBeNull();
    expect(within(container).getByLabelText('Breadcrumb')).toHaveTextContent('client_with_sub');
    expect(within(container).queryByRole('button', { name: 'Up one level' })).toBeNull();
    expect(within(container).getByLabelText('Legend')).toHaveTextContent('Inputs');
    expect(nodeIn(container, 'inputs')).toHaveTextContent('Recipe inputs');
    expect(nodeIn(container, CHAIN)).toHaveTextContent('client_chain');
    expect(container.querySelector('.rc-play')).toBeNull();
    expect(container.querySelector('.rc-run-marks')).toBeNull();
    expect(container.querySelector('.rc-run-hint')).toBeNull();
    expect(within(container).queryByRole('button', { name: 'Dry run' })).toBeNull();
    expect(container.querySelector('.rc-viewer')).toHaveAttribute('data-theme', 'light');
  });

  it('starts drilled into the include a level page documents', async () => {
    const container = addContainer('with-sub-chain', CHAIN);
    const viewer = await mount(container);
    await laidOutIn(container);
    expect(viewer.store.getState().drill).toEqual([CHAIN]);
    const crumbs = within(within(container).getByLabelText('Breadcrumb'));
    expect(crumbs.getByRole('button', { name: 'client_with_sub' })).toBeInTheDocument();
    expect(crumbs.getByText('client_chain')).toHaveClass('rc-breadcrumb-current');
    expect(within(container).getByRole('button', { name: 'Up one level' })).toBeInTheDocument();
    expect(container.querySelector('.react-flow__node[data-id="chain_inc"]')).not.toBeNull();
    const box = container.querySelector(`.react-flow__node[data-id="overrides:${CHAIN}"]`)!;
    expect(box.querySelector('.rc-box-title')).toHaveTextContent(/^Inputs$/);
  });

  it('keeps the selection of each diagram apart and shares the text size', async () => {
    const first = addContainer('with_sub');
    const second = addContainer('with-sub-chain', CHAIN);
    await mount(first);
    await mount(second);
    await laidOutIn(first);
    await laidOutIn(second);
    openSide(first, 'Inspector');
    openSide(second, 'Inspector');
    fireEvent.click(nodeIn(first, 'seed'));
    await waitFor(() => expect(side(first).querySelector('.rc-inspector-title')).toHaveTextContent('seed'));
    expect(side(second)).toHaveTextContent('Select a step, sub-recipe, inputs or outputs box');

    fireEvent.change(within(first).getByLabelText(/Text size/), { target: { value: '1.3' } });
    for (const container of [first, second]) {
      await waitFor(() =>
        expect(container.querySelector<HTMLElement>('.rc-diagram-canvas')!.style.getPropertyValue('--rc-text-scale')).toBe(
          '1.3',
        ),
      );
    }
    expect(JSON.parse(window.localStorage.getItem('rc.site.view')!).textSize).toBe(1.3);
  });

  it('links op references in the same tab and includes to their pages', async () => {
    const container = addContainer('with_sub');
    await mount(container);
    await laidOutIn(container);
    openSide(container, 'Inspector');
    fireEvent.click(nodeIn(container, 'seed'));
    const op = await within(side(container)).findByRole('link', { name: /Op reference for custom/ });
    expect(op).toHaveAttribute('href', `${SITE}ops/custom/`);
    expect(op).not.toHaveAttribute('target');
    expect(within(side(container)).getByRole('link', { name: 'Link to this step' })).toHaveAttribute(
      'href',
      '#view=with_sub:seed',
    );

    fireEvent.click(nodeIn(container, CHAIN));
    const page = await within(side(container)).findByRole('link', { name: 'The chain level' });
    expect(page).toHaveAttribute('href', `${SITE}levels/chain/`);
    expect(page.closest('tr')).toHaveTextContent('documented at');
    expect(within(side(container)).queryByRole('link', { name: 'Link to this step' })).toBeNull();
  });

  it('shows the recipe files from the site and follows the selection', async () => {
    const container = addContainer('with_sub');
    const viewer = await mount(container);
    await laidOutIn(container);
    const yaml = showYaml(container);
    const tabs = within(yaml.querySelector<HTMLElement>('[aria-label="Recipe files"]')!);
    expect(tabs.getAllByRole('tab').map((tab) => tab.textContent)).toEqual([
      'with_sub.yaml',
      'sub/leaf.yaml',
      'sub/chain.yaml',
    ]);
    await waitFor(() => expect(yaml.querySelector('.rc-yaml')).not.toBeNull());
    expect(fetchMock).toHaveBeenCalledWith(`${SITE}recipes/client/with_sub.yaml`);

    act(() => viewer.store.getState().select({ kind: 'step', id: 'seed' }));
    const seedLine = stepLine(FILES['with_sub.yaml'], 'seed')!;
    await waitFor(() =>
      expect(yaml.querySelector('.rc-yaml-highlight')).toHaveAttribute('data-line', String(seedLine)),
    );

    act(() => viewer.store.getState().select({ kind: 'step', id: 'chain_inc' }));
    await waitFor(() => expect(tabs.getByRole('tab', { name: 'sub/chain.yaml' })).toHaveAttribute('aria-selected', 'true'));
    expect(fetchMock).toHaveBeenCalledWith(`${SITE}recipes/client/sub/chain.yaml`);
    const chainLine = stepLine(FILES['sub/chain.yaml'], 'chain_inc')!;
    await waitFor(() =>
      expect(yaml.querySelector('.rc-yaml-highlight')).toHaveAttribute('data-line', String(chainLine)),
    );
  });

  it('shows the drilled sub-recipe file until a node is selected', async () => {
    const container = addContainer('with-sub-chain', CHAIN);
    const viewer = await mount(container);
    await laidOutIn(container);
    const yaml = showYaml(container);
    const tabs = within(yaml.querySelector<HTMLElement>('[aria-label="Recipe files"]')!);
    expect(tabs.getByRole('tab', { name: 'sub/chain.yaml' })).toHaveAttribute('aria-selected', 'true');
    await waitFor(() => expect(yaml.querySelector('.rc-yaml')).not.toBeNull());
    expect(fetchMock).toHaveBeenCalledWith(`${SITE}recipes/client/sub/chain.yaml`);
    expect(fetchMock).not.toHaveBeenCalledWith(`${SITE}recipes/client/with_sub.yaml`);

    act(() => viewer.store.getState().drillOut());
    await waitFor(() => expect(tabs.getByRole('tab', { name: 'with_sub.yaml' })).toHaveAttribute('aria-selected', 'true'));
  });

  it('follows the Material palette', async () => {
    const container = addContainer('with_sub');
    const viewer = await mount(container);
    const root = container.querySelector('.rc-viewer')!;
    expect(root).toHaveAttribute('data-theme', 'light');
    act(() => {
      document.body.dataset.mdColorScheme = 'slate';
    });
    await waitFor(() => expect(root).toHaveAttribute('data-theme', 'dark'));
    expect(viewer.store.getState().theme).toBe('dark');
    expect(container.querySelector('.react-flow')).toHaveClass('dark');
    act(() => {
      document.body.dataset.mdColorScheme = 'default';
    });
    await waitFor(() => expect(root).toHaveAttribute('data-theme', 'light'));
  });

  it('selects the step a deep link names, drilling to it', async () => {
    window.history.replaceState(null, '', '#view=with_sub:leaf_add');
    const container = addContainer('with_sub');
    const viewer = await mount(container);
    expect(viewer.store.getState().selected).toEqual({ kind: 'step', id: 'leaf_add' });
    expect(viewer.store.getState().drill).toEqual([CHAIN, 'inc:sub/leaf.yaml']);
    await waitFor(() => expect(side(container).querySelector('.rc-inspector-title')).toHaveTextContent('leaf_add'));

    act(() => {
      window.location.hash = '#view=with_sub:total';
    });
    await waitFor(() => expect(viewer.store.getState().selected).toEqual({ kind: 'step', id: 'total' }));
    expect(viewer.store.getState().drill).toEqual([]);
  });

  it('keys F, / and Escape inside the viewer only', async () => {
    const container = addContainer('with-sub-chain', CHAIN);
    const viewer = await mount(container);
    await laidOutIn(container);
    const root = container.querySelector<HTMLElement>('.rc-viewer')!;
    const onPage = vi.fn();
    document.addEventListener('keydown', onPage);
    fireEvent.keyDown(root, { key: 'f' });
    expect(viewer.store.getState().fitRequest).toBe(1);
    fireEvent.keyDown(root, { key: '/' });
    await waitFor(() => expect(within(container).getByLabelText('Find step')).toHaveFocus());
    expect(onPage).not.toHaveBeenCalled();
    act(() => viewer.store.getState().select({ kind: 'step', id: 'chain_inc' }));
    fireEvent.keyDown(root, { key: 'Escape' });
    expect(viewer.store.getState().selected).toBeNull();
    fireEvent.keyDown(root, { key: 'Escape' });
    expect(viewer.store.getState().drill).toEqual([]);
    fireEvent.keyDown(root, { key: 'Escape' });
    expect(onPage).toHaveBeenCalledTimes(1);
    document.removeEventListener('keydown', onPage);
  });

  it('enters and leaves full screen', async () => {
    const container = addContainer('with_sub');
    await mount(container);
    await laidOutIn(container);
    const root = container.querySelector<HTMLElement>('.rc-viewer')!;
    fireEvent.click(within(root).getByRole('button', { name: 'Full screen' }));
    expect(root).toHaveClass('rc-viewer-fullscreen');
    expect(document.documentElement).toHaveClass('rc-viewer-no-scroll');
    fireEvent.keyDown(root, { key: 'Escape' });
    expect(root).not.toHaveClass('rc-viewer-fullscreen');
    expect(document.documentElement).not.toHaveClass('rc-viewer-no-scroll');
  });

  it('opens the inspector in full screen and closes it again on leaving', async () => {
    const container = addContainer('with_sub');
    await mount(container);
    await laidOutIn(container);
    const root = container.querySelector<HTMLElement>('.rc-viewer')!;
    expect(container.querySelector('.rc-viewer-side')).toBeNull();
    fireEvent.click(within(root).getByRole('button', { name: 'Full screen' }));
    // Material styles aside elements with its own font and colour.
    expect(side(container).tagName).toBe('DIV');
    expect(side(container)).toHaveTextContent('Inspector');
    fireEvent.click(within(root).getByRole('button', { name: 'Exit full screen' }));
    expect(container.querySelector('.rc-viewer-side')).toBeNull();

    openSide(container, 'Inspector');
    fireEvent.click(within(root).getByRole('button', { name: 'Full screen' }));
    fireEvent.keyDown(root, { key: 'Escape' });
    expect(within(root).getByRole('button', { name: 'Full screen' })).toBeInTheDocument();
    expect(container.querySelector('.rc-viewer-side')).not.toBeNull();
  });

  it('switches the main area between the diagram and the YAML, keeping the diagram', async () => {
    const container = addContainer('with_sub');
    const viewer = await mount(container);
    await laidOutIn(container);
    const before = nodeIn(container, 'seed').style.transform;
    expect(toolbarButton(container, 'Diagram')).toHaveAttribute('aria-pressed', 'true');

    const yaml = showYaml(container);
    expect(toolbarButton(container, 'YAML')).toHaveAttribute('aria-pressed', 'true');
    expect(toolbarButton(container, 'Fit')).toBeDisabled();
    expect(nodeIn(container, 'seed').closest('.rc-viewer-layer')).toHaveAttribute('inert');
    await waitFor(() => expect(yaml.querySelector('.rc-yaml')).not.toBeNull());

    openSide(container, 'Inspector');
    act(() => viewer.store.getState().select({ kind: 'step', id: 'seed' }));
    await waitFor(() => expect(side(container).querySelector('.rc-inspector-title')).toHaveTextContent('seed'));

    fireEvent.click(toolbarButton(container, 'Diagram'));
    expect(container.querySelector('.rc-viewer-yaml')).toBeNull();
    expect(toolbarButton(container, 'Fit')).not.toBeDisabled();
    expect(nodeIn(container, 'seed').style.transform).toBe(before);
  });

  it('leaves the list in place when the graph schema is not its own', async () => {
    graph = { ...siteGraph(), schema: 3 };
    const container = addContainer('with_sub');
    let result: Mounted | null = null;
    await act(async () => {
      result = await mountViewer(container, SITE);
    });
    expect(result).toBeNull();
    expect(container.dataset.rvState).toBe('failed');
    expect(container.querySelector('.rc-viewer-notice')).toHaveTextContent('graph schema 3');
    expect(container.querySelector('.rc-viewer-root')).toBeNull();
    expect(container.querySelector('ul.fallback')).not.toBeNull();
  });

  it('fails the container, list showing, when the viewer throws while drawing', async () => {
    const error = vi.spyOn(console, 'error').mockImplementation(() => {});
    graph = { ...siteGraph(), includes: null } as unknown as Graph;
    const container = addContainer('with_sub');
    let viewer: Mounted | null = null;
    await act(async () => {
      viewer = await mountViewer(container, SITE);
    });
    await waitFor(() => expect(container.dataset.rvState).toBe('failed'));
    expect(container.querySelector('.rc-viewer-notice')).toHaveTextContent('could not be drawn');
    // The boundary caught it: the root mounted, then drew nothing.
    expect(container.querySelector('.rc-viewer-root')).not.toBeVisible();
    expect(container.querySelector('.rc-viewer')).toBeNull();
    expect(container.querySelector('ul.fallback')).not.toBeNull();
    // A later deep link no longer reaches the failed viewer.
    window.history.replaceState(null, '', '#view=with_sub:total');
    act(() => followDeepLink(SITE));
    expect(viewer!.store.getState().selected).toBeNull();
    error.mockRestore();
  });

  it('shows the content tab of a deep linked diagram before drawing it, then scrolls to it', async () => {
    window.history.replaceState(null, '', '#view=with_sub:total');
    const container = addContainer('with_sub');
    const [yamlTab, flowTab] = inSecondTab(container);
    const scroll = vi.fn();
    container.scrollIntoView = scroll;
    let drawnAtClick: boolean | null = null;
    flowTab.addEventListener('click', () => {
      drawnAtClick = container.querySelector('.rc-viewer') !== null;
    });
    const viewer = await mount(container);
    expect(flowTab.checked).toBe(true);
    expect(yamlTab.checked).toBe(false);
    expect(drawnAtClick).toBe(false);
    expect(viewer.store.getState().selected).toEqual({ kind: 'step', id: 'total' });
    await waitFor(() => expect(scroll).toHaveBeenCalledWith({ block: 'center' }));
  });

  it('shows the content tab of a deep linked diagram whose step is gone', async () => {
    window.history.replaceState(null, '', '#view=with_sub:gone');
    const container = addContainer('with_sub');
    const [yamlTab, flowTab] = inSecondTab(container);
    await act(async () => {
      initViewers(SITE);
    });
    await waitFor(() => expect(container.dataset.rvState).toBe('ready'));
    expect(flowTab.checked).toBe(true);
    expect(yamlTab.checked).toBe(false);
  });

  it('draws a container once', async () => {
    const container = addContainer('with_sub');
    await mount(container);
    let again: Mounted | null = null;
    await act(async () => {
      again = await mountViewer(container, SITE);
    });
    expect(again).toBeNull();
    expect(container.querySelectorAll('.rc-viewer')).toHaveLength(1);
  });
});
