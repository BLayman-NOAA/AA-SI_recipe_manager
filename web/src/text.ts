/** Small text helpers shared by panes and diagram nodes. */

/** A recipe value as text: strings as they are, everything else as JSON. */
export function showValue(value: unknown): string {
  if (value === null || value === undefined) return '';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

/** How many characters of a value a diagram node shows; the rest is in its title. */
export const VALUE_CLIP = 80;

/** Text clipped to a length with an ellipsis. */
export function clip(text: string, max = VALUE_CLIP): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

/** A value as text, or "unset" when it has none. */
export function valueText(value: unknown): string {
  return showValue(value) || 'unset';
}

/** The first sentence of a description. */
export function firstSentence(text: string | null | undefined): string {
  if (!text) return '';
  const match = /^(.+?[.!?])(\s|$)/.exec(text.trim());
  return match ? match[1] : text.trim();
}

/** "1 step", "2 steps". */
export function plural(count: number, word: string): string {
  return `${count} ${word}${count === 1 ? '' : 's'}`;
}

/** Names joined as "a, b and c". */
export function andList(names: string[]): string {
  return names.length > 1 ? `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}` : names.join('');
}

/** Seconds as "77 s", or "1.2 s" under ten. */
export function secondsText(seconds: number): string {
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`;
}

/** Text with regular expression characters escaped, for use inside a RegExp. */
export function escapeRegExp(text: string): string {
  return text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** A run of description text, marked when it is inline code. */
export interface TextPart {
  text: string;
  code: boolean;
}

const RST_CODE = /``(.+?)``/g;

/** Split text on RST inline code (``depth``) into plain and code parts. */
export function splitCode(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let last = 0;
  for (const match of text.matchAll(RST_CODE)) {
    if (match.index > last) parts.push({ text: text.slice(last, match.index), code: false });
    parts.push({ text: match[1], code: true });
    last = match.index + match[0].length;
  }
  if (last < text.length) parts.push({ text: text.slice(last), code: false });
  return parts;
}

/** Text with RST inline code marks removed, for tooltips. */
export function plainText(text: string | null | undefined): string {
  return text ? text.replace(RST_CODE, '$1') : '';
}
