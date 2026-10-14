/** Syntax colouring for recipe YAML, one token list per line. */

export type TokenKind =
  | 'plain'
  | 'key'
  | 'punct'
  | 'string'
  | 'number'
  | 'literal'
  | 'ref'
  | 'comment'
  | 'block';

export interface Token {
  text: string;
  kind: TokenKind;
}

const KEY = /^(\s*)(-\s+)?((?:"[^"]*"|'[^']*'|[^\s#'"][^:#]*?))(\s*:)(?=\s|$)/;
const DASH = /^(\s*)(-)(?=\s|$)/;
const BLOCK_START = /^[|>][-+0-9]*$/;
const NUMBER = /^[-+]?(\d[\d_]*(\.\d*)?|\.\d+)([eE][-+]?\d+)?$/;
const LITERAL = /^(true|false|yes|no|on|off|null|~)$/i;
const SCALAR_END = /\s#|\s*$/;

/**
 * Split recipe YAML into coloured tokens.
 *
 * Handles mapping keys, list dashes, comments, quoted and plain scalars,
 * numbers, booleans and null, `${...}` references, flow punctuation, and the
 * text of `|` and `>` block scalars, which is tracked across lines so that a
 * description is never coloured as keys.
 */
export function highlightYaml(text: string): Token[][] {
  const result: Token[][] = [];
  // Indent of the line that opened the current block scalar, or null outside one.
  let blockParent: number | null = null;

  for (const line of text.split(/\r?\n/)) {
    const indent = line.length - line.trimStart().length;
    if (blockParent !== null) {
      if (line.trim() === '' || indent > blockParent) {
        result.push(line ? [{ text: line, kind: 'block' }] : []);
        continue;
      }
      blockParent = null;
    }
    const tokens = highlightLine(line);
    if (opensBlock(tokens)) blockParent = indent;
    result.push(tokens);
  }
  return result;
}

/** Whether a line ends with a `|` or `>` block indicator (before any comment). */
function opensBlock(tokens: Token[]): boolean {
  const meaningful = tokens.filter((token) => token.kind !== 'comment' && token.text.trim() !== '');
  const last = meaningful.at(-1);
  return last !== undefined && last.kind === 'punct' && BLOCK_START.test(last.text.trim());
}

/** Tokens for one line outside a block scalar. */
export function highlightLine(line: string): Token[] {
  const tokens: Token[] = [];
  let rest = line;

  const key = KEY.exec(rest);
  if (key) {
    push(tokens, key[1], 'plain');
    if (key[2]) push(tokens, key[2], 'punct');
    push(tokens, key[3], 'key');
    push(tokens, key[4], 'punct');
    rest = rest.slice(key[0].length);
  } else {
    const dash = DASH.exec(rest);
    if (dash) {
      push(tokens, dash[1], 'plain');
      push(tokens, dash[2], 'punct');
      rest = rest.slice(dash[0].length);
    }
  }
  scanValue(rest, tokens);
  return tokens;
}

/** Colour the value part of a line: scalars, references, punctuation, comment. */
function scanValue(text: string, tokens: Token[]) {
  let index = 0;
  while (index < text.length) {
    const char = text[index];
    const rest = text.slice(index);
    if (char === '#' && (index === 0 || /\s/.test(text[index - 1]))) {
      push(tokens, rest, 'comment');
      return;
    }
    if (/\s/.test(char)) {
      const space = /^\s+/.exec(rest)![0];
      push(tokens, space, 'plain');
      index += space.length;
      continue;
    }
    if (char === '"' || char === "'") {
      const end = closingQuote(text, index);
      splitRefs(text.slice(index, end), tokens);
      index = end;
      continue;
    }
    if ('[]{},'.includes(char)) {
      push(tokens, char, 'punct');
      index += 1;
      continue;
    }
    const indicator = /^[|>][-+0-9]*(?=\s|$)/.exec(rest);
    if (indicator && text.slice(0, index).trim() === '') {
      push(tokens, indicator[0], 'punct');
      index += indicator[0].length;
      continue;
    }
    const scalar = plainScalar(rest);
    plain(scalar, tokens);
    index += scalar.length;
  }
}

/** The index just past a quoted scalar starting at `start`. */
function closingQuote(text: string, start: number): number {
  const quote = text[start];
  let index = start + 1;
  while (index < text.length) {
    if (quote === '"' && text[index] === '\\') {
      index += 2;
      continue;
    }
    if (text[index] === quote) {
      if (quote === "'" && text[index + 1] === "'") {
        index += 2;
        continue;
      }
      return index + 1;
    }
    index += 1;
  }
  return text.length;
}

/** A plain scalar up to flow punctuation in a flow context, a comment, or the end. */
function plainScalar(text: string): string {
  const inFlow = /^[^[\]{}]*[\]},]/.test(text);
  const stop = inFlow ? /[,\]}]|\s#/ : SCALAR_END;
  const match = stop.exec(text);
  const end = match && match.index > 0 ? match.index : text.length;
  return text.slice(0, end).replace(/\s+$/, '') || text.slice(0, 1);
}

/** A plain scalar: a number, literal, reference or string. */
function plain(text: string, tokens: Token[]) {
  if (NUMBER.test(text)) push(tokens, text, 'number');
  else if (LITERAL.test(text)) push(tokens, text, 'literal');
  else splitRefs(text, tokens);
}

/** A string, split around `${...}` references. */
function splitRefs(text: string, tokens: Token[]) {
  let last = 0;
  for (const match of text.matchAll(/\$\{[^}]*\}/g)) {
    push(tokens, text.slice(last, match.index), 'string');
    push(tokens, match[0], 'ref');
    last = match.index + match[0].length;
  }
  push(tokens, text.slice(last), 'string');
}

/** Append a token, merging with the previous one of the same kind. */
function push(tokens: Token[], text: string, kind: TokenKind) {
  if (!text) return;
  const previous = tokens.at(-1);
  if (previous && previous.kind === kind) previous.text += text;
  else tokens.push({ text, kind });
}
