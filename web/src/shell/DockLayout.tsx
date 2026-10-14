import 'dockview-react/dist/styles/dockview.css';
import {
  DockviewReact,
  themeDark,
  themeLight,
  type DockviewReadyEvent,
  type IDockviewPanelProps,
} from 'dockview-react';
import { useEffect, useRef, type FunctionComponent } from 'react';
import { Diagram } from '../diagram/Diagram';
import { EchogramPane } from '../echogram/EchogramPane';
import { InputsPane } from '../inputs/InputsPane';
import { Inspector } from '../inspector/Inspector';
import { LogsPane } from '../logs/LogsPane';
import { OpsPane } from '../ops/OpsPane';
import { OutputsPane } from '../outputs/OutputsPane';
import { ProblemsPane } from '../problems/ProblemsPane';
import { RecipesPane } from '../recipes/RecipesPane';
import { RunSettingsPane } from '../runs/RunSettingsPane';
import { useView } from '../state/view';
import { YamlPane } from '../yaml/YamlPane';
import { persistLayout, restoreLayout, syncOpenPanes, useDock } from './dock';
import type { PaneId } from './panes';

/** Wrap a pane so Dockview's panel props are not passed to it. */
function pane(Component: FunctionComponent): FunctionComponent<IDockviewPanelProps> {
  return function DockPane() {
    return <Component />;
  };
}

export const PANE_COMPONENTS: Record<PaneId, FunctionComponent<IDockviewPanelProps>> = {
  recipes: pane(RecipesPane),
  ops: pane(OpsPane),
  diagram: pane(Diagram),
  yaml: pane(YamlPane),
  inspector: pane(Inspector),
  inputs: pane(InputsPane),
  runSettings: pane(RunSettingsPane),
  logs: pane(LogsPane),
  outputs: pane(OutputsPane),
  problems: pane(ProblemsPane),
  echogram: pane(EchogramPane),
};

const SAVE_DELAY_MS = 300;

/** The docking area: every pane, the saved or default layout, and layout saving. */
export function DockLayout() {
  const theme = useView((state) => state.theme);
  const saveTimer = useRef<number | undefined>(undefined);

  const onReady = (event: DockviewReadyEvent) => {
    useDock.setState({ api: event.api });
    // Every add, remove, move and restore reports here, so this is the one
    // place the open pane list is refreshed.
    event.api.onDidLayoutChange(() => {
      syncOpenPanes();
      window.clearTimeout(saveTimer.current);
      saveTimer.current = window.setTimeout(persistLayout, SAVE_DELAY_MS);
    });
    restoreLayout(event.api);
  };

  useEffect(
    () => () => {
      window.clearTimeout(saveTimer.current);
      useDock.setState({ api: null, open: [] });
    },
    [],
  );

  return (
    <div className="rc-dock-host">
      <DockviewReact
        className="rc-dock"
        theme={theme === 'dark' ? themeDark : themeLight}
        components={PANE_COMPONENTS}
        onReady={onReady}
      />
    </div>
  );
}
