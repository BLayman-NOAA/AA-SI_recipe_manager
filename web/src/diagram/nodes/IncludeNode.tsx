import { plainText } from '../../text';
import { includeMeta } from '../buildView';
import { ExpandButton, Handles, IconButton, Spinner, stateClass, useDiagram, type ViewNodeProps } from './common';
import { InputRows } from './SmallNodes';

/**
 * A collapsed sub-recipe: title, counts, the ports that connect it, its
 * overrides and own inputs when expanded, and drill in.
 */
export function IncludeNode({ data }: ViewNodeProps<'include'>) {
  const { node } = data;
  const { include, inPorts, outPorts, expanded, rows, summary, surveyBacked, status } = node.data;
  const marks = [
    surveyBacked && 'rc-survey-backed',
    status === 'failed' && 'rc-actual-failed',
    status === 'running' && 'rc-running',
  ]
    .filter(Boolean)
    .join(' ');
  const { drillInto, toggleBoxExpanded } = useDiagram();

  return (
    <div
      className={`rc-node rc-include ${stateClass(node)} ${marks}`}
      title={plainText(include.description) || undefined}
    >
      <Handles inPorts={inPorts} outPorts={outPorts} input output bus="both" />
      {status === 'running' && (
        <div className="rc-run-marks">
          <Spinner />
        </div>
      )}
      {summary && (
        <div className="rc-run-marks rc-include-summary" title="What a run would do with the steps inside">
          <span className="rc-run-mark">{summary}</span>
        </div>
      )}
      <div className="rc-include-head">
        <div className="rc-include-title">{include.title}</div>
        <div className="rc-node-tools">
          <IconButton
            icon="drill"
            title="Drill in"
            onClick={() => drillInto(include.id)}
            className="rc-node-tool rc-node-button"
          />
        </div>
        <span className="rc-include-toggle">
          <ExpandButton expanded={expanded} what="inputs" onClick={() => toggleBoxExpanded(include.id)} />
        </span>
      </div>
      <div className="rc-include-meta">{includeMeta(node.data)}</div>
      <div className="rc-include-source">{include.source}</div>
      {(inPorts.length > 0 || outPorts.length > 0) && (
        <div className="rc-include-ports">
          <ul>
            {inPorts.map((port) => (
              <li key={port.id} title={port.title}>
                {port.label}
              </li>
            ))}
          </ul>
          <ul className="rc-include-outs">
            {outPorts.map((port) => (
              <li key={port.id} title={port.title}>
                {port.label}
              </li>
            ))}
          </ul>
        </div>
      )}
      {expanded &&
        (rows.length === 0 ? <div className="rc-box-empty">No inputs</div> : <InputRows rows={rows} sections />)}
    </div>
  );
}
