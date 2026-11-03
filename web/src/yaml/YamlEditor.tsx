/**
 * A plain text editor for one YAML file: a textarea with a line number gutter
 * that follows its scrolling, and the highlighted line drawn behind the text.
 * The textarea's text is transparent over a coloured copy of it, so the text
 * shows the same colours as the read only view. Undo inside it is the
 * browser's own.
 */
import { Fragment, memo, useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import { highlightYaml, type Token } from './highlight';
import { YamlTokens } from './YamlText';

export interface YamlEditorProps {
  text: string;
  onChange: (text: string) => void;
  /** The line to highlight and scroll to, from 0, or null. */
  highlight: number | null;
  label: string;
}

/** The line height in pixels the textarea renders with, for scrolling to a line. */
function lineHeight(element: HTMLElement): number {
  const value = parseFloat(window.getComputedStyle(element).lineHeight);
  return Number.isFinite(value) && value > 0 ? value : 20;
}

/** The line numbers, which follow the scroll through a CSS variable. */
const Gutter = memo(function Gutter({ count, highlight }: { count: number; highlight: number | null }) {
  return (
    <div className="rc-yaml-gutter" aria-hidden="true">
      <div className="rc-yaml-gutter-lines">
        {Array.from({ length: count }, (_, index) => (
          <div key={index} className={`rc-yaml-number${index === highlight ? ' rc-yaml-highlight' : ''}`}>
            {index + 1}
          </div>
        ))}
      </div>
    </div>
  );
});

/**
 * The coloured lines, joined with newlines so the text matches the textarea's.
 * A text that ends in a newline gets one more, since a pre does not draw an
 * empty last line.
 */
const ColouredLines = memo(function ColouredLines({ lines }: { lines: Token[][] }) {
  return (
    <>
      {lines.map((line, index) => (
        <Fragment key={index}>
          {index > 0 && '\n'}
          <YamlTokens tokens={line} />
        </Fragment>
      ))}
      {lines.length > 1 && lines[lines.length - 1].length === 0 && '\n'}
    </>
  );
});

/** The textarea, its coloured copy and its gutter, laid out with one line height so the lines match. */
export function YamlEditor({ text, onChange, highlight, label }: YamlEditorProps) {
  const area = useRef<HTMLTextAreaElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const colors = useRef<HTMLPreElement>(null);
  const [composing, setComposing] = useState(false);
  const lines = useMemo(() => highlightYaml(text), [text]);

  // Only a new highlight scrolls; typing never moves the view.
  useEffect(() => {
    const element = area.current;
    if (highlight === null || !element) return;
    element.scrollTop = Math.max(0, highlight * lineHeight(element) - element.clientHeight / 2);
  }, [highlight]);

  const count = lines.length;
  return (
    <div ref={root} className="rc-yaml rc-yaml-editor">
      <Gutter count={count} highlight={highlight} />
      <div className="rc-yaml-editor-body">
        {highlight !== null && highlight < count && (
          <div
            className="rc-yaml-editor-band rc-yaml-highlight"
            style={{ '--rc-yaml-band': highlight } as CSSProperties}
            aria-hidden="true"
          />
        )}
        <pre ref={colors} className="rc-yaml-editor-colors" aria-hidden="true">
          <ColouredLines lines={lines} />
        </pre>
        <textarea
          ref={area}
          className={`rc-yaml-textarea${composing ? ' rc-yaml-composing' : ''}`}
          aria-label={label}
          value={text}
          wrap="off"
          spellCheck={false}
          autoCapitalize="off"
          autoComplete="off"
          onChange={(event) => onChange(event.target.value)}
          onCompositionStart={() => setComposing(true)}
          onCompositionEnd={() => setComposing(false)}
          onScroll={(event) => {
            // Set on the elements, not through a render, so the coloured text and the
            // gutter move in the same frame as the textarea.
            const { scrollTop: top, scrollLeft: left } = event.currentTarget;
            colors.current?.style.setProperty('transform', `translate(${-left}px, ${-top}px)`);
            root.current?.style.setProperty('--rc-yaml-scroll', `${-top}px`);
          }}
        />
      </div>
    </div>
  );
}
