/** The menu bar's menus as data, built from the client state and its commands. */
import type { SessionSummary } from '../api/models';
import type { Direction, Theme } from '../state/view';
import { opsUrl } from './links';
import { PANES, type PaneId } from './panes';

export interface MenuItem {
  id: string;
  label: string;
  shortcut?: string;
  enabled: boolean;
  /** Milestone that brings a disabled item, shown as its tooltip. */
  milestone?: number;
  /** Set for toggles: whether the option is on. */
  checked?: boolean;
  action?: () => void;
  /** Submenu items. */
  items?: MenuItem[];
  /** Draw a separator above this item. */
  separator?: boolean;
}

export interface Menu {
  id: string;
  label: string;
  items: MenuItem[];
}

/** What the menus show. */
export interface MenuState {
  sessions: SessionSummary[];
  sid: string | null;
  openPanes: ReadonlySet<string>;
  connected: boolean;
  direction: Direction;
  theme: Theme;
  /** Docs site root, ending in "/". */
  docsUrl: string;
}

/** What the menus do. */
export interface Commands {
  openRecipe: () => void;
  openSession: (sid: string) => void;
  closeSession: () => void;
  findStep: () => void;
  showPane: (id: PaneId) => void;
  togglePane: (id: PaneId) => void;
  toggleConnected: () => void;
  expandAll: () => void;
  collapseAll: () => void;
  toggleDirection: () => void;
  fit: () => void;
  resetLayout: () => void;
  toggleTheme: () => void;
  openUrl: (url: string) => void;
  showShortcuts: () => void;
  showAbout: () => void;
}

export function milestoneTooltip(milestone: number): string {
  return `Arrives in milestone ${milestone}`;
}

/** The tooltip of an item: the milestone note for disabled items. */
export function itemTooltip(item: MenuItem): string | undefined {
  if (!item.enabled && item.milestone !== undefined) return milestoneTooltip(item.milestone);
  return undefined;
}

function later(id: string, label: string, milestone: number, shortcut?: string): MenuItem {
  return { id, label, milestone, shortcut, enabled: false };
}

function sessionItems(state: MenuState, commands: Commands): MenuItem[] {
  if (state.sessions.length === 0) {
    return [{ id: 'recent-none', label: 'No sessions', enabled: false }];
  }
  return state.sessions.map((session) => ({
    id: `recent-${session.id}`,
    label: `${session.title} (${session.created.slice(0, 16).replace('T', ' ')})`,
    enabled: true,
    checked: session.id === state.sid,
    action: () => commands.openSession(session.id),
  }));
}

export function buildMenus(state: MenuState, commands: Commands): Menu[] {
  const hasSession = state.sid !== null;
  const directionLabel = state.direction === 'RIGHT' ? 'left to right' : 'top to bottom';
  return [
    {
      id: 'file',
      label: 'File',
      items: [
        { id: 'open-recipe', label: 'Open Recipe', enabled: true, action: commands.openRecipe },
        later('new-recipe', 'New Recipe', 9),
        {
          id: 'recent-sessions',
          label: 'Recent Sessions',
          enabled: true,
          items: sessionItems(state, commands),
        },
        later('save', 'Save', 5, 'Ctrl+S'),
        later('save-as', 'Save As', 5, 'Ctrl+Shift+S'),
        later('revert', 'Revert', 5),
        {
          id: 'close-session',
          label: 'Delete Session',
          enabled: hasSession,
          separator: true,
          action: commands.closeSession,
        },
      ],
    },
    {
      id: 'edit',
      label: 'Edit',
      items: [
        later('undo', 'Undo', 5, 'Ctrl+Z'),
        later('redo', 'Redo', 5, 'Ctrl+Y'),
        later('delete', 'Delete', 9, 'Delete'),
        later('rename-step', 'Rename Step', 9),
        {
          id: 'find-step',
          label: 'Find Step',
          shortcut: 'Ctrl+F',
          enabled: hasSession,
          separator: true,
          action: commands.findStep,
        },
      ],
    },
    {
      id: 'recipe',
      label: 'Recipe',
      items: [
        later('add-step', 'Add Step', 9),
        later('add-custom-step', 'Add Custom Step', 9),
        later('add-sub-recipe', 'Add Sub-recipe', 9),
        {
          id: 'recipe-inputs',
          label: 'Recipe Inputs',
          enabled: true,
          separator: true,
          action: () => commands.showPane('inputs'),
        },
        {
          id: 'run-settings',
          label: 'Run Settings',
          enabled: true,
          action: () => commands.showPane('runSettings'),
        },
      ],
    },
    {
      id: 'run',
      label: 'Run',
      items: [
        later('dry-run', 'Dry Run', 3),
        later('run-all', 'Run All', 3),
        later('run-to-selected', 'Run to Selected', 3, 'Ctrl+Enter'),
        later('run-selected-only', 'Run Selected Only', 3, 'Ctrl+Shift+Enter'),
        later('force-rerun-selected', 'Force Rerun Selected', 3),
        later('force-rerun-all', 'Force Rerun All', 3),
        later('cancel', 'Cancel', 3),
        { ...later('run-history', 'Run History', 3), separator: true },
      ],
    },
    {
      id: 'view',
      label: 'View',
      items: [
        {
          id: 'connected',
          label: 'Highlight Connected',
          enabled: true,
          checked: state.connected,
          action: commands.toggleConnected,
        },
        {
          id: 'direction',
          label: `Layout Direction: ${directionLabel}`,
          enabled: true,
          action: commands.toggleDirection,
        },
        { id: 'expand-all', label: 'Expand All', enabled: hasSession, action: commands.expandAll },
        { id: 'collapse-all', label: 'Collapse All', enabled: hasSession, action: commands.collapseAll },
        { id: 'fit', label: 'Fit', shortcut: 'F', enabled: true, action: commands.fit },
        {
          id: 'theme',
          label: 'Light Theme',
          enabled: true,
          checked: state.theme === 'light',
          separator: true,
          action: commands.toggleTheme,
        },
        { id: 'reset-layout', label: 'Reset Layout', enabled: true, action: commands.resetLayout },
        ...PANES.map((pane, index) => ({
          id: `pane-${pane.id}`,
          label: pane.title,
          enabled: true,
          checked: state.openPanes.has(pane.id),
          separator: index === 0,
          action: () => commands.togglePane(pane.id),
        })),
      ],
    },
    {
      id: 'cache',
      label: 'Cache',
      items: [
        later('refresh-preview', 'Refresh Preview', 3),
        later('explain-miss', 'Explain Miss for Selected', 3),
        later('survey-region', 'Show Survey Backed Region', 3),
      ],
    },
    {
      id: 'help',
      label: 'Help',
      items: [
        {
          id: 'op-reference',
          label: 'Op Reference',
          enabled: true,
          action: () => commands.openUrl(opsUrl(state.docsUrl)),
        },
        {
          id: 'docs',
          label: 'Recipe Manager Docs',
          enabled: true,
          action: () => commands.openUrl(state.docsUrl),
        },
        {
          id: 'shortcuts',
          label: 'Keyboard Shortcuts',
          enabled: true,
          separator: true,
          action: commands.showShortcuts,
        },
        { id: 'about', label: 'About', enabled: true, action: commands.showAbout },
      ],
    },
  ];
}
