import 'dockview-react/dist/styles/dockview.css';
import {
  DockviewReact,
  themeDark,
  themeLight,
  type DockviewApi,
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
import { RunConfigPane } from '../runs/RunConfigPane';
import { RunSettingsPane } from '../runs/RunSettingsPane';
import { RunsPane } from '../runs/RunsPane';
import { useView, type Theme } from '../state/view';
import { YamlPane } from '../yaml/YamlPane';
import {
  addPopoutWindow,
  persistLayout,
  removePopoutWindow,
  restoreLayout,
  syncOpenPanes,
  useDock,
} from './dock';
import { PaneTab } from './PaneTab';
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
  runConfig: pane(RunConfigPane),
  logs: pane(LogsPane),
  runs: pane(RunsPane),
  outputs: pane(OutputsPane),
  problems: pane(ProblemsPane),
  echogram: pane(EchogramPane),
};

const SAVE_DELAY_MS = 300;

const dockTheme = (theme: Theme) => (theme === 'dark' ? themeDark : themeLight);

/**
 * Theme a popout window like the main one. Dockview copies the page's styles
 * into it; the theme attribute and dockview's theme class are set here.
 */
function themeWindow(target: Window, theme: Theme): void {
  const doc = target.document;
  doc.documentElement.dataset.theme = theme;
  const container = doc.querySelector('.dv-popout-window');
  container?.classList.remove(themeDark.className, themeLight.className);
  container?.classList.add(dockTheme(theme).className);
}

function themePopouts(api: DockviewApi | null, theme: Theme): void {
  for (const popout of api?.getPopouts() ?? []) themeWindow(popout.window, theme);
}

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
    event.api.onDidAddPopoutGroup((popout) => {
      themeWindow(popout.window, useView.getState().theme);
      addPopoutWindow(popout.window);
    });
    event.api.onWillClosePopoutWindow((popout) => removePopoutWindow(popout.window));
    restoreLayout(event.api);
  };

  useEffect(() => themePopouts(useDock.getState().api, theme), [theme]);

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
        theme={dockTheme(theme)}
        components={PANE_COMPONENTS}
        defaultTabComponent={PaneTab}
        onReady={onReady}
      />
    </div>
  );
}
