/** The docs site's data for the viewer: container attributes, data URLs, pages and deep links. */
import { findInclude, type Graph } from '../api/graph';
import { includePath } from '../diagram/locate';
import type { DocsEnv } from '../env';

/** The manifest schema this viewer reads (site_data.py MANIFEST_SCHEMA). */
export const MANIFEST_SCHEMA = 2;

/** One documented recipe of docs/data/manifest.json. */
export interface ManifestEntry {
  slug: string;
  title: string;
  path: string;
  page: string;
  /** The page's URL relative to the site root. */
  url: string;
  parent: string | null;
  /** The slug of the recipe whose graph draws this entry; its own slug for a root. */
  root: string;
  /** The entry's include id in its root graph; null for a root. */
  include: string | null;
  /** The posix folder of the root recipe, relative to the site root. */
  recipe_dir: string;
}

export interface Manifest {
  schema: number;
  recipes: ManifestEntry[];
}

/** What a `.recipe-viewer` container asks to draw. */
export interface ViewerTarget {
  /** The root recipe whose graph is drawn. */
  recipe: string;
  /** The manifest entry the container documents. */
  slug: string;
  /** The include id to start drilled into; null starts at the whole recipe. */
  drill: string | null;
}

/**
 * The target of a container.
 *
 * Raises:
 *   Error: The container lacks data-recipe or data-slug.
 */
export function parseContainer(container: HTMLElement): ViewerTarget {
  const { recipe, slug, drill } = container.dataset;
  if (!recipe || !slug) throw new Error('the container needs data-recipe and data-slug');
  return { recipe, slug, drill: drill || null };
}

const fetched = new Map<string, Promise<unknown>>();

/**
 * Fetch a URL once per page; a failure is tried again on the next call.
 *
 * Args:
 *   url: What to fetch.
 *   read: Reads the body of a successful response.
 */
export function fetchOnce<T>(url: string, read: (response: Response) => Promise<T>): Promise<T> {
  let result = fetched.get(url) as Promise<T> | undefined;
  if (!result) {
    result = fetch(url).then((response) => {
      if (!response.ok) throw new Error(`${response.status} ${url}`);
      return read(response);
    });
    result.catch(() => fetched.delete(url));
    fetched.set(url, result);
  }
  return result;
}

/** Forget everything fetched, so the next viewer fetches anew. */
export function forgetFetched(): void {
  fetched.clear();
}

/** The site root of a script at assets/viewer/viewer.js, ending in "/". */
export function siteRootFrom(scriptUrl: string): string {
  return new URL('../../', scriptUrl).href;
}

export function manifestUrl(siteRoot: string): string {
  return `${siteRoot}data/manifest.json`;
}

export function graphUrl(siteRoot: string, recipe: string): string {
  return `${siteRoot}data/recipes/${recipe}.json`;
}

/**
 * The URL of a recipe file of a graph.
 *
 * Args:
 *   siteRoot: The site root, ending in "/".
 *   recipeDir: The root recipe's folder relative to the site root.
 *   path: A step document or include source, relative to recipeDir.
 */
export function recipeFileUrl(siteRoot: string, recipeDir: string, path: string): string {
  return `${siteRoot}${recipeDir}/${path}`;
}

/**
 * The manifest entry of a slug.
 *
 * Raises:
 *   Error: The manifest has no such entry.
 */
export function entryOf(manifest: Manifest, slug: string): ManifestEntry {
  const entry = manifest.recipes.find((candidate) => candidate.slug === slug);
  if (!entry) throw new Error(`the manifest has no recipe ${slug}`);
  return entry;
}

/** The pages documenting the includes of one root recipe, by include id. */
export function pagesFor(manifest: Manifest, root: string): DocsEnv['pages'] {
  const pages: DocsEnv['pages'] = {};
  for (const entry of manifest.recipes) {
    if (entry.root === root && entry.include) pages[entry.include] = { title: entry.title, url: entry.url };
  }
  return pages;
}

/** The drill path that shows an include, outermost first; empty when the graph lacks it. */
export function initialDrill(graph: Graph, include: string | null): string[] {
  return include && findInclude(graph, include) ? includePath(graph, include) : [];
}

/** A deep link to a step of one viewer: `#view=<slug>:<stepId>`. */
export function deepLink(slug: string, stepId: string): string {
  return `#view=${encodeURIComponent(slug)}:${encodeURIComponent(stepId)}`;
}

/** The viewer slug and step a location hash points at, or null. */
export function parseDeepLink(hash: string): { slug: string; stepId: string } | null {
  const match = /^#?view=([^:]+):(.+)$/.exec(hash);
  if (!match) return null;
  try {
    return { slug: decodeURIComponent(match[1]), stepId: decodeURIComponent(match[2]) };
  } catch {
    return null;
  }
}

/**
 * Show the Material content tab holding an element, when it sits in one.
 *
 * Material keeps one tab of a set visible (and links tabs of the same label
 * across the page), so a deep linked diagram may sit in a hidden tab, where it
 * cannot be measured.
 */
export function showContentTab(element: HTMLElement): void {
  const block = element.closest('.tabbed-block');
  const set = block?.closest('.tabbed-set');
  if (!block || !set) return;
  const blocks = [...set.querySelectorAll(':scope > .tabbed-content > .tabbed-block')];
  const inputs = [...set.querySelectorAll<HTMLInputElement>(':scope > input')];
  const input = inputs[blocks.indexOf(block)];
  if (input && !input.checked) input.click();
  if (set.parentElement) showContentTab(set.parentElement);
}
