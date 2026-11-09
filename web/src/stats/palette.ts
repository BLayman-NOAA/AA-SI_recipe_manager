/** Group colours: the echogram labels layer's palette, so a cluster reads the same in both panes. */
import { NOISE_COLOR, paletteColors } from 'aa-si-echogram-gl';

export function isNoiseId(id: number): boolean {
  return id < 0;
}

/** A colour channel as the byte the labels layer's palette texture holds. */
const byte = (value: number) => Math.min(Math.floor(value * 255), 255);

function css([r, g, b]: [number, number, number]): string {
  return `rgb(${byte(r)}, ${byte(g)}, ${byte(b)})`;
}

/**
 * The labels layer's colour of a cluster label as CSS.
 *
 * The palette lists labels -1 through `highest` in order, and an entry does
 * not depend on `highest`, so any `highest` at or above the label gives the
 * colour the labels layer draws.
 *
 * Args:
 *   id: The cluster label; any negative label is noise.
 *   highest: The largest label in the table.
 */
export function groupColor(id: number, highest: number): string {
  if (isNoiseId(id)) return css(NOISE_COLOR);
  return css(paletteColors(Math.max(highest, id))[id + 1]);
}
