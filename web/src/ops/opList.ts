/** Grouping and search for the Ops pane. */
import type { OpCatalog, OpEntry } from '../api/models';

export interface OpGroup {
  category: string | null;
  ops: OpEntry[];
}

export function matchesOp(op: OpEntry, query: string): boolean {
  const text = query.trim().toLowerCase();
  if (!text) return true;
  return [op.op, op.category ?? '', op.description ?? ''].some((value) =>
    value.toLowerCase().includes(text),
  );
}

/** Ops grouped by category in catalog category order, then uncategorized ops. */
export function groupOps(catalog: OpCatalog, query: string): OpGroup[] {
  const categories: (string | null)[] = [...catalog.categories, null];
  return categories
    .map((category) => ({
      category,
      ops: catalog.ops
        .filter((op) => (op.category ?? null) === category && matchesOp(op, query))
        .sort((a, b) => a.op.localeCompare(b.op)),
    }))
    .filter((group) => group.ops.length > 0);
}
