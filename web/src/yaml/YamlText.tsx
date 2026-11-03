/** Read only YAML text, coloured, with line numbers and a highlighted line. */
import { useEffect, useMemo, useRef } from 'react';
import { highlightYaml, type Token } from './highlight';

export interface YamlTextProps {
  text: string;
  highlight: number | null;
}

/** Read only, syntax coloured text with line numbers; the highlighted line is scrolled into view. */
export function YamlText({ text, highlight }: YamlTextProps) {
  const container = useRef<HTMLPreElement>(null);
  const lines = useMemo(() => highlightYaml(text), [text]);

  useEffect(() => {
    if (highlight === null) return;
    const line = container.current?.querySelector(`[data-line="${highlight}"]`);
    line?.scrollIntoView?.({ block: 'center' });
  }, [highlight, text]);

  return (
    <pre ref={container} className="rc-yaml">
      {lines.map((line, index) => (
        <div
          key={index}
          data-line={index}
          className={`rc-yaml-line${index === highlight ? ' rc-yaml-highlight' : ''}`}
        >
          <span className="rc-yaml-number">{index + 1}</span>
          <span className="rc-yaml-code">{line.length === 0 ? ' ' : <YamlTokens tokens={line} />}</span>
        </div>
      ))}
    </pre>
  );
}

/** The coloured spans of one line, shared by the read only text and the editor. */
export function YamlTokens({ tokens }: { tokens: Token[] }) {
  return (
    <>
      {tokens.map((token, part) => (
        <span key={part} className={`rc-tok-${token.kind}`}>
          {token.text}
        </span>
      ))}
    </>
  );
}
