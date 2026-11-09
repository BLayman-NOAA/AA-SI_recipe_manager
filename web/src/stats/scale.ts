/** Axis helpers for the statistics charts. */
import type { Series } from './table';

/** A step of 1, 2 or 5 times a power of ten giving about `count` intervals. */
export function niceStep(span: number, count: number): number {
  const raw = span / Math.max(1, count);
  const power = 10 ** Math.floor(Math.log10(raw));
  const fraction = raw / power;
  const nice = fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10;
  return nice * power;
}

/** Ticks at a nice step inside [min, max]. */
export function niceTicks(min: number, max: number, count = 5): number[] {
  if (!(max > min)) return [min];
  const step = niceStep(max - min, count);
  const ticks: number[] = [];
  for (let k = Math.ceil(min / step - 1e-9); k * step <= max + step * 1e-9; k += 1) {
    // Round away float noise such as 0.30000000000000004.
    ticks.push(Number((k * step).toPrecision(12)));
  }
  return ticks;
}

/** How many decimals the ticks of a step need. */
export function stepDecimals(step: number): number {
  return Math.max(0, -Math.floor(Math.log10(step) + 1e-9));
}

/** A linear map from a domain to a range. */
export function linearScale(domain: [number, number], range: [number, number]): (value: number) => number {
  const [d0, d1] = domain;
  const [r0, r1] = range;
  const span = d1 - d0 || 1;
  return (value) => r0 + ((value - d0) / span) * (r1 - r0);
}

/** Share of the data's span added above and below. */
const PAD = 0.05;

/**
 * The y domain of a chart and the baseline its bars grow from.
 *
 * The span of mean plus and minus SD, padded 5 percent. Sv bars grow from the
 * padded floor, as the reference figure draws them; any other kind grows from
 * zero, which its domain always includes.
 */
export function yAxis(series: Series): { domain: [number, number]; baseline: number } {
  const ends: number[] = [];
  for (const row of series.bars) {
    for (const bar of row) {
      if (bar) ends.push(bar.mean - (bar.std ?? 0), bar.mean + (bar.std ?? 0));
    }
  }
  if (ends.length === 0) return { domain: [0, 1], baseline: 0 };
  let low = Math.min(...ends);
  let high = Math.max(...ends);
  const pad = (high - low || Math.abs(high) || 1) * PAD;
  low -= pad;
  high += pad;
  if (series.kind === 'sv') return { domain: [low, high], baseline: low };
  low = Math.min(low, 0);
  high = Math.max(high, 0);
  return { domain: [low, high], baseline: 0 };
}
