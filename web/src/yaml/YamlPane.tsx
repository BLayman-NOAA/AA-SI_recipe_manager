import { useEffect, useMemo, useRef, useState } from 'react';
import { findInclude, findStep } from '../api/graph';
import { useCurrentSession, useFileText } from '../api/queries';
import { useSessionStore } from '../state/session';
import { useView } from '../state/view';
import { stepLine } from './findLine';
import { highlightYaml } from './highlight';

interface YamlTextProps {
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
          <span className="rc-yaml-code">
            {line.length === 0
              ? ' '
              : line.map((token, part) => (
                  <span key={part} className={`rc-tok-${token.kind}`}>
                    {token.text}
                  </span>
                ))}
          </span>
        </div>
      ))}
    </pre>
  );
}

/** The session's recipe files, one tab each; follows the selected step. */
export function YamlPane() {
  const sid = useSessionStore((state) => state.sid);
  const session = useCurrentSession();
  const selected = useView((state) => state.selected);
  const files = session.data?.files ?? [];
  const graph = session.data?.graph;
  const [active, setActive] = useState<string | null>(null);

  const target = useMemo(() => {
    if (!graph || !selected) return null;
    if (selected.kind === 'step') {
      const step = findStep(graph, selected.id);
      return step ? { path: step.document, stepId: step.id } : null;
    }
    if (selected.kind === 'include') {
      const include = findInclude(graph, selected.id);
      return include ? { path: include.source, stepId: null } : null;
    }
    return null;
  }, [graph, selected]);

  useEffect(() => {
    if (target) setActive(target.path);
  }, [target]);

  const path = files.some((file) => file.path === active) ? active : files[0]?.path ?? null;
  const file = useFileText(sid, path);
  const text = file.data?.text ?? null;
  const highlight =
    text !== null && target?.stepId && target.path === path ? stepLine(text, target.stepId) : null;

  if (files.length === 0) {
    return <div className="rc-placeholder">Open a recipe from the Recipes pane to see its YAML.</div>;
  }
  return (
    <div className="rc-yaml-pane">
      <div className="rc-tabs" role="tablist">
        {files.map((item) => (
          <button
            key={item.path}
            type="button"
            role="tab"
            aria-selected={item.path === path}
            className="rc-tab"
            title={item.origin}
            onClick={() => setActive(item.path)}
          >
            {item.path}
          </button>
        ))}
        <span className="rc-spacer" />
        <span className="rc-badge">read only</span>
      </div>
      {file.isLoading && <div className="rc-empty rc-yaml-status">Loading...</div>}
      {file.error && (
        <div className="rc-error-text rc-yaml-status">
          Could not load {path}: {file.error.message}
        </div>
      )}
      {text !== null && <YamlText text={text} highlight={highlight} />}
    </div>
  );
}
