/** Items grouped under a header per declaring file, shared by the inspector and the Inputs pane. */
import { Fragment, type ReactNode } from 'react';
import { groupByDocument, type Graph } from '../api/graph';

interface ByFileProps<T> {
  graph: Graph;
  items: T[];
  fileOf: (item: T) => string | null | undefined;
  children: (item: T) => ReactNode;
}

/**
 * Items under a header per declaring file, the root file first and then the
 * sub-recipes in graph order. A recipe of one file gets no header.
 */
export function ByFile<T>({ graph, items, fileOf, children }: ByFileProps<T>) {
  const groups = groupByDocument(graph, items, fileOf);
  return (
    <>
      {groups.map((group) => (
        <Fragment key={group.file ?? ''}>
          {groups.length > 1 && <h3 className="rc-group-head rc-mono">{group.file ?? 'Other inputs'}</h3>}
          {group.items.map(children)}
        </Fragment>
      ))}
    </>
  );
}
