import { opLabel } from '../../api/graph';
import { Prose } from '../../Prose';
import { clip, firstSentence, plainText } from '../../text';
import type { Badge } from '../buildView';
import { Badges, ExpandButton, Handles, stateClass, useDiagram, type ViewNodeProps } from './common';

const BADGE_CLASS: Record<Badge, string> = {
  'per item': 'rc-node-badge-map',
  collects: 'rc-node-badge-map',
  sweep: '',
  checkpoint: 'rc-node-badge-checkpoint',
  'writes files': 'rc-node-badge-sink',
  custom: 'rc-node-badge-custom',
};

/** A recipe step: id and op, ports, badges and, expanded, its parameters. */
export function StepNode({ data }: ViewNodeProps<'step'>) {
  const { node } = data;
  const { step, badges, expanded, params } = node.data;
  const { toggleStepExpanded } = useDiagram();
  const inPorts = step.inputs.map((port) => ({
    id: `in:${port.name}`,
    label: port.name,
    title: `${port.name}${port.type ? ` (${port.type})` : ''}`,
  }));
  const outPorts = step.outputs.map((port) => ({
    id: `out:${port.name}`,
    label: port.name,
    title: `${port.name}${port.type ? ` (${port.type})` : ''}`,
  }));

  return (
    <div className={`rc-node rc-step ${stateClass(node)}`}>
      <Handles inPorts={inPorts} outPorts={outPorts} input output bus="both" />
      <div className="rc-step-head">
        <div className="rc-step-id">{step.id}</div>
        <ExpandButton expanded={expanded} what="parameters" onClick={() => toggleStepExpanded(step.id)} />
      </div>
      <div className="rc-step-op">{opLabel(step)}</div>
      {step.description && (
        <div className="rc-step-description" title={plainText(step.description)}>
          <Prose text={firstSentence(step.description)} />
        </div>
      )}
      <Badges
        items={badges.map((badge) => (
          <span key={badge} className={`rc-node-badge ${BADGE_CLASS[badge]}`}>
            {badge}
          </span>
        ))}
      />
      {expanded &&
        (params.length === 0 ? (
          <div className="rc-step-params rc-box-empty">No parameters.</div>
        ) : (
          <table className="rc-step-params">
            <tbody>
              {params.map((param) => (
                <tr key={param.name} title={plainText(param.description) || undefined}>
                  <td>{param.name}</td>
                  <td className={param.set ? 'rc-step-param-set' : undefined} title={param.value}>
                    {param.value ? clip(param.value) : <span className="rc-muted">none</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        ))}
    </div>
  );
}
