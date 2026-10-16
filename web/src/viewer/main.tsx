/**
 * The docs site viewer: draws each `.recipe-viewer` container of a MkDocs page
 * with the application's diagram, fed by the site's graph JSON and manifest.
 *
 * A container is drawn the first time it comes into view (a diagram in a
 * hidden content tab cannot be measured). Its fallback step list stays in the
 * page and shows again whenever the viewer cannot draw.
 */
// CSS order: viewer.css first (see its header), then the shared CSS in the
// application's order, the diagram's (through SiteViewer) before theme.css.
import './viewer.css';
import { Component, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { findStep, GRAPH_SCHEMA, type Graph } from '../api/graph';
import { selectStep } from '../diagram/locate';
import { createViewStore, type ViewPreferences, type ViewStore } from '../state/view';
import {
  entryOf,
  fetchOnce,
  forgetFetched,
  graphUrl,
  initialDrill,
  manifestUrl,
  MANIFEST_SCHEMA,
  pagesFor,
  parseContainer,
  parseDeepLink,
  showContentTab,
  siteRootFrom,
  type Manifest,
} from './siteData';
import { SIDE_OPEN_WIDTH, SiteViewer } from './SiteViewer';
import '../theme/theme.css';
import { materialTheme } from './theme';

/** Where the viewer keeps its preferences; shared by every diagram of the site. */
export const SITE_VIEW_KEY = 'rc.site.view';

const SELECTOR = '.recipe-viewer[data-recipe]';

export interface Mounted {
  container: HTMLElement;
  slug: string;
  graph: Graph;
  store: ViewStore;
  root: Root;
}

const mounted: Mounted[] = [];

/** The preferences every diagram of a page shares. */
type Shared = Pick<ViewPreferences, 'connected' | 'direction' | 'textSize'>;

function sharedOf(state: ViewPreferences): Shared {
  return { connected: state.connected, direction: state.direction, textSize: state.textSize };
}

function sameShared(a: ViewPreferences, b: ViewPreferences): boolean {
  return a.connected === b.connected && a.direction === b.direction && a.textSize === b.textSize;
}

/** Apply a store's direction, text size and Highlight connected to every other diagram of the page. */
function syncPreferences(store: ViewStore): void {
  store.subscribe((state, previous) => {
    if (sameShared(state, previous)) return;
    for (const other of mounted) {
      if (other.store !== store && !sameShared(other.store.getState(), state)) other.store.setState(sharedOf(state));
    }
  });
}

/** Mark a container failed with a notice; its step list shows again. */
function fail(container: HTMLElement, message: string): void {
  container.dataset.rvState = 'failed';
  const notice = document.createElement('p');
  notice.className = 'rc-viewer-notice';
  notice.textContent = `The diagram could not be drawn (${message}); the steps are listed below.`;
  container.prepend(notice);
}

interface ViewerBoundaryProps {
  onError: (message: string) => void;
  children: ReactNode;
}

/** Draws nothing, and fails the container, when the viewer throws while rendering. */
class ViewerBoundary extends Component<ViewerBoundaryProps, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: unknown) {
    this.props.onError(error instanceof Error ? error.message : String(error));
  }

  render() {
    return this.state.failed ? null : this.props.children;
  }
}

/** Fail a mounted viewer that threw while drawing: hide it and stop following links into it. */
function drop(viewer: Mounted, message: string): void {
  const index = mounted.indexOf(viewer);
  if (index >= 0) mounted.splice(index, 1);
  viewer.container.querySelector<HTMLElement>(':scope > .rc-viewer-root')?.setAttribute('hidden', '');
  fail(viewer.container, message);
}

/**
 * Select the step a deep link names, when it is in this viewer's graph,
 * showing the content tab the viewer sits in.
 */
function applyDeepLink(viewer: Mounted, hash: string): boolean {
  const link = parseDeepLink(hash);
  if (!link || link.slug !== viewer.slug || !findStep(viewer.graph, link.stepId)) return false;
  showContentTab(viewer.container);
  selectStep(viewer.store, viewer.graph, link.stepId);
  return true;
}

/**
 * Draw one container.
 *
 * Args:
 *   container: A `.recipe-viewer` element with data-recipe, data-slug and data-drill.
 *   siteRoot: The site root, ending in "/".
 *
 * Returns:
 *   The mounted viewer, or null when the container was already handled or failed.
 */
export async function mountViewer(container: HTMLElement, siteRoot: string): Promise<Mounted | null> {
  if (container.dataset.rvState) return null;
  container.dataset.rvState = 'loading';
  const element = document.createElement('div');
  element.className = 'rc-viewer-root';
  container.prepend(element);
  try {
    const target = parseContainer(container);
    const [manifest, graph] = await Promise.all([
      fetchOnce<Manifest>(manifestUrl(siteRoot), (response) => response.json()),
      fetchOnce<Graph>(graphUrl(siteRoot, target.recipe), (response) => response.json()),
    ]);
    if (manifest.schema !== MANIFEST_SCHEMA) {
      throw new Error(`manifest schema ${manifest.schema}; this viewer reads schema ${MANIFEST_SCHEMA}`);
    }
    if (graph.schema !== GRAPH_SCHEMA) {
      throw new Error(`graph schema ${graph.schema}; this viewer reads schema ${GRAPH_SCHEMA}`);
    }
    const recipeDir = entryOf(manifest, target.recipe).recipe_dir;
    const store = createViewStore({ storageKey: SITE_VIEW_KEY });
    store.setState({ theme: materialTheme(), drill: initialDrill(graph, target.drill) });
    const root = createRoot(element);
    const viewer: Mounted = { container, slug: target.slug, graph, store, root };
    mounted.push(viewer);
    syncPreferences(store);
    const linked = applyDeepLink(viewer, window.location.hash);
    container.dataset.rvState = 'ready';
    root.render(
      <ViewerBoundary onError={(message) => drop(viewer, message)}>
        <SiteViewer
          graph={graph}
          slug={target.slug}
          store={store}
          siteRoot={siteRoot}
          recipeDir={recipeDir}
          pages={pagesFor(manifest, target.recipe)}
          sideOpenAtStart={linked || container.clientWidth >= SIDE_OPEN_WIDTH}
        />
      </ViewerBoundary>,
    );
    if (linked) window.requestAnimationFrame(() => container.scrollIntoView?.({ block: 'center' }));
    return viewer;
  } catch (error) {
    element.remove();
    fail(container, error instanceof Error ? error.message : String(error));
    return null;
  }
}

/** Unmount every viewer and forget fetched data (for tests). */
export function unmountViewers(): void {
  for (const viewer of mounted.splice(0)) viewer.root.unmount();
  forgetFetched();
}

/** Draw the page's containers as each comes into view; a deep linked one at once. */
export function initViewers(siteRoot: string): void {
  const containers = [...document.querySelectorAll<HTMLElement>(SELECTOR)];
  const link = parseDeepLink(window.location.hash);
  const linked = link && containers.find((container) => container.dataset.slug === link.slug);
  if (linked) {
    // Shown before drawing even when the step is gone, so the diagram can be measured.
    showContentTab(linked);
    void mountViewer(linked, siteRoot);
  }
  if (!('IntersectionObserver' in window)) {
    containers.forEach((container) => void mountViewer(container, siteRoot));
    return;
  }
  const observer = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        observer.unobserve(entry.target);
        void mountViewer(entry.target as HTMLElement, siteRoot);
      }
    },
    { rootMargin: '200px' },
  );
  for (const container of containers) {
    if (container !== linked) observer.observe(container);
  }
}

/** Follow a deep link set after load, such as a "Link to this step" click. */
export function followDeepLink(siteRoot: string): void {
  const link = parseDeepLink(window.location.hash);
  if (!link) return;
  const viewer = mounted.find((item) => item.slug === link.slug);
  if (viewer) {
    if (applyDeepLink(viewer, window.location.hash)) viewer.container.scrollIntoView?.({ block: 'nearest' });
    return;
  }
  const container = [...document.querySelectorAll<HTMLElement>(SELECTOR)].find(
    (candidate) => candidate.dataset.slug === link.slug,
  );
  if (container) void mountViewer(container, siteRoot);
}

declare global {
  interface Window {
    /** Material's page observable, present when its bundle has loaded. */
    document$?: { subscribe: (next: () => void) => unknown };
  }
}

function start(): void {
  const siteRoot = siteRootFrom(import.meta.url);
  window.addEventListener('hashchange', () => followDeepLink(siteRoot));
  if (window.document$?.subscribe) window.document$.subscribe(() => initViewers(siteRoot));
  else if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => initViewers(siteRoot));
  } else initViewers(siteRoot);
}

start();
