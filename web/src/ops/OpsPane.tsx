import { useState } from 'react';
import { useOps } from '../api/queries';
import { opsUrl, useDocsUrl } from '../shell/links';
import { categoryColor, categoryLabel } from '../theme/categories';
import { Prose } from '../Prose';
import { firstSentence, plainText } from '../text';
import { groupOps } from './opList';

/** The op catalog by category, searchable. Read only until the add step wizard. */
export function OpsPane() {
  const ops = useOps();
  const docsUrl = useDocsUrl();
  const [query, setQuery] = useState('');
  const groups = ops.data ? groupOps(ops.data, query) : [];

  return (
    <div className="rc-pane">
      <div className="rc-pane-toolbar">
        <input
          className="rc-input"
          type="search"
          placeholder="Search ops"
          aria-label="Search ops"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {ops.isPending && <div className="rc-empty">Loading ops...</div>}
      {ops.error && <div className="rc-field-error">{ops.error.message}</div>}
      {ops.data && groups.length === 0 && <div className="rc-empty">No ops match.</div>}
      {groups.map((group) => (
        <section key={group.category ?? ''}>
          <h3>{categoryLabel(group.category)}</h3>
          <ul className="rc-list">
            {group.ops.map((op) => (
              <li key={op.op} className="rc-row" title={plainText(op.description) || undefined}>
                <span className="rc-swatch" style={{ background: categoryColor(op.category) }} />
                <div className="rc-row-main">
                  <div className="rc-row-title">
                    <a href={opsUrl(docsUrl, op.op)} target="_blank" rel="noreferrer">
                      {op.op}
                    </a>{' '}
                    {op.experimental && <span className="rc-badge">experimental</span>}
                    {op.sink && <span className="rc-badge">sink</span>}
                  </div>
                  <div className="rc-row-sub">
                    <Prose text={firstSentence(op.description)} />
                  </div>
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
