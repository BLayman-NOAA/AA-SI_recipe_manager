/** Documentation links, from the server's docs_url (GET /api/health). */
import { useHealth } from '../api/queries';
import { FALLBACK_DOCS_URL } from '../env';

export { FALLBACK_DOCS_URL } from '../env';

/** The docs site root, always ending in "/". */
export function useDocsUrl(): string {
  return useHealth().data?.docs_url ?? FALLBACK_DOCS_URL;
}

/** The op reference index, or one op's page. */
export function opsUrl(docsUrl: string, op?: string): string {
  return op ? `${docsUrl}ops/${op}/` : `${docsUrl}ops/`;
}
