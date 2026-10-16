import { Handles, type ViewNodeProps } from './common';

/** A mapped chain drawn around its members: a dashed container with its label. */
export function MapGroupNode({ data }: ViewNodeProps<'mapGroup'>) {
  const { label, title } = data.node.data;
  return (
    <div className="rc-group rc-map-group" title={title ?? undefined}>
      <span className="rc-map-group-label">{label}</span>
      <Handles bus="in" />
    </div>
  );
}
