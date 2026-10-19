import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { buildMenus, type Commands, type MenuState } from '../src/shell/menus';

import { PANES, placementFor } from '../src/shell/panes';
import { LAYOUT_KEY, useDock } from '../src/shell/dock';
import { matchShortcut, type KeyInput } from '../src/shell/shortcuts';
import { useView } from '../src/state/view';
import { mockApi, testQueryClient } from './helpers';

const MENUS = ['File', 'Edit', 'Recipe', 'Run', 'View', 'Cache', 'Help'];

function renderApp() {
  mockApi({
    'GET /api/health': {
      status: 'ok',
      version: '9.8.7',
      dev: true,
      state_dir: '/state',
      docs_url: 'https://docs.example.org/',
    },
    'GET /api/sessions': [],
    'GET /api/recipes': { roots: [], recipes: [] },
    'GET /api/ops': { schema: 1, counts: { ops: 0, implementations: 0, sources_resolved: 0 }, categories: [], ops: [] },
  });
  return render(<App client={testQueryClient()} />);
}

function noCommands(): Commands {
  const noop = () => {};
  return {
    openRecipe: noop,
    openSession: noop,
    closeSession: noop,
    findStep: noop,
    showPane: noop,
    togglePane: noop,
    toggleConnected: noop,
    expandAll: noop,
    collapseAll: noop,
    toggleDirection: noop,
    fit: noop,
    resetLayout: noop,
    toggleTheme: noop,
    setTextSize: noop,
    openUrl: noop,
    showShortcuts: noop,
    showAbout: noop,
    dryRun: noop,
    runAll: noop,
    runToSelected: noop,
    runSelectedOnly: noop,
    forceRerunSelected: noop,
    forceRerunAll: noop,
    cancelRun: noop,
    stopRun: noop,
    showRunHistory: noop,
    refreshPreview: noop,
    toggleSurveyRegion: noop,
  };
}

const STATE: MenuState = {
  sessions: [],
  sid: null,
  openPanes: new Set(['diagram']),
  connected: false,
  direction: 'RIGHT',
  theme: 'dark',
  docsUrl: 'https://docs.example.org/',
  run: { canRun: false, canRunSelected: false, isActive: false, isCancelling: false },
  surveyRegion: true,
  textSize: 1.15,
};

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('menu bar', () => {
  beforeEach(() => {
    useView.getState().resetScope();
  });

  it('renders the seven menus', () => {
    renderApp();
    const bar = screen.getByRole('menubar');
    const labels = within(bar)
      .getAllByRole('button', { name: /.+/ })
      .filter((button) => button.className === 'rc-menu-button')
      .map((button) => button.textContent);
    expect(labels).toEqual(MENUS);
  });

  it('lists the File items, with milestone tooltips on disabled ones', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole('button', { name: 'File' }));
    const menu = screen.getByRole('menu', { name: 'File' });
    const items = within(menu)
      .getAllByRole('menuitem')
      .map((item) => item.querySelector('.rc-menu-label')?.textContent);
    expect(items).toEqual([
      'Open Recipe',
      'New Recipe',
      'Recent Sessions',
      'Save',
      'Save As',
      'Revert',
      'Delete Session',
    ]);
    const byLabel = (label: string) =>
      within(menu)
        .getAllByRole('menuitem')
        .find((item) => item.querySelector('.rc-menu-label')?.textContent === label)!;
    const save = byLabel('Save');
    expect(save).toHaveAttribute('aria-disabled', 'true');
    expect(save).toHaveAttribute('title', 'Arrives in milestone 5');
    expect(byLabel('New Recipe')).toHaveAttribute('title', 'Arrives in milestone 9');
    expect(byLabel('Open Recipe')).not.toHaveAttribute('aria-disabled', 'true');
  });

  it('opens the Keyboard Shortcuts dialog from Help', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole('button', { name: 'Help' }));
    await user.click(screen.getByRole('menuitem', { name: /Keyboard Shortcuts/ }));
    const dialog = screen.getByRole('dialog', { name: 'Keyboard Shortcuts' });
    expect(within(dialog).getByText('Ctrl+Enter')).toBeInTheDocument();
    expect(within(dialog).getByText('Fit the diagram')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Close' }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('shows the server version in About', async () => {
    const user = userEvent.setup();
    renderApp();
    await user.click(screen.getByRole('button', { name: 'Help' }));
    await user.click(screen.getByRole('menuitem', { name: /About/ }));
    const dialog = screen.getByRole('dialog', { name: 'About' });
    await waitFor(() => expect(within(dialog).getByText('9.8.7')).toBeInTheDocument());
  });

  it('builds the default layout and toggles a pane from the View menu', async () => {
    const user = userEvent.setup();
    renderApp();
    await waitFor(() => expect(useDock.getState().open).toHaveLength(PANES.length));

    await user.click(screen.getByRole('button', { name: 'View' }));
    const toggle = screen.getByRole('menuitemcheckbox', { name: /^Problems/ });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    await user.click(toggle);
    await waitFor(() => expect(useDock.getState().open).not.toContain('problems'));
    await waitFor(() => expect(window.localStorage.getItem(LAYOUT_KEY)).not.toBeNull());

    await user.click(screen.getByRole('button', { name: 'View' }));
    await user.click(screen.getByRole('menuitemcheckbox', { name: /^Problems/ }));
    await waitFor(() => expect(useDock.getState().open).toContain('problems'));
  });

  it('restores the saved layout after a reload', async () => {
    const user = userEvent.setup();
    const first = renderApp();
    await waitFor(() => expect(useDock.getState().open).toHaveLength(PANES.length));
    await user.click(screen.getByRole('button', { name: 'View' }));
    await user.click(screen.getByRole('menuitemcheckbox', { name: /^Echogram/ }));
    // Wait for the save after the close, not an earlier save of the default layout.
    await waitFor(() => {
      const saved = window.localStorage.getItem(LAYOUT_KEY);
      expect(saved).toContain('problems');
      expect(saved).not.toContain('"echogram"');
    });
    first.unmount();

    renderApp();
    await waitFor(() => expect(useDock.getState().open).toHaveLength(PANES.length - 1));
    expect(useDock.getState().open).not.toContain('echogram');
  });

  it('falls back to the default layout when the saved one is unusable', async () => {
    window.localStorage.setItem(LAYOUT_KEY, JSON.stringify({ panels: { nope: {} } }));
    renderApp();
    await waitFor(() => expect(useDock.getState().open).toHaveLength(PANES.length));
  });

  it('toggles the layout direction from the toolbar, naming the current one', async () => {
    const user = userEvent.setup();
    useView.setState({ direction: 'RIGHT' });
    renderApp();
    const button = screen.getByRole('button', { name: /Toggle Layout Direction/ });
    expect(button).toHaveTextContent('(left to right)');
    expect(button).toHaveAttribute('title', 'Layout is left to right. Click for top to bottom.');
    await user.click(button);
    expect(useView.getState().direction).toBe('DOWN');
    expect(button).toHaveAttribute('title', 'Layout is top to bottom. Click for left to right.');
  });

  it('toggles a view option from the View menu', async () => {
    const user = userEvent.setup();
    renderApp();
    const before = useView.getState().connected;
    await user.click(screen.getByRole('button', { name: 'View' }));
    await user.click(screen.getByRole('menuitemcheckbox', { name: /Highlight Connected/ }));
    expect(useView.getState().connected).toBe(!before);
  });
});

describe('buildMenus', () => {
  it('gives every item disabled for a later milestone that milestone', () => {
    const waitingForSession = new Set([
      'close-session',
      'find-step',
      'expand-all',
      'collapse-all',
      'dry-run',
      'run-all',
      'run-to-selected',
      'run-selected-only',
      'force-rerun-selected',
      'force-rerun-all',
      'cancel',
      'stop-now',
      'refresh-preview',
    ]);
    const menus = buildMenus(STATE, noCommands());
    const disabled = menus
      .flatMap((menu) => menu.items)
      .filter((item) => !item.enabled && !waitingForSession.has(item.id));
    expect(disabled.length).toBeGreaterThan(10);
    for (const item of disabled) {
      expect(item.milestone, item.id).toBeGreaterThan(1);
    }
  });

  it('wires the M1 items to commands', () => {
    const commands = noCommands();
    commands.showPane = vi.fn();
    commands.togglePane = vi.fn();
    const menus = buildMenus({ ...STATE, sid: 's1' }, commands);
    const item = (menu: string, id: string) =>
      menus.find((entry) => entry.label === menu)!.items.find((entry) => entry.id === id)!;

    item('Recipe', 'recipe-inputs').action!();
    expect(commands.showPane).toHaveBeenCalledWith('inputs');
    item('View', 'pane-problems').action!();
    expect(commands.togglePane).toHaveBeenCalledWith('problems');
    expect(item('File', 'close-session').enabled).toBe(true);
    expect(item('View', 'pane-diagram').checked).toBe(true);
    expect(item('View', 'pane-problems').checked).toBe(false);
  });

  it('enables the Run items by the run state and wires them to commands', () => {
    const commands = noCommands();
    commands.runToSelected = vi.fn();
    commands.cancelRun = vi.fn();
    commands.stopRun = vi.fn();
    commands.showRunHistory = vi.fn();
    const item = (state: MenuState, id: string) =>
      buildMenus(state, commands)
        .find((menu) => menu.label === 'Run')!
        .items.find((entry) => entry.id === id)!;

    const idle = { ...STATE, sid: 's1', run: { canRun: true, canRunSelected: false, isActive: false, isCancelling: false } };
    expect(item(idle, 'dry-run').enabled).toBe(true);
    expect(item(idle, 'run-all').enabled).toBe(true);
    expect(item(idle, 'run-to-selected').enabled).toBe(false);
    expect(item(idle, 'cancel').enabled).toBe(false);
    expect(item(idle, 'run-history').enabled).toBe(true);

    const selected = { ...idle, run: { canRun: true, canRunSelected: true, isActive: false, isCancelling: false } };
    expect(item(selected, 'run-to-selected').enabled).toBe(true);
    expect(item(selected, 'run-selected-only').enabled).toBe(true);
    item(selected, 'run-to-selected').action!();
    expect(commands.runToSelected).toHaveBeenCalledTimes(1);

    const running = { ...idle, run: { canRun: false, canRunSelected: false, isActive: true, isCancelling: false } };
    expect(item(running, 'run-all').enabled).toBe(false);
    expect(item(running, 'cancel').enabled).toBe(true);
    expect(item(idle, 'stop-now').enabled).toBe(false);
    expect(item(running, 'stop-now').enabled).toBe(false);
    const cancelling = { ...running, run: { ...running.run, isCancelling: true } };
    expect(item(cancelling, 'stop-now')).toMatchObject({ label: 'Stop Now', enabled: true });
    item(cancelling, 'stop-now').action!();
    expect(commands.stopRun).toHaveBeenCalledTimes(1);
    item(running, 'cancel').action!();
    item(running, 'run-history').action!();
    expect(commands.cancelRun).toHaveBeenCalledTimes(1);
    expect(commands.showRunHistory).toHaveBeenCalledTimes(1);
  });

  it('enables the Cache items, with Explain Miss waiting for milestone 7', () => {
    const commands = noCommands();
    commands.toggleSurveyRegion = vi.fn();
    const cache = buildMenus({ ...STATE, sid: 's1' }, commands).find((menu) => menu.label === 'Cache')!;
    const byId = (id: string) => cache.items.find((entry) => entry.id === id)!;
    expect(byId('refresh-preview').enabled).toBe(true);
    expect(byId('explain-miss')).toMatchObject({ enabled: false, milestone: 7 });
    expect(byId('survey-region')).toMatchObject({ enabled: true, checked: true });
    byId('survey-region').action!();
    expect(commands.toggleSurveyRegion).toHaveBeenCalledTimes(1);
  });

  it('lists the view options and panes in the View menu', () => {
    const view = buildMenus(STATE, noCommands()).find((menu) => menu.label === 'View')!;
    expect(view.items.map((item) => item.id)).toEqual([
      'connected',
      'direction',
      'expand-all',
      'collapse-all',
      'fit',
      'text-size',
      'theme',
      'reset-layout',
      ...PANES.map((pane) => `pane-${pane.id}`),
    ]);
    expect(view.items.slice(0, 4).map((item) => item.label)).toEqual([
      'Highlight Connected',
      'Layout Direction: left to right',
      'Expand All',
      'Collapse All',
    ]);
  });

  it('offers the diagram text sizes with the current one checked', () => {
    const commands = noCommands();
    commands.setTextSize = vi.fn();
    const view = buildMenus(STATE, commands).find((menu) => menu.label === 'View')!;
    const sizes = view.items.find((item) => item.id === 'text-size')!.items!;
    expect(sizes.map((item) => item.label)).toEqual(['100%', '115%', '130%', '150%']);
    expect(sizes.filter((item) => item.checked).map((item) => item.label)).toEqual(['115%']);
    sizes[2].action!();
    expect(commands.setTextSize).toHaveBeenCalledWith(1.3);
  });

  it('asks the diagram to expand or collapse everything from the View menu', () => {
    const commands = noCommands();
    commands.expandAll = vi.fn();
    commands.collapseAll = vi.fn();
    const view = buildMenus({ ...STATE, sid: 's1' }, commands).find((menu) => menu.label === 'View')!;
    view.items.find((item) => item.id === 'expand-all')!.action!();
    view.items.find((item) => item.id === 'collapse-all')!.action!();
    expect(commands.expandAll).toHaveBeenCalledTimes(1);
    expect(commands.collapseAll).toHaveBeenCalledTimes(1);
  });

  it('lists recent sessions as a submenu', () => {
    const commands = noCommands();
    commands.openSession = vi.fn();
    const sessions = [{ id: 's1', title: 'dive', origin: '/r/dive.yaml', created: '2026-10-13T15:30:12Z' }];
    const menus = buildMenus({ ...STATE, sessions, sid: 's1' }, commands);
    const recent = menus[0].items.find((entry) => entry.id === 'recent-sessions')!;
    expect(recent.items![0].label).toBe('dive (2026-10-13 15:30)');
    expect(recent.items![0].checked).toBe(true);
    recent.items![0].action!();
    expect(commands.openSession).toHaveBeenCalledWith('s1');
  });
});

describe('panes', () => {
  it('places a pane by its reference, falling back up the chain', () => {
    const all = new Set(PANES.map((pane) => pane.id));
    expect(placementFor('ops', all)).toEqual({ reference: 'recipes', direction: 'below' });
    expect(placementFor('ops', new Set(['diagram']))).toEqual({ reference: 'diagram', direction: 'left' });
    expect(placementFor('yaml', new Set())).toBeNull();
    expect(placementFor('logs', new Set())).toEqual({ direction: 'below' });
  });
});

describe('shortcuts', () => {
  const key = (input: Partial<KeyInput>): KeyInput => ({
    key: '',
    ctrlKey: false,
    metaKey: false,
    shiftKey: false,
    altKey: false,
    typing: false,
    ...input,
  });

  it('maps keys to shortcuts', () => {
    expect(matchShortcut(key({ key: 'f' }))).toBe('fit');
    expect(matchShortcut(key({ key: 'f', typing: true }))).toBeNull();
    expect(matchShortcut(key({ key: 'f', ctrlKey: true }))).toBe('find');
    expect(matchShortcut(key({ key: '/' }))).toBe('find');
    expect(matchShortcut(key({ key: 'Escape' }))).toBe('escape');
    expect(matchShortcut(key({ key: 'Escape', typing: true }))).toBeNull();
    expect(matchShortcut(key({ key: 'Enter', ctrlKey: true }))).toBe('runToSelected');
    expect(matchShortcut(key({ key: 'Enter', ctrlKey: true, shiftKey: true }))).toBe('runSelectedOnly');
    expect(matchShortcut(key({ key: 'S', ctrlKey: true, shiftKey: true }))).toBe('saveAs');
    expect(matchShortcut(key({ key: 'x' }))).toBeNull();
  });
});
