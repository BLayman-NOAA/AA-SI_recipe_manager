/** The small diagram nodes: external references, ghosts, the inputs and outputs boxes and the run config box. */
import { clip, plainText, showValue } from '../../text';
import {
  CONFIG_HANDLE,
  configRowTag,
  inputsBoxCount,
  MORE_HANDLE,
  moreText,
  rowHandle,
  rowTag,
  rowValue,
  runConfigCount,
  splitRows,
  type InputRow,
  type RunConfigRow,
} from '../buildView';
import {
  BoxHandles,
  ConfigHandle,
  ExpandButton,
  Handles,
  stateClass,
  useDiagram,
  useHandleRefresh,
  type ViewNodeProps,
} from './common';

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

const MORE_TITLE =
  'Declared in the sub-recipe files and left to their defaults. They can be set with --input or the run config.';

function InputRowItem({ row, handle }: { row: InputRow; handle: boolean }) {
  const value = rowValue(row);
  const tag = rowTag(row);
  const unset = row.setBy === 'unset';
  const classes = ['rc-box-value', row.set && 'rc-box-value-set', unset && 'rc-box-value-unset'];
  return (
    <li title={plainText(row.description) || undefined} className={row.missing ? 'rc-box-row-missing' : undefined}>
      {handle && <ConfigHandle id={rowHandle(row.name)} type="target" row />}
      <span className="rc-box-name">{row.name}</span>
      <span className={classes.filter(Boolean).join(' ')} title={value}>
        {clip(value)}
        {tag && <span className="rc-box-tag">{tag}</span>}
      </span>
      {row.from.length > 0 && <span className="rc-box-from">from {row.from.map((ref) => ref.step).join(', ')}</span>}
      {row.source === 'other' && row.declaredIn && <span className="rc-box-from">declared in {row.declaredIn}</span>}
    </li>
  );
}

/**
 * Rows of overrides and inputs: name, value (clipped, in full in its title),
 * who sets it and where it comes from. With sections, an Overrides and an
 * Inputs header head the two groups; a count of the inputs declared further
 * down closes the list. With handles, each row has one for a run config line.
 */
export function InputRows({
  rows,
  sections = false,
  more = 0,
  handles = false,
}: {
  rows: InputRow[];
  sections?: boolean;
  more?: number;
  handles?: boolean;
}) {
  const { overrides, inputs } = splitRows(rows);
  return (
    <ul className="rc-box-rows">
      {sections && overrides.length > 0 && <li className="rc-box-section">Overrides</li>}
      {overrides.map((row) => (
        <InputRowItem key={`override:${row.name}`} row={row} handle={handles} />
      ))}
      {sections && inputs.length > 0 && <li className="rc-box-section">Inputs</li>}
      {inputs.map((row) => (
        <InputRowItem key={`input:${row.name}`} row={row} handle={handles} />
      ))}
      {more > 0 && (
        <li className="rc-box-more" title={MORE_TITLE}>
          {handles && <ConfigHandle id={MORE_HANDLE} type="target" row />}
          {moreText(more)}
        </li>
      )}
    </ul>
  );
}

/** The recipe inputs, or drilled in the sub-recipe's overrides and inputs; a click selects the whole box. */
export function InputsBoxNode({ data }: ViewNodeProps<'inputsBox'>) {
  const { node } = data;
  const { id, title, include, rows, more, expanded, configLines } = node.data;
  const { toggleBoxExpanded } = useDiagram();
  useHandleRefresh(node.id, configLines ? `${expanded}:${rows.map((row) => row.name).join(',')}` : '');
  return (
    <div className={`rc-node rc-box rc-box-inputs${include ? ' rc-box-parent' : ''} ${stateClass(node)}`}>
      <BoxHandles source target={include !== null} />
      {configLines && <ConfigHandle id={CONFIG_HANDLE} type="target" />}
      <div className="rc-box-head" title={include?.source}>
        <div>
          <div className="rc-box-title">{title}</div>
          <div className="rc-box-count">{inputsBoxCount(node.data)}</div>
        </div>
        <ExpandButton expanded={expanded} what="inputs" onClick={() => toggleBoxExpanded(id)} />
      </div>
      {expanded && rows.length === 0 && more === 0 && include && <div className="rc-box-empty">No inputs</div>}
      {expanded && rows.length + more > 0 && (
        <InputRows rows={rows} sections={include !== null} more={more} handles={configLines} />
      )}
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

/** A config input's row: its value, what becomes of it, and a handle when its line leaves the row. */
function ConfigRowItem({ row }: { row: RunConfigRow }) {
  const value = showValue(row.value) || 'null';
  const tag = configRowTag(row);
  const session = showValue(row.sessionValue) || 'null';
  const title = row.state === 'overridden' ? `The session uses ${session} instead.` : value;
  return (
    <li className={`rc-box-row-config-${row.state}`}>
      {row.handle && <ConfigHandle id={rowHandle(row.name)} type="source" row />}
      <span className="rc-box-name">{row.name}</span>
      <span className={`rc-box-value${row.value === null ? ' rc-box-value-null' : ''}`} title={title}>
        {clip(value)}
        {tag && <span className="rc-box-tag">{tag}</span>}
      </span>
    </li>
  );
}

/**
 * The run config file the session uses, titled with its file name: its inputs,
 * with lines into the recipe inputs box, then its run settings, muted.
 */
export function RunConfigNode({ data }: ViewNodeProps<'runConfig'>) {
  const { node } = data;
  const { path, name, inputs, settings, expanded, error } = node.data;
  const { toggleBoxExpanded } = useDiagram();
  useHandleRefresh(node.id, inputs.filter((row) => row.handle).map((row) => row.name).join(','));
  return (
    <div className={`rc-node rc-box rc-box-config ${stateClass(node)}`}>
      <ConfigHandle id={CONFIG_HANDLE} type="source" />
      <div className="rc-box-head" title={path}>
        <div>
          <div className="rc-box-title rc-box-file">{name}</div>
          <div className="rc-box-count">{runConfigCount(node.data)}</div>
        </div>
        <ExpandButton expanded={expanded} what="run config" onClick={() => toggleBoxExpanded(node.id)} />
      </div>
      {error && <div className="rc-box-error">{error}</div>}
      {expanded && inputs.length + settings.length > 0 && (
        <ul className="rc-box-rows">
          {inputs.map((row) => (
            <ConfigRowItem key={row.name} row={row} />
          ))}
          {settings.length > 0 && <li className="rc-box-section">Settings</li>}
          {settings.map((setting) => (
            <li key={setting.name} className="rc-box-row-setting" title={showValue(setting.value)}>
              <span className="rc-box-name">{setting.name}</span>
              <span className="rc-box-setting-value">{clip(showValue(setting.value))}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
