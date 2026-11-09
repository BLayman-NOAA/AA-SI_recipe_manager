/**
 * The aa-table/1 statistics table a step saves as json, read for the
 * statistics pane.
 *
 * The cluster statistics op writes one row per group (clusters, then noise
 * with group_id -1) and, per feature, mean, std, min and max columns tagged
 * with `feature` and `stat`.
 */

export const TABLE_SCHEMA = 'aa-table/1';

export interface TableColumn {
  name: string;
  type: string;
  unit?: string | null;
  feature?: string;
  stat?: string;
}

export interface TableFeature {
  name: string;
  label: string;
  /** `sv`, `difference`, or another kind a later op writes. */
  kind: string;
}

export type TableRow = Record<string, unknown>;

export interface Table {
  schema: typeof TABLE_SCHEMA;
  title: string;
  description: string | null;
  groupColumn: string;
  columns: TableColumn[];
  features: TableFeature[];
  rows: TableRow[];
  source: Record<string, unknown>;
}

export type ParsedTable = { ok: true; table: Table } | { ok: false; reason: string };

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** "a number", "a list", "null": what a value is, for a refusal. */
function kindOf(value: unknown): string {
  if (value === null) return 'null';
  if (Array.isArray(value)) return 'a list';
  return typeof value === 'object' ? 'an object' : `a ${typeof value}`;
}

const isString = (value: unknown): value is string => typeof value === 'string';

function isColumn(value: unknown): value is TableColumn {
  return isObject(value) && isString(value.name) && isString(value.type);
}

function isFeature(value: unknown): value is TableFeature {
  return isObject(value) && isString(value.name) && isString(value.label) && isString(value.kind);
}

/**
 * Read json data as an aa-table/1 table; unknown extra keys are ignored.
 *
 * Returns:
 *   The table, or a short reason it is not one.
 */
export function parseTable(data: unknown): ParsedTable {
  if (!isObject(data)) return { ok: false, reason: `${kindOf(data)}, not a table` };
  if (data.schema !== TABLE_SCHEMA) {
    const found = data.schema === undefined ? 'schema missing' : `schema ${JSON.stringify(data.schema)}`;
    return { ok: false, reason: `not an ${TABLE_SCHEMA} table (${found})` };
  }
  for (const key of ['columns', 'features', 'rows']) {
    if (!Array.isArray(data[key])) return { ok: false, reason: `${key} is not a list` };
  }
  const columns = data.columns as unknown[];
  const features = data.features as unknown[];
  const rows = data.rows as unknown[];
  if (!columns.every(isColumn)) return { ok: false, reason: 'a column has no name or type' };
  if (!features.every(isFeature)) return { ok: false, reason: 'a feature has no name, label or kind' };
  if (!rows.every(isObject)) return { ok: false, reason: 'a row is not an object' };
  const groupColumn = data.group_column;
  if (!isString(groupColumn) || !columns.some((column) => column.name === groupColumn)) {
    return { ok: false, reason: 'group_column names no column' };
  }
  return {
    ok: true,
    table: {
      schema: TABLE_SCHEMA,
      title: isString(data.title) ? data.title : 'Statistics',
      description: isString(data.description) ? data.description : null,
      groupColumn,
      columns,
      features,
      rows,
      source: isObject(data.source) ? data.source : {},
    },
  };
}

/** One row of the table: a cluster, or noise. */
export interface Group {
  label: string;
  /** The cluster label; -1 is noise. */
  id: number;
  n: number | null;
  percentage: number | null;
}

const numberOr = (value: unknown, fallback: number | null = null): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : fallback;

/** The groups in row order; a row without a group_id takes its index. */
export function groupsOf(table: Table): Group[] {
  return table.rows.map((row, index) => ({
    label: String(row[table.groupColumn] ?? ''),
    id: numberOr(row.group_id, index) as number,
    n: numberOr(row.n_samples),
    percentage: numberOr(row.percentage),
  }));
}

export function isNoise(group: Group): boolean {
  return group.id < 0;
}

/** The largest cluster label of the table, at least 0. */
export function maxLabel(table: Table): number {
  return Math.max(0, ...groupsOf(table).map((group) => group.id));
}

/** One bar: a group's mean of a feature and its standard deviation. */
export interface Bar {
  mean: number;
  std: number | null;
}

/** The features of one kind, drawn as one chart. */
export interface Series {
  kind: string;
  label: string;
  unit: string | null;
  features: TableFeature[];
  groups: Group[];
  /** bars[groupIndex][featureIndex]; null where the mean is missing. */
  bars: (Bar | null)[][];
}

const KIND_ORDER = ['sv', 'difference'];

const KIND_LABELS: Record<string, string> = { sv: 'Sv', difference: 'Sv difference' };

/** The kinds present, `sv` first, then `difference`, then others as they appear. */
function kindsOf(features: TableFeature[]): string[] {
  const present = [...new Set(features.map((feature) => feature.kind))];
  const known = KIND_ORDER.filter((kind) => present.includes(kind));
  return [...known, ...present.filter((kind) => !KIND_ORDER.includes(kind))];
}

/** The column holding one statistic of a feature, found by its tags. */
function statColumn(table: Table, feature: string, stat: string): TableColumn | undefined {
  return table.columns.find((column) => column.feature === feature && column.stat === stat);
}

/** One series per kind of feature, each holding every group's bars. */
export function seriesFor(table: Table): Series[] {
  const groups = groupsOf(table);
  return kindsOf(table.features).map((kind) => {
    const features = table.features.filter((feature) => feature.kind === kind);
    const columns = features.map((feature) => ({
      mean: statColumn(table, feature.name, 'mean'),
      std: statColumn(table, feature.name, 'std'),
    }));
    const unit = columns.find((pair) => pair.mean?.unit)?.mean?.unit ?? null;
    const bars = table.rows.map((row) =>
      columns.map(({ mean, std }) => {
        const value = mean ? numberOr(row[mean.name]) : null;
        if (value === null) return null;
        return { mean: value, std: std ? numberOr(row[std.name]) : null };
      }),
    );
    return { kind, label: KIND_LABELS[kind] ?? kind, unit, features, groups, bars };
  });
}

/** A number with one decimal and a true minus sign: "−71.3". */
export function decimal(value: number, digits = 1): string {
  return value.toFixed(digits).replace(/^-/, '−');
}

/** A sample count in full with separators: "1,234". */
export function countText(n: number | null): string {
  return n === null ? '' : n.toLocaleString('en-US');
}

/** "41.2 %", or nothing when unknown. */
export function percentText(percentage: number | null): string {
  return percentage === null ? '' : `${percentage.toFixed(1)} %`;
}
