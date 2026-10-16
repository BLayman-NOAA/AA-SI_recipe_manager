import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  deepLink,
  entryOf,
  fetchOnce,
  forgetFetched,
  graphUrl,
  initialDrill,
  manifestUrl,
  pagesFor,
  parseContainer,
  parseDeepLink,
  recipeFileUrl,
  showContentTab,
  siteRootFrom,
  type Manifest,
  type ManifestEntry,
} from '../src/viewer/siteData';
import { withSubGraph } from './helpers';

const ROOT = 'https://example.org/docs/';

function entry(slug: string, fields: Partial<ManifestEntry> = {}): ManifestEntry {
  return {
    slug,
    title: slug,
    path: `recipes/demo/${slug}.yaml`,
    page: `${slug}.md`,
    url: `${slug}/`,
    parent: null,
    root: slug,
    include: null,
    recipe_dir: 'recipes/demo',
    ...fields,
  };
}

const MANIFEST: Manifest = {
  schema: 2,
  recipes: [
    entry('with_sub'),
    entry('chain', {
      title: 'The chain',
      url: 'levels/chain/',
      parent: 'with_sub',
      root: 'with_sub',
      include: 'inc:sub/chain.yaml',
    }),
    entry('other', { recipe_dir: 'recipes/other' }),
    entry('other-child', { parent: 'other', root: 'other', include: 'inc:x.yaml' }),
  ],
};

/** Material content tabs as pymdownx.tabbed writes them, with markup in the second tab. */
function tabbedSet(name: string, second: string): HTMLElement {
  const set = document.createElement('div');
  set.className = 'tabbed-set tabbed-alternate';
  set.innerHTML = `
    <input type="radio" name="${name}" id="${name}1" checked><input type="radio" name="${name}" id="${name}2">
    <div class="tabbed-labels"><label for="${name}1">Flow diagram</label><label for="${name}2">YAML</label></div>
    <div class="tabbed-content">
      <div class="tabbed-block"><p>first</p></div>
      <div class="tabbed-block">${second}</div>
    </div>`;
  document.body.appendChild(set);
  return set;
}

describe('site data', () => {
  afterEach(() => {
    forgetFetched();
    vi.unstubAllGlobals();
    document.body.innerHTML = '';
  });

  it('reads a container', () => {
    const div = document.createElement('div');
    div.dataset.recipe = 'hb1603-dive-analysis';
    expect(() => parseContainer(div)).toThrow('data-slug');
    div.dataset.slug = 'hb1603-dive-analysis';
    expect(parseContainer(div)).toEqual({ recipe: 'hb1603-dive-analysis', slug: 'hb1603-dive-analysis', drill: null });
    div.dataset.slug = 'hb1603-level-2';
    div.dataset.drill = 'inc:sub_recipes/processing_lvl_2_combined.yaml';
    expect(parseContainer(div)).toEqual({
      recipe: 'hb1603-dive-analysis',
      slug: 'hb1603-level-2',
      drill: 'inc:sub_recipes/processing_lvl_2_combined.yaml',
    });
  });

  it('finds the site root from the script and the data under it', () => {
    expect(siteRootFrom('https://example.org/docs/assets/viewer/viewer.js')).toBe(ROOT);
    expect(siteRootFrom('http://127.0.0.1:8000/assets/viewer/viewer.js')).toBe('http://127.0.0.1:8000/');
    expect(manifestUrl(ROOT)).toBe(`${ROOT}data/manifest.json`);
    expect(graphUrl(ROOT, 'with_sub')).toBe(`${ROOT}data/recipes/with_sub.json`);
  });

  it('joins recipe files onto the root recipe folder', () => {
    expect(recipeFileUrl(ROOT, 'recipes/HB1603', 'dive_analysis.yaml')).toBe(
      `${ROOT}recipes/HB1603/dive_analysis.yaml`,
    );
    expect(recipeFileUrl(ROOT, 'recipes/HB1603', 'sub_recipes/level_2.yaml')).toBe(
      `${ROOT}recipes/HB1603/sub_recipes/level_2.yaml`,
    );
  });

  it('finds manifest entries and names a missing one', () => {
    expect(entryOf(MANIFEST, 'other').recipe_dir).toBe('recipes/other');
    expect(() => entryOf(MANIFEST, 'missing')).toThrow('the manifest has no recipe missing');
  });

  it('maps the includes of one root to their pages', () => {
    expect(pagesFor(MANIFEST, 'with_sub')).toEqual({
      'inc:sub/chain.yaml': { title: 'The chain', url: 'levels/chain/' },
    });
    expect(Object.keys(pagesFor(MANIFEST, 'other'))).toEqual(['inc:x.yaml']);
    expect(pagesFor(MANIFEST, 'missing')).toEqual({});
  });

  it('starts drilled into a known include, through its parents', () => {
    expect(initialDrill(withSubGraph, null)).toEqual([]);
    expect(initialDrill(withSubGraph, 'inc:missing.yaml')).toEqual([]);
    expect(initialDrill(withSubGraph, 'inc:sub/chain.yaml')).toEqual(['inc:sub/chain.yaml']);
    expect(initialDrill(withSubGraph, 'inc:sub/leaf.yaml')).toEqual(['inc:sub/chain.yaml', 'inc:sub/leaf.yaml']);
  });

  it('writes and reads deep links', () => {
    expect(deepLink('hb1603-level-2', 'dive_sv')).toBe('#view=hb1603-level-2:dive_sv');
    expect(parseDeepLink('#view=hb1603-level-2:dive_sv')).toEqual({ slug: 'hb1603-level-2', stepId: 'dive_sv' });
    expect(parseDeepLink(deepLink('a', 'step:with colon'))).toEqual({ slug: 'a', stepId: 'step:with colon' });
    expect(parseDeepLink('view=a:b')).toEqual({ slug: 'a', stepId: 'b' });
    expect(parseDeepLink('')).toBeNull();
    expect(parseDeepLink('#level-2')).toBeNull();
    expect(parseDeepLink('#view=a')).toBeNull();
    expect(parseDeepLink('#view=a:%E0%A4%A')).toBeNull();
  });

  it('fetches a URL once, and again after a failure or when told to forget', async () => {
    let status = 500;
    const fetchMock = vi.fn(async () => new Response('body', { status }));
    vi.stubGlobal('fetch', fetchMock);
    const read = (response: Response) => response.text();
    await expect(fetchOnce('https://x.test/a', read)).rejects.toThrow('500 https://x.test/a');
    status = 200;
    await expect(fetchOnce('https://x.test/a', read)).resolves.toBe('body');
    await fetchOnce('https://x.test/a', read);
    expect(fetchMock).toHaveBeenCalledTimes(2);
    forgetFetched();
    await fetchOnce('https://x.test/a', read);
    expect(fetchMock).toHaveBeenCalledTimes(3);
  });

  it('shows the Material content tab an element sits in, through nested sets', () => {
    const outer = tabbedSet('outer', '<div class="inner-host"></div>');
    const host = outer.querySelector<HTMLElement>('.inner-host')!;
    const inner = tabbedSet('inner', '<span class="target"></span>');
    host.appendChild(inner);
    const target = inner.querySelector<HTMLElement>('.target')!;
    showContentTab(target);
    expect(inner.querySelector<HTMLInputElement>(':scope > input:nth-of-type(2)')!.checked).toBe(true);
    expect(outer.querySelector<HTMLInputElement>(':scope > input:nth-of-type(2)')!.checked).toBe(true);
    expect(outer.querySelector<HTMLInputElement>(':scope > input:nth-of-type(1)')!.checked).toBe(false);
  });

  it('leaves an element outside any content tab alone', () => {
    const div = document.createElement('div');
    document.body.appendChild(div);
    expect(() => showContentTab(div)).not.toThrow();
  });
});
