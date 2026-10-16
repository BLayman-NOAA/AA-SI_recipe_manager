/** The recipe files of a graph as the YAML pane shows them, fetched from the docs site. */
import { useEffect, useMemo, useState } from 'react';
import { findInclude, type Graph } from '../api/graph';
import { useViewStore } from '../state/view';
import { stepLine } from '../yaml/findLine';
import { graphFiles, yamlTarget } from '../yaml/followSelection';
import { YamlFiles } from '../yaml/YamlFiles';
import { fetchOnce, recipeFileUrl } from './siteData';

/** The text at a URL, fetched once per page. */
function useStaticText(url: string): { text: string | null; error: string | null } {
  const [state, setState] = useState<{ url: string; text: string | null; error: string | null } | null>(null);
  useEffect(() => {
    let live = true;
    fetchOnce(url, (response) => response.text()).then(
      (text) => live && setState({ url, text, error: null }),
      (error: Error) => live && setState({ url, text: null, error: error.message }),
    );
    return () => {
      live = false;
    };
  }, [url]);
  return state?.url === url ? state : { text: null, error: null };
}

export interface ViewerYamlProps {
  graph: Graph;
  siteRoot: string;
  recipeDir: string;
}

/** One tab per recipe file of the graph; follows the selected step as the YAML pane does. */
export function ViewerYaml({ graph, siteRoot, recipeDir }: ViewerYamlProps) {
  const store = useViewStore();
  const selected = store((state) => state.selected);
  const drill = store((state) => state.drill);
  const files = useMemo(() => graphFiles(graph).map((path) => ({ path })), [graph]);
  const [active, setActive] = useState<string | null>(null);
  const target = useMemo(() => yamlTarget(graph, selected), [graph, selected]);

  useEffect(() => {
    if (target) setActive(target.path);
  }, [target]);

  // Until a node is selected, the file of the sub-recipe drilled into.
  const drilled = drill.length > 0 ? findInclude(graph, drill[drill.length - 1])?.source : undefined;
  const path = files.some((file) => file.path === active) ? active! : (drilled ?? files[0].path);
  const { text, error } = useStaticText(recipeFileUrl(siteRoot, recipeDir, path));
  const highlight = text !== null && target?.stepId && target.path === path ? stepLine(text, target.stepId) : null;

  return (
    <YamlFiles
      files={files}
      active={path}
      onSelect={setActive}
      text={text}
      loading={text === null && !error}
      error={error}
      highlight={highlight}
    />
  );
}
