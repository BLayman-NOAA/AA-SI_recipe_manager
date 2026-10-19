import { useEffect, useMemo, useState } from 'react';
import { useCurrentSession, useFileText } from '../api/queries';
import { useSessionStore } from '../state/session';
import { useViewStore } from '../state/view';
import { stepLine } from './findLine';
import { yamlTarget } from './followSelection';
import { YamlFiles } from './YamlFiles';

/** The session's recipe files, one tab each; follows the selected step. */
export function YamlPane() {
  const sid = useSessionStore((state) => state.sid);
  const session = useCurrentSession();
  const selected = useViewStore()((state) => state.selected);
  const files = session.data?.files ?? [];
  const graph = session.data?.graph;
  const [active, setActive] = useState<string | null>(null);

  const target = useMemo(() => (graph ? yamlTarget(graph, selected) : null), [graph, selected]);

  useEffect(() => {
    if (target) setActive(target.path);
  }, [target]);

  const path = files.some((file) => file.path === active) ? active : files[0]?.path ?? null;
  const file = useFileText(sid, path);
  const text = file.data?.text ?? null;
  const highlight =
    text !== null && target?.stepId && target.path === path ? stepLine(text, target.stepId) : null;

  if (files.length === 0) {
    return <div className="rc-placeholder">Open a recipe from the Recipes pane to see its YAML.</div>;
  }
  return (
    <YamlFiles
      files={files.map((item) => ({ path: item.path, title: item.origin }))}
      active={path}
      onSelect={setActive}
      text={text}
      loading={file.isLoading}
      error={file.error?.message ?? null}
      highlight={highlight}
    />
  );
}
