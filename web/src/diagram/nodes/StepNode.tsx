import { opLabel } from '../../api/graph';
import { Prose } from '../../Prose';
import { tierLetter, type StepBadge } from '../../runs/badges';
import { clip, firstSentence, plainText } from '../../text';
import type { Badge } from '../buildView';
import {
  Badges,
  ExpandButton,
  Handles,
  Icon,
  IconButton,
  Spinner,
  stateClass,
  useDiagram,
  type ViewNodeProps,
} from './common';

const BADGE_CLASS: Record<Badge, string> = {
  'per item': 'rc-node-badge-map',
  collects: 'rc-node-badge-map',
  sweep: '',
  checkpoint: 'rc-node-badge-checkpoint',
  'writes files': 'rc-node-badge-sink',
  custom: 'rc-node-badge-custom',
};

const LETTER_TITLE = { S: 'survey cache', U: 'user cache', M: 'already done (marker)' };

/** Class names for a step's run marks: prediction outline, actual fill, stale, survey backed. */
export function badgeClass(badge: StepBadge | null): string {
  if (!badge) return '';
  return [
    badge.predicted && `rc-pred-${badge.predicted}`,
    badge.actual && `rc-actual-${badge.actual}`,
    badge.actual === 'running' && 'rc-running',
    badge.stale && 'rc-stale',
    badge.surveyBacked && 'rc-survey-backed',
  ]
    .filter(Boolean)
    .join(' ');
}

/**
 * The marks drawn over a step's corner: a spinner while it runs, instance
 * progress, the S, U or M letter and the saved indicator. They sit outside
 * the node's flow, so a change never resizes the node (which would lay the
 * diagram out again).
 */
function RunMarks({ badge }: { badge: StepBadge }) {
  const letter = tierLetter(badge);
  const saved = badge.saved;
  const running = badge.actual === 'running';
  if (!running && !badge.progress && !letter && !saved && !badge.willSave) return null;
  return (
    <div className="rc-run-marks">
      {running && <Spinner />}
      {badge.progress && (
        <span className="rc-run-mark rc-run-progress" title="Instances done">
          {badge.progress.done}/{badge.progress.count ?? '?'}
        </span>
      )}
      {letter && (
        <span className={`rc-run-mark rc-run-letter rc-letter-${letter}`} title={LETTER_TITLE[letter]}>
          {letter}
        </span>
      )}
      {saved && (
        <span
          className="rc-run-mark rc-run-saved"
          title={`A result for the current hash is saved in the ${saved === 'S' ? 'survey' : 'user'} cache`}
        >
          <Icon icon="disk" />
          {saved}
        </span>
      )}
      {badge.willSave && (
        <span className="rc-run-mark rc-run-saved rc-run-pending" title="Will be saved this run">
          <Icon icon="disk" />
        </span>
      )}
    </div>
  );
}

/** A recipe step: id and op, ports, badges, run marks and, expanded, its parameters. */
export function StepNode({ data }: ViewNodeProps<'step'>) {
  const { node } = data;
  const { step, badges, expanded, params, badge } = node.data;
  const { toggleStepExpanded, runToHere, canRun } = useDiagram();
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
  const checkpointed = step.checkpoint === 'always';
  const title = badge?.note ?? (badge?.actual === 'failed' ? 'Failed: click to open its log' : undefined);

  return (
    <div
      className={`rc-node rc-step ${stateClass(node)} ${badgeClass(badge)}${checkpointed ? ' rc-checkpointed' : ''}`}
      title={title}
    >
      <Handles inPorts={inPorts} outPorts={outPorts} input output bus="both" />
      {badge && <RunMarks badge={badge} />}
      {badge?.stale && <span className="rc-stale-corner" title="The hash changed since the result shown" />}
      <div className="rc-step-head">
        <div className="rc-step-id">{step.id}</div>
        {runToHere && (
          <IconButton
            icon="play"
            title={checkpointed ? 'Run to here (a checkpoint in the recipe)' : 'Run to here'}
            className="rc-node-tool rc-play"
            disabled={!canRun}
            onClick={() => runToHere(step.id)}
          />
        )}
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
