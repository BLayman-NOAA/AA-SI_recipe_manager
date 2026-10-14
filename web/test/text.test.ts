import { describe, expect, it } from 'vitest';
import { escapeRegExp, firstSentence, plainText, plural, showValue, splitCode } from '../src/text';

describe('text helpers', () => {
  it('splits RST inline code from plain text', () => {
    expect(splitCode('Masks below ``depth`` from the ``.evl`` line.')).toEqual([
      { text: 'Masks below ', code: false },
      { text: 'depth', code: true },
      { text: ' from the ', code: false },
      { text: '.evl', code: true },
      { text: ' line.', code: false },
    ]);
    expect(splitCode('``x``')).toEqual([{ text: 'x', code: true }]);
    expect(splitCode('no code, `single` ticks stay')).toEqual([
      { text: 'no code, `single` ticks stay', code: false },
    ]);
    expect(plainText('Read ``depth``.')).toBe('Read depth.');
  });

  it('shows values, first sentences, plurals and escaped patterns', () => {
    expect(showValue(null)).toBe('');
    expect(showValue('a')).toBe('a');
    expect(showValue([70, 120])).toBe('[70,120]');
    expect(firstSentence('One. Two.')).toBe('One.');
    expect(firstSentence('No end')).toBe('No end');
    expect(plural(1, 'step')).toBe('1 step');
    expect(plural(3, 'step')).toBe('3 steps');
    expect(new RegExp(escapeRegExp('a.b(c)')).test('a.b(c)')).toBe(true);
  });
});
