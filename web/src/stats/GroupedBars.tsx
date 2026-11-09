/**
 * Grouped bars of mean and standard deviation, one group of bars per feature
 * and one bar per cluster, in plain SVG. The form follows the ML package's
 * cluster statistics figure.
 */
import { linearScale, niceTicks, stepDecimals, yAxis } from './scale';
import {
  countText,
  decimal,
  isNoise,
  percentText,
  type Bar,
  type Group,
  type Series,
  type TableFeature,
} from './table';

const MARGIN = { top: 8, right: 8, bottom: 30, left: 62 };
/** Surface gap between neighbouring bars. */
const GAP = 2;
const MAX_BAR = 24;
const MIN_BAR = 4;
/** Air on either side of a feature's bars. */
const BAND_PAD = 16;
const RADIUS = 4;
const CAP = 3;
/** Width of a character of 11px axis text, for sizing bands to their labels. */
const CHAR_WIDTH = 6.5;

export interface BarLayout {
  bar: number;
  band: number;
  width: number;
}

/**
 * Bar and band widths that fit the features into `width`, bars at most 24px.
 *
 * Bands never get narrower than their bars or their labels need, so a chart
 * too wide for the pane is drawn at its own width and scrolls.
 */
export function barLayout(groups: number, features: TableFeature[], width: number): BarLayout {
  const count = Math.max(1, features.length);
  const available = Math.max(0, width - MARGIN.left - MARGIN.right);
  const fit = Math.floor((available / count - BAND_PAD) / Math.max(1, groups)) - GAP;
  const bar = Math.min(MAX_BAR, Math.max(MIN_BAR, fit));
  const label = Math.max(0, ...features.map((feature) => feature.label.length)) * CHAR_WIDTH + 8;
  const band = Math.max(available / count, groups * (bar + GAP) - GAP + BAND_PAD, label);
  return { bar, band, width: MARGIN.left + count * band + MARGIN.right };
}

const px = (value: number) => Number(value.toFixed(2));

/**
 * A bar from `base` to `end`, with 4px rounded corners at the data end and
 * square at the baseline.
 */
export function barPath(x: number, width: number, base: number, end: number): string {
  const r = Math.min(RADIUS, width / 2, Math.abs(base - end));
  const s = end < base ? 1 : -1;
  const sweep = end < base ? 1 : 0;
  return (
    `M${px(x)},${px(base)}V${px(end + s * r)}` +
    `A${px(r)},${px(r)} 0 0 ${sweep} ${px(x + r)},${px(end)}` +
    `H${px(x + width - r)}` +
    `A${px(r)},${px(r)} 0 0 ${sweep} ${px(x + width)},${px(end + s * r)}` +
    `V${px(base)}Z`
  );
}

/** "mean −71.3 dB, SD 4.1 dB", or "no value". */
export function barValueText(bar: Bar | null, unit: string | null): string {
  if (!bar) return 'no value';
  const u = unit ? ` ${unit}` : '';
  const sd = bar.std === null ? 'no SD' : `SD ${decimal(bar.std)}${u}`;
  return `mean ${decimal(bar.mean)}${u}, ${sd}`;
}

/** "n 1,234 (41.2 %)", or what of it is known. */
export function groupCountText(group: Group): string {
  const n = group.n === null ? '' : `n ${countText(group.n)}`;
  const share = group.percentage === null ? '' : `(${percentText(group.percentage)})`;
  return [n, share].filter(Boolean).join(' ');
}

/** "Cluster 0, 18 kHz: mean −71.3 dB, SD 4.1 dB, n 1,234 (41.2 %)". */
export function barTitle(group: Group, feature: TableFeature, bar: Bar | null, unit: string | null): string {
  const counts = bar ? groupCountText(group) : '';
  return `${group.label}, ${feature.label}: ${barValueText(bar, unit)}${counts ? `, ${counts}` : ''}`;
}

export interface GroupedBarsProps {
  series: Series;
  colorOf: (group: Group) => string;
  /** Width the chart may use, in pixels. */
  width?: number;
  height?: number;
}

export function GroupedBars({ series, colorOf, width = 520, height = 220 }: GroupedBarsProps) {
  const { groups, features, bars, unit } = series;
  if (groups.length === 0) return null;
  const layout = barLayout(groups.length, features, width);
  const plotTop = MARGIN.top;
  const plotBottom = height - MARGIN.bottom;
  const plotLeft = MARGIN.left;
  const plotRight = layout.width - MARGIN.right;
  const { domain, baseline } = yAxis(series);
  const y = linearScale(domain, [plotBottom, plotTop]);
  const ticks = niceTicks(domain[0], domain[1], 4);
  const decimals = ticks.length > 1 ? stepDecimals(ticks[1] - ticks[0]) : 1;
  const inner = groups.length * (layout.bar + GAP) - GAP;
  const barX = (f: number, g: number) =>
    plotLeft + f * layout.band + (layout.band - inner) / 2 + g * (layout.bar + GAP);
  const yLabel = unit ? `${series.label} (${unit})` : series.label;

  return (
    <div className="rc-stats-bars">
      <svg
        className="rc-stats-svg"
        width={layout.width}
        height={height}
        viewBox={`0 0 ${px(layout.width)} ${height}`}
        role="group"
        aria-label={`${series.label} by feature, one bar per group`}
      >
        {ticks.map((tick) => (
          <g key={tick}>
            {tick !== baseline && (
              <line className="rc-stats-grid" x1={plotLeft} x2={plotRight} y1={px(y(tick))} y2={px(y(tick))} />
            )}
            <text className="rc-stats-tick" x={plotLeft - 6} y={px(y(tick))} textAnchor="end" dominantBaseline="middle">
              {decimal(tick, decimals)}
            </text>
          </g>
        ))}
        <text
          className="rc-stats-axis-label"
          transform={`translate(12 ${px((plotTop + plotBottom) / 2)}) rotate(-90)`}
          textAnchor="middle"
          dominantBaseline="middle"
        >
          {yLabel}
        </text>
        {features.map((feature, f) => (
          <text
            key={feature.name}
            className="rc-stats-category"
            x={px(plotLeft + (f + 0.5) * layout.band)}
            y={plotBottom + 16}
            textAnchor="middle"
          >
            {feature.label}
          </text>
        ))}
        {groups.map((group, g) =>
          features.map((feature, f) => {
            const bar = bars[g][f];
            const x = barX(f, g);
            const center = x + layout.bar / 2;
            const cap = Math.min(CAP, layout.bar / 2);
            const title = barTitle(group, feature, bar, unit);
            return (
              <g key={`${g}/${feature.name}`} className="rc-stats-mark">
                <title>{title}</title>
                <rect
                  className="rc-stats-hit"
                  x={px(x - GAP / 2)}
                  y={plotTop}
                  width={layout.bar + GAP}
                  height={plotBottom - plotTop}
                />
                {bar && (
                  <path
                    className={isNoise(group) ? 'rc-stats-bar rc-stats-noise' : 'rc-stats-bar'}
                    d={barPath(x, layout.bar, y(baseline), y(bar.mean))}
                    fill={colorOf(group)}
                  />
                )}
                {bar && bar.std !== null && (
                  <path
                    className="rc-stats-err"
                    d={
                      `M${px(center)},${px(y(bar.mean - bar.std))}V${px(y(bar.mean + bar.std))}` +
                      `M${px(center - cap)},${px(y(bar.mean - bar.std))}H${px(center + cap)}` +
                      `M${px(center - cap)},${px(y(bar.mean + bar.std))}H${px(center + cap)}`
                    }
                  />
                )}
              </g>
            );
          }),
        )}
        <line
          className="rc-stats-baseline"
          x1={plotLeft}
          x2={plotRight}
          y1={px(y(baseline))}
          y2={px(y(baseline))}
        />
      </svg>
    </div>
  );
}
