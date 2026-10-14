import type { ViewNodeProps } from './common';

/** A mapped chain drawn around its members: a dashed container with its label. */
export function MapGroupNode({ data }: ViewNodeProps<'mapGroup'>) {
  const { group } = data.node.data;
  return (
    <div className="rc-group rc-map-group">
      <span className="rc-map-group-label">{group.label}</span>
    </div>
  );
}
