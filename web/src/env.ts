/** Where documentation links point: the application's docs site, or the site the viewer is on. */
import { createContext, useContext } from 'react';

/** The server's default docs_url, used until /api/health answers and outside the app. */
export const FALLBACK_DOCS_URL = 'https://blayman-noaa.github.io/AA-SI_recipe_manager/';

export interface DocsEnv {
  /** The docs site root, ending in "/". */
  docsUrl: string;
  /** Where op reference links open: a new tab in the app, the same tab on the site. */
  linkTarget: '_blank' | '_self';
  /** Site pages documenting includes, by include id, as URLs relative to docsUrl. */
  pages: Record<string, { title: string; url: string }>;
}

export const DocsEnvContext = createContext<DocsEnv>({
  docsUrl: FALLBACK_DOCS_URL,
  linkTarget: '_blank',
  pages: {},
});

/** The docs environment of the enclosing provider. */
export function useDocsEnv(): DocsEnv {
  return useContext(DocsEnvContext);
}
