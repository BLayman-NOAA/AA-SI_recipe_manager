import { plainText, plural } from '../../text';
import { ExpandButton, Handles, IconButton, stateClass, useDiagram, type ViewNodeProps } from './common';
import { InputRows } from './SmallNodes';

/**
 * A collapsed sub-recipe: title, counts, the ports that connect it, its
 * overrides when expanded, and drill in.
 */
export function IncludeNode({ data }: ViewNodeProps<'include'>) {
  const { node } = data;
  const { include, stepCount, overrideCount, inPorts, outPorts, expanded, rows } = node.data;
  const { drillInto, toggleBoxExpanded } = useDiagram();

  return (
    <div className={`rc-node rc-include ${stateClass(node)}`} title={plainText(include.description) || undefined}>
      <Handles inPorts={inPorts} outPorts={outPorts} input output bus="both" />
      <div className="rc-include-head">
        <div className="rc-include-title">{include.title}</div>
        <div className="rc-node-tools">
          <IconButton icon="drill" title="Drill in" onClick={() => drillInto(include.id)} />
        </div>
        <span className="rc-include-toggle">
          <ExpandButton expanded={expanded} what="overrides" onClick={() => toggleBoxExpanded(include.id)} />
        </span>
      </div>
      <div className="rc-include-meta">
        {plural(stepCount, 'step')}, {plural(overrideCount, 'override')}
      </div>
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
      {expanded && (rows.length === 0 ? <div className="rc-box-empty">No overrides</div> : <InputRows rows={rows} />)}
    </div>
  );
}
