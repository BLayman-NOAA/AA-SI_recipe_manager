/// <reference types="node" />
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { highlightLine, highlightYaml, type TokenKind } from '../src/yaml/highlight';

/** The non blank tokens of a line as `kind:text` pairs. */
function kinds(line: string): string[] {
  return highlightLine(line)
    .filter((token) => token.text.trim() !== '')
    .map((token) => `${token.kind}:${token.text.trim()}`);
}

describe('highlightLine', () => {
  it('colours a key and a plain string value', () => {
    expect(kinds('  op: compute_sv')).toEqual(['key:op', 'punct::', 'string:compute_sv']);
  });

  it('colours list dashes, keys, numbers and literals', () => {
    expect(kinds('  - id: seed')).toEqual(['punct:-', 'key:id', 'punct::', 'string:seed']);
    expect(kinds('    factor: 2.5')).toEqual(['key:factor', 'punct::', 'number:2.5']);
    expect(kinds('    show: false')).toEqual(['key:show', 'punct::', 'literal:false']);
    expect(kinds('    save_dir: null')).toEqual(['key:save_dir', 'punct::', 'literal:null']);
  });

  it('colours references inside plain and quoted strings', () => {
    expect(kinds('    ds: ${compute_sv.ds_Sv}')).toEqual(['key:ds', 'punct::', 'ref:${compute_sv.ds_Sv}']);
    expect(kinds('    path: "./out/${inputs.name}.csv"')).toEqual([
      'key:path',
      'punct::',
      'string:"./out/',
      'ref:${inputs.name}',
      'string:.csv"',
    ]);
  });

  it('colours comments, but not a hash inside a value', () => {
    expect(kinds('# Level 2: calibrated Sv')).toEqual(['comment:# Level 2: calibrated Sv']);
    expect(kinds('    bin: 10s  # seconds')).toEqual(['key:bin', 'punct::', 'string:10s', 'comment:# seconds']);
    expect(kinds('    url: "a#b"')).toEqual(['key:url', 'punct::', 'string:"a#b"']);
  });

  it('colours flow lists item by item', () => {
    expect(kinds('    window: [0, 2400]')).toEqual([
      'key:window',
      'punct::',
      'punct:[',
      'number:0',
      'punct:,',
      'number:2400',
      'punct:]',
    ]);
  });

  it('keeps every character of the line', () => {
    const line = '  - id: dive_sv   # a step {x}';
    expect(highlightLine(line).map((token) => token.text).join('')).toBe(line);
  });
});

describe('highlightYaml', () => {
  it('colours block scalar text as text, not as keys', () => {
    const lines = highlightYaml(
      ['description: >', '  Mask: the seabed', '  and noise.', 'steps:'].join('\n'),
    );
    const kindsOf = (index: number): TokenKind[] => lines[index].map((token) => token.kind);
    expect(kindsOf(0)).toContain('punct');
    expect(kindsOf(1)).toEqual(['block']);
    expect(kindsOf(2)).toEqual(['block']);
    expect(kindsOf(3)).toContain('key');
  });

  it('returns one token list per line, CRLF included', () => {
    expect(highlightYaml('a: 1\r\nb: 2\r\n')).toHaveLength(3);
  });
});

/** Every YAML file under the repo's docs recipes and the client fixtures. */
function recipeFiles(): string[] {
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const path = join(dir, name);
      if (statSync(path).isDirectory()) walk(path);
      else if (/[.]ya?ml$/.test(name)) files.push(path);
    }
  };
  ['../docs/recipes', '../tests/fixtures/client_recipes'].forEach(walk);
  return files;
}

describe('real recipes', () => {
  it('keeps every line of every repo recipe and fixture intact', () => {
    const files = recipeFiles();
    expect(files.length).toBeGreaterThan(5);
    for (const file of files) {
      const text = readFileSync(file, 'utf-8');
      const lines = text.split(/\r?\n/);
      highlightYaml(text).forEach((tokens, index) => {
        expect(tokens.map((token) => token.text).join(''), `${file}:${index + 1}`).toBe(lines[index]);
      });
    }
  });
});
