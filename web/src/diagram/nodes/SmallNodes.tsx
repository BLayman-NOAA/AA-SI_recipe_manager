/** The small diagram nodes: external references, ghosts and the inputs and outputs boxes. */
import { clip, plainText, plural } from '../../text';
import { rowValue, type InputRow } from '../buildView';
import { BoxHandles, ExpandButton, Handles, stateClass, useDiagram, type ViewNodeProps } from './common';

export function ExternalNode({ data }: ViewNodeProps<'external'>) {
  const { node } = data;
  const { external } = node.data;
  return (
    <div
      className={`rc-node rc-pill rc-pill-external ${stateClass(node)}`}
      title="Output of a step in another file"
    >
      <Handles output />
      <span className="rc-pill-label">{external.label}</span>
      {external.type && <span className="rc-pill-type">{external.type}</span>}
    </div>
  );
}

export function GhostNode({ data }: ViewNodeProps<'ghost'>) {
  const { node } = data;
  const { stepId, op, where } = node.data;
  return (
    <div
      className={`rc-node rc-ghost ${stateClass(node)}`}
      title="Step outside this sub-recipe (double click to go to it)"
    >
      <Handles output bus="out" />
      <div className="rc-ghost-id">{stepId}</div>
      {op && <div className="rc-ghost-op">{op}</div>}
      <div className="rc-ghost-where">{where ?? 'top level'}</div>
    </div>
  );
}

/** Rows of inputs or overrides: name, value (clipped, in full in its title) and where it comes from. */
export function InputRows({ rows }: { rows: InputRow[] }) {
  return (
    <ul className="rc-box-rows">
      {rows.map((row) => {
        const value = rowValue(row);
        return (
          <li key={row.name} title={plainText(row.description) || undefined}>
            <span className="rc-box-name">{row.name}</span>
            <span className={`rc-box-value${row.set ? ' rc-box-value-set' : ''}`} title={value}>
              {clip(value)}
            </span>
            {row.from.length > 0 && (
              <span className="rc-box-from">from {row.from.map((ref) => ref.step).join(', ')}</span>
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** The recipe inputs, or drilled in the include's overrides; a click selects the whole box. */
export function InputsBoxNode({ data }: ViewNodeProps<'inputsBox'>) {
  const { node } = data;
  const { id, title, include, rows, expanded } = node.data;
  const { toggleBoxExpanded } = useDiagram();
  return (
    <div className={`rc-node rc-box rc-box-inputs${include ? ' rc-box-parent' : ''} ${stateClass(node)}`}>
      <BoxHandles source target={include !== null} />
      <div className="rc-box-head">
        <div>
          <div className="rc-box-title">{title}</div>
          <div className="rc-box-count">{plural(rows.length, 'input')}</div>
        </div>
        <ExpandButton expanded={expanded} what="inputs" onClick={() => toggleBoxExpanded(id)} />
      </div>
      {expanded && rows.length === 0 && include && (
        <div className="rc-box-empty">{include.title} sets no overrides</div>
      )}
      {expanded && rows.length > 0 && <InputRows rows={rows} />}
    </div>
  );
}

/**
 * One producer's outputs that leave the view: those read later, emphasized,
 * the recipe outputs, and drilled in the ones nothing reads, muted.
 */
export function OutputsBoxNode({ data }: ViewNodeProps<'outputsBox'>) {
  const { node } = data;
  const { ownerLabel, ownerTitle, rows, expanded, muted, summary } = node.data;
  const { toggleBoxExpanded } = useDiagram();
  return (
    <div className={`rc-node rc-box rc-box-outputs${muted ? ' rc-box-muted' : ''} ${stateClass(node)}`}>
      <BoxHandles target />
      <div className="rc-box-head">
        <div>
          <div className="rc-box-title" title={ownerTitle}>
            Outputs <span className="rc-box-owner">{ownerLabel}</span>
          </div>
          <div className="rc-box-count">{summary}</div>
        </div>
        <ExpandButton expanded={expanded} what="outputs" onClick={() => toggleBoxExpanded(node.id)} />
      </div>
      {expanded && (
        <ul className="rc-box-rows">
          {rows.map((row) => (
            <li key={`${row.kind}:${row.name}`} className={`rc-box-row-${row.kind}`}>
              <code>{row.name}</code>
              {row.type && <span className="rc-box-type">{row.type}</span>}
              {row.kind === 'final' && <span className="rc-box-tag">recipe output</span>}
              {row.kind === 'exit' && <span className="rc-box-from">used by {row.by.join(', ')}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
