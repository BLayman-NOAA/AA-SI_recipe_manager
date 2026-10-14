/** Documentation links, from the server's docs_url (GET /api/health). */
import { useHealth } from '../api/queries';

/** Used only until /api/health has answered: the server's default docs_url. */
export const FALLBACK_DOCS_URL = 'https://blayman-noaa.github.io/AA-SI_recipe_manager/';

/** The docs site root, always ending in "/". */
export function useDocsUrl(): string {
  return useHealth().data?.docs_url ?? FALLBACK_DOCS_URL;
}

/** The op reference index, or one op's page. */
export function opsUrl(docsUrl: string, op?: string): string {
  return op ? `${docsUrl}ops/${op}/` : `${docsUrl}ops/`;
}
