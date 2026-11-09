/** A statistics table drawn: title, legend, counts and one chart per kind of feature. */
import { useLayoutEffect, useRef, useState } from 'react';
import { plural } from '../text';
import { barValueText, GroupedBars } from './GroupedBars';
import { groupColor } from './palette';
import {
  countText,
  decimal,
  groupsOf,
  isNoise,
  maxLabel,
  percentText,
  seriesFor,
  type Group,
  type Series,
  type Table,
} from './table';

function swatchClass(group: Group): string {
  return isNoise(group) ? 'rc-stats-swatch rc-stats-swatch-noise' : 'rc-stats-swatch';
}

export function Legend({ groups, colorOf }: { groups: Group[]; colorOf: (group: Group) => string }) {
  return (
    <ul className="rc-stats-legend" aria-label="Groups">
      {groups.map((group, g) => (
        <li key={g}>
          <span className={swatchClass(group)} style={{ background: colorOf(group) }} />
          {group.label}
        </li>
      ))}
    </ul>
  );
}

/** The samples in each group and their share of the whole. */
export function CountsTable({ groups }: { groups: Group[] }) {
  return (
    <table className="rc-table rc-stats-counts">
      <thead>
        <tr>
          <th>Group</th>
          <th>Samples</th>
          <th>Share</th>
        </tr>
      </thead>
      <tbody>
        {groups.map((group, g) => (
          <tr key={g}>
            <td>{group.label}</td>
            <td className="rc-stats-number">{countText(group.n)}</td>
            <td className="rc-stats-number">{percentText(group.percentage)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

/** A chart's values as a table, for reading them without hovering. */
function ValuesTable({ series }: { series: Series }) {
  const unit = series.unit ? ` (${series.unit})` : '';
  return (
    <details className="rc-stats-values">
      <summary>Values</summary>
      <table className="rc-table">
        <thead>
          <tr>
            <th>Mean ± SD{unit}</th>
            {series.features.map((feature) => (
              <th key={feature.name}>{feature.label}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {series.groups.map((group, g) => (
            <tr key={g}>
              <td>{group.label}</td>
              {series.bars[g].map((bar, f) => (
                <td key={series.features[f].name} className="rc-stats-number" title={barValueText(bar, series.unit)}>
                  {bar ? `${decimal(bar.mean)} ± ${bar.std === null ? 'none' : decimal(bar.std)}` : 'no value'}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </details>
  );
}

/** The width of an element, followed as it resizes; 0 until measured. */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    setWidth(element.clientWidth);
    const observer = new ResizeObserver(() => setWidth(element.clientWidth));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

/** "2,995 samples in 2 clusters", from the table's source when it says. */
function summaryText(table: Table): string | null {
  const { n_total_samples: total, n_clusters: clusters } = table.source;
  if (typeof total === 'number' && typeof clusters === 'number') {
    return `${countText(total)} samples in ${plural(clusters, 'cluster')}`;
  }
  return table.description;
}

export function StatsView({ table, stepId, output }: { table: Table; stepId: string; output: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const groups = groupsOf(table);
  const highest = maxLabel(table);
  const colorOf = (group: Group) => groupColor(group.id, highest);
  const summary = summaryText(table);
  return (
    <div className="rc-stats-view" ref={ref}>
      <div className="rc-stats-title">{table.title}</div>
      <p className="rc-stats-summary">
        <span className="rc-mono">
          {stepId}.{output}
        </span>
        {summary ? `, ${summary}` : ''}
      </p>
      {groups.length === 0 ? (
        <p className="rc-empty">The table has no rows.</p>
      ) : (
        <>
          <Legend groups={groups} colorOf={colorOf} />
          <CountsTable groups={groups} />
          {seriesFor(table).map((series) => (
            <section key={series.kind} className="rc-stats-chart">
              <h3>{series.label}</h3>
              <GroupedBars series={series} colorOf={colorOf} width={width || undefined} />
              <ValuesTable series={series} />
            </section>
          ))}
        </>
      )}
    </div>
  );
}
