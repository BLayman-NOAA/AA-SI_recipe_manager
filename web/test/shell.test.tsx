import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { DockviewApi } from 'dockview-react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App } from '../src/App';
import { buildMenus, itemTooltip, tabMenuItems, type Commands, type MenuState } from '../src/shell/menus';

import { PANES, placementFor } from '../src/shell/panes';
import {
  LAYOUT_KEY,
  POPOUT_URL,
  addPopoutWindow,
  dockedLayout,
  onPopoutWindows,
  persistLayout,
  popoutPane,
  removePopoutWindow,
  returnPane,
  useDock,
} from '../src/shell/dock';
import { Dialog } from '../src/shell/Dialog';
import { useDialogs } from '../src/shell/dialogs';
import { installShortcuts, matchShortcut, type KeyInput } from '../src/shell/shortcuts';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import { editableSession, mockApi, testQueryClient } from './helpers';

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
    save: noop,
    saveAs: noop,
    revert: noop,
    undo: noop,
    redo: noop,
    findStep: noop,
    showPane: noop,
    togglePane: noop,
    popoutPane: noop,
    returnPane: noop,
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
    showEchogramForSelected: noop,
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
  run: { canRun: false, canRunSelected: false, canRunStep: false, isActive: false, isCancelling: false },
  edit: { dirty: false, typing: false, canUndo: false, canRedo: false, undoKind: null, redoKind: null, note: null },
  surveyRegion: true,
  textSize: 1.15,
  echogramSelected: false,
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
    expect(save).not.toHaveAttribute('title');
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

  it('puts the Run Config pane beside Run Settings and toggles it from the View menu', async () => {
    const user = userEvent.setup();
    expect(placementFor('runConfig', new Set(['inspector']))).toEqual({ reference: 'inspector', direction: 'within' });
    renderApp();
    await waitFor(() => expect(useDock.getState().open).toContain('runConfig'));
    const api = useDock.getState().api!;
    expect(api.getPanel('runConfig')!.group.id).toBe(api.getPanel('runSettings')!.group.id);

    await user.click(screen.getByRole('button', { name: 'View' }));
    const toggle = screen.getByRole('menuitemcheckbox', { name: /^Run Config/ });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    await user.click(toggle);
    await waitFor(() => expect(useDock.getState().open).not.toContain('runConfig'));
    await user.click(screen.getByRole('button', { name: 'View' }));
    await user.click(screen.getByRole('menuitemcheckbox', { name: /^Run Config/ }));
    await waitFor(() => expect(useDock.getState().open).toContain('runConfig'));
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
      'save',
      'save-as',
      'revert',
      'undo',
      'redo',
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
      'show-echogram',
    ]);
    const menus = buildMenus(STATE, noCommands());
    const disabled = menus
      .flatMap((menu) => menu.items)
      .filter((item) => !item.enabled && !waitingForSession.has(item.id));
    expect(disabled.length).toBeGreaterThan(5);
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

    const idle = { ...STATE, sid: 's1', run: { ...STATE.run, canRun: true } };
    expect(item(idle, 'dry-run').enabled).toBe(true);
    expect(item(idle, 'run-all').enabled).toBe(true);
    expect(item(idle, 'run-to-selected').enabled).toBe(false);
    expect(item(idle, 'cancel').enabled).toBe(false);
    expect(item(idle, 'run-history').enabled).toBe(true);

    const selected = { ...idle, run: { ...idle.run, canRunSelected: true, canRunStep: true } };
    expect(item(selected, 'run-to-selected').enabled).toBe(true);
    expect(item(selected, 'run-selected-only').enabled).toBe(true);
    expect(item(selected, 'force-rerun-selected').enabled).toBe(true);
    // A sub-recipe selected: Run to Selected runs through it; the step only items stay off.
    const include = { ...idle, run: { ...idle.run, canRunSelected: true } };
    expect(item(include, 'run-to-selected').enabled).toBe(true);
    expect(item(include, 'run-selected-only').enabled).toBe(false);
    expect(item(include, 'force-rerun-selected').enabled).toBe(false);
    item(selected, 'run-to-selected').action!();
    expect(commands.runToSelected).toHaveBeenCalledTimes(1);

    const running = { ...idle, run: { ...idle.run, canRun: false, isActive: true } };
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
    expect(byId('show-echogram')).toMatchObject({ label: 'Show Selected in Echogram', enabled: false });
  });

  it('enables Show Selected in Echogram with a step the eye icon shows on', () => {
    const commands = noCommands();
    commands.showEchogramForSelected = vi.fn();
    const cache = buildMenus({ ...STATE, sid: 's1', echogramSelected: true }, commands).find((menu) => menu.label === 'Cache')!;
    const item = cache.items.find((entry) => entry.id === 'show-echogram')!;
    expect(item.enabled).toBe(true);
    item.action!();
    expect(commands.showEchogramForSelected).toHaveBeenCalledTimes(1);
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
      'echogram-window',
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

  it('enables the File and Edit items by the editing state and wires them', () => {
    const commands = noCommands();
    commands.save = vi.fn();
    commands.saveAs = vi.fn();
    commands.revert = vi.fn();
    commands.undo = vi.fn();
    commands.redo = vi.fn();
    const item = (state: MenuState, menu: string, id: string) =>
      buildMenus(state, commands)
        .find((entry) => entry.label === menu)!
        .items.find((entry) => entry.id === id)!;

    const clean = { ...STATE, sid: 's1' };
    expect(item(clean, 'File', 'save').enabled).toBe(false);
    expect(item(clean, 'File', 'save-as').enabled).toBe(true);
    expect(item(clean, 'File', 'revert').enabled).toBe(false);
    expect(item(clean, 'Edit', 'undo')).toMatchObject({ label: 'Undo', enabled: false });
    expect(item(clean, 'Edit', 'redo')).toMatchObject({ label: 'Redo', enabled: false });
    expect(item(STATE, 'File', 'save-as').enabled).toBe(false);

    // Typing waiting out the debounce enables Save, as Ctrl+S sends it first; Revert waits for the edit.
    const typing = { ...clean, edit: { ...clean.edit, typing: true } };
    expect(item(typing, 'File', 'save').enabled).toBe(true);
    expect(item(typing, 'File', 'revert').enabled).toBe(false);

    const edited = {
      ...clean,
      edit: {
        dirty: true,
        typing: false,
        canUndo: true,
        canRedo: true,
        undoKind: 'set_param',
        redoKind: 'replace_text',
        note: null,
      },
    };
    expect(item(edited, 'File', 'save')).toMatchObject({ enabled: true, shortcut: 'Ctrl+S' });
    expect(item(edited, 'File', 'revert').enabled).toBe(true);
    expect(item(edited, 'Edit', 'undo')).toMatchObject({ label: 'Undo parameter change', enabled: true });
    expect(item(edited, 'Edit', 'redo')).toMatchObject({ label: 'Redo text edit', enabled: true });
    for (const [menu, id] of [
      ['File', 'save'],
      ['File', 'save-as'],
      ['File', 'revert'],
      ['Edit', 'undo'],
      ['Edit', 'redo'],
    ]) {
      item(edited, menu, id).action!();
    }
    expect(commands.save).toHaveBeenCalledTimes(1);
    expect(commands.saveAs).toHaveBeenCalledTimes(1);
    expect(commands.revert).toHaveBeenCalledTimes(1);
    expect(commands.undo).toHaveBeenCalledTimes(1);
    expect(commands.redo).toHaveBeenCalledTimes(1);
  });

  it('puts the restart note on Undo as its tooltip', () => {
    const note = '2 older edits could not be restored after the restart';
    const state = { ...STATE, sid: 's1', edit: { ...STATE.edit, canUndo: true, undoKind: 'set_param', note } };
    const undo = buildMenus(state, noCommands())
      .find((menu) => menu.label === 'Edit')!
      .items.find((entry) => entry.id === 'undo')!;
    expect(itemTooltip(undo)).toBe(note);
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

describe('pane windows', () => {
  type Leaf = { type: string; data: unknown; visible?: boolean };

  function leafOf(node: Leaf, group: string): Leaf | undefined {
    if (node.type === 'leaf') return (node.data as { id: string }).id === group ? node : undefined;
    return (node.data as Leaf[]).map((child) => leafOf(child, group)).find(Boolean);
  }

  function tab(title: string): HTMLElement {
    const tabs = [...document.querySelectorAll<HTMLElement>('.dv-default-tab')];
    return tabs.find((element) => element.textContent === title)!;
  }

  async function renderLayout() {
    renderApp();
    await waitFor(() => expect(useDock.getState().open).toHaveLength(PANES.length));
    return useDock.getState().api!;
  }

  beforeEach(() => {
    useView.getState().resetScope();
    useDialogs.getState().close();
  });

  it('offers the echogram in a new window from the View menu', () => {
    const commands = noCommands();
    commands.popoutPane = vi.fn();
    const view = buildMenus(STATE, commands).find((menu) => menu.label === 'View')!;
    const item = view.items.find((entry) => entry.id === 'echogram-window')!;
    expect(item).toMatchObject({ label: 'Echogram in New Window', enabled: true });
    item.action!();
    expect(commands.popoutPane).toHaveBeenCalledWith('echogram');
  });

  it('builds the tab menu for a pane in the main window and in a popout', () => {
    const commands = { popoutPane: vi.fn(), returnPane: vi.fn() };
    const docked = tabMenuItems('logs', false, commands);
    expect(docked.map((item) => item.label)).toEqual(['Open in New Window']);
    docked[0].action!();
    expect(commands.popoutPane).toHaveBeenCalledWith('logs');
    const popped = tabMenuItems('logs', true, commands);
    expect(popped.map((item) => item.label)).toEqual(['Return to Main Window']);
    popped[0].action!();
    expect(commands.returnPane).toHaveBeenCalledWith('logs');
  });

  it('opens a pane in a new window from its tab menu', async () => {
    const user = userEvent.setup();
    const api = await renderLayout();
    const addPopoutGroup = vi.spyOn(api, 'addPopoutGroup').mockResolvedValue(true);

    fireEvent.contextMenu(tab('Problems'));
    const menu = screen.getByRole('menu', { name: 'Pane' });
    expect(within(menu).queryByRole('menuitem', { name: /Return to Main Window/ })).toBeNull();
    await user.click(within(menu).getByRole('menuitem', { name: /Open in New Window/ }));
    expect(addPopoutGroup).toHaveBeenCalledWith(api.getPanel('problems'), { popoutUrl: POPOUT_URL });
    expect(POPOUT_URL).toBe('/popout.html');
    expect(screen.queryByRole('menu', { name: 'Pane' })).toBeNull();

    fireEvent.contextMenu(tab('Logs'));
    expect(screen.getByRole('menu', { name: 'Pane' })).toBeInTheDocument();
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(screen.queryByRole('menu', { name: 'Pane' })).toBeNull();
  });

  it('shows a closed pane before opening it in a new window', async () => {
    const user = userEvent.setup();
    const api = await renderLayout();
    await user.click(screen.getByRole('button', { name: 'View' }));
    await user.click(screen.getByRole('menuitemcheckbox', { name: /^Echogram/ }));
    await waitFor(() => expect(useDock.getState().open).not.toContain('echogram'));
    const addPopoutGroup = vi.spyOn(api, 'addPopoutGroup').mockResolvedValue(true);

    popoutPane('echogram');
    expect(api.getPanel('echogram')).toBeDefined();
    expect(addPopoutGroup).toHaveBeenCalledWith(api.getPanel('echogram'), { popoutUrl: POPOUT_URL });
  });

  it('moves a pane into a popout window, saves it docked, and closes the window to return it', async () => {
    // An iframe stands in for the window dockview opens.
    const frame = document.createElement('iframe');
    document.body.append(frame);
    const popout = frame.contentWindow!;
    const open = vi.fn(() => popout);
    vi.stubGlobal('open', open);
    try {
      const api = await renderLayout();
      const home = api.getPanel('problems')!.group.id;
      popoutPane('problems');
      await waitFor(() => expect(open).toHaveBeenCalledTimes(1));
      expect(open.mock.calls[0]).toContain(POPOUT_URL);
      popout.dispatchEvent(new Event('load'));
      await waitFor(() => expect(api.getPopouts()).toHaveLength(1));
      expect(api.getPanel('problems')!.group.api.location.type).toBe('popout');
      expect(popout.document.documentElement.dataset.theme).toBe(useView.getState().theme);

      // The tab now lives in the popout's document, and its menu opens there.
      const popped = [...popout.document.querySelectorAll<HTMLElement>('.dv-default-tab')];
      expect(popped.map((element) => element.textContent)).toEqual(['Problems']);
      fireEvent.contextMenu(popped[0]);
      const menu = within(popout.document.body).getByRole('menu', { name: 'Pane' });
      expect(within(menu).getByRole('menuitem', { name: /Return to Main Window/ })).toBeInTheDocument();

      persistLayout();
      const saved = JSON.parse(window.localStorage.getItem(LAYOUT_KEY)!);
      expect(saved.popoutGroups).toBeUndefined();
      expect(leafOf(saved.grid.root, home)!.data).toMatchObject({ views: expect.arrayContaining(['problems']) });

      const close = vi.spyOn(popout, 'close').mockImplementation(() => {});
      returnPane('problems');
      expect(close).toHaveBeenCalledTimes(1);
    } finally {
      frame.remove();
    }
  });

  it('pops out a pane alone in its group without leaving an empty group, and returns it there', async () => {
    const frame = document.createElement('iframe');
    document.body.append(frame);
    const popout = frame.contentWindow!;
    const open = vi.fn(() => popout);
    vi.stubGlobal('open', open);
    const emptyGroups = (api: DockviewApi) =>
      api.groups.filter(
        (group) => group.api.location.type === 'grid' && group.api.isVisible && group.panels.length === 0,
      );
    try {
      const api = await renderLayout();
      const home = api.getPanel('echogram')!.group;
      expect(home.panels).toHaveLength(1);
      popoutPane('echogram');
      await waitFor(() => expect(open).toHaveBeenCalledTimes(1));
      popout.dispatchEvent(new Event('load'));
      await waitFor(() => expect(api.getPopouts()).toHaveLength(1));
      expect(api.getPanel('echogram')!.group.api.location.type).toBe('popout');
      expect(emptyGroups(api)).toEqual([]);
      // The layout reports the window to whatever listens in popouts.
      const closed = vi.fn();
      const watched: Window[] = [];
      const stop = onPopoutWindows((target) => {
        watched.push(target);
        return closed;
      });
      expect(watched).toEqual([popout]);

      // The user closing the window docks it back where it was.
      popout.dispatchEvent(new Event('beforeunload'));
      await waitFor(() => expect(api.getPopouts()).toHaveLength(0));
      const back = api.getPanel('echogram')!.group;
      expect(back.api.location.type).toBe('grid');
      expect(back.api.isVisible).toBe(true);
      expect(back.id).toBe(home.id);
      expect(emptyGroups(api)).toEqual([]);
      expect(closed).toHaveBeenCalledTimes(1);
      stop();
    } finally {
      frame.remove();
    }
  });

  it('docks popped out panes when the layout is saved and restored', async () => {
    const first = await renderLayout();
    const layout = first.toJSON();
    const home = first.getPanel('echogram')!.group.id;

    // The layout as dockview writes it with the echogram popped out: its home
    // group left empty and hidden, the pane in a popout group.
    const leaf = leafOf(layout.grid.root as Leaf, home)!;
    const group = leaf.data as { views: string[]; activeView?: string };
    layout.popoutGroups = [
      { data: { ...structuredClone(group), id: '99' } as never, gridReferenceGroup: home, position: null, url: POPOUT_URL },
    ];
    group.views = [];
    delete group.activeView;
    leaf.visible = false;

    const docked = dockedLayout(layout);
    expect(docked.popoutGroups).toBeUndefined();
    expect(leafOf(docked.grid.root as Leaf, home)).toMatchObject({ data: { views: ['echogram'], activeView: 'echogram' } });
    expect(leafOf(docked.grid.root as Leaf, home)!.visible).toBeUndefined();
    // The popout's pane closes when its home group is gone.
    const orphan = dockedLayout({ ...layout, popoutGroups: [{ ...layout.popoutGroups[0], gridReferenceGroup: 'gone' }] });
    expect(Object.keys(orphan.panels)).not.toContain('echogram');

    const open = vi.fn(() => null);
    vi.stubGlobal('open', open);
    cleanup();
    window.localStorage.setItem(LAYOUT_KEY, JSON.stringify(layout));
    const api = await renderLayout();
    expect(api.getPanel('echogram')!.group.id).toBe(home);
    expect(api.getPopouts()).toHaveLength(0);
    expect(open).not.toHaveBeenCalled();
  });
});

describe('Dialog', () => {
  it('shows in the document it is given and closes on Escape pressed there', () => {
    const frame = document.createElement('iframe');
    document.body.append(frame);
    const doc = frame.contentDocument!;
    const onClose = vi.fn();
    try {
      render(
        <Dialog doc={doc} title="Pick" onClose={onClose}>
          <p>Body</p>
        </Dialog>,
      );
      expect(screen.queryByRole('dialog')).toBeNull();
      expect(within(doc.body).getByRole('dialog', { name: 'Pick' })).toHaveTextContent('Body');
      fireEvent.keyDown(document.body, { key: 'Escape' });
      expect(onClose).not.toHaveBeenCalled();
      fireEvent.keyDown(doc.body, { key: 'Escape' });
      expect(onClose).toHaveBeenCalledTimes(1);
    } finally {
      cleanup();
      frame.remove();
    }
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

  it('leaves undo and redo in a text field to the field', () => {
    expect(matchShortcut(key({ key: 'z', ctrlKey: true }))).toBe('undo');
    expect(matchShortcut(key({ key: 'y', ctrlKey: true }))).toBe('redo');
    expect(matchShortcut(key({ key: 'z', ctrlKey: true, typing: true }))).toBeNull();
    expect(matchShortcut(key({ key: 'y', ctrlKey: true, typing: true }))).toBeNull();
    expect(matchShortcut(key({ key: 's', ctrlKey: true, typing: true }))).toBe('save');
  });

  it('listens in each popout window while it is open', () => {
    // An iframe stands in for the popout window dockview opens.
    const frame = document.createElement('iframe');
    document.body.append(frame);
    const popout = frame.contentWindow!;
    const save = vi.fn();
    const undo = vi.fn();
    const stop = installShortcuts({ save, undo });
    const press = (target: EventTarget, key = 's') => {
      const event = new KeyboardEvent('keydown', { key, ctrlKey: true, bubbles: true, cancelable: true });
      target.dispatchEvent(event);
      return event;
    };
    try {
      addPopoutWindow(popout);
      expect(press(popout.document.body).defaultPrevented).toBe(true);
      expect(save).toHaveBeenCalledTimes(1);
      // A text field in the popout keeps its own undo.
      const field = popout.document.createElement('textarea');
      popout.document.body.append(field);
      expect(press(field, 'z').defaultPrevented).toBe(false);
      expect(undo).not.toHaveBeenCalled();
      press(popout.document.body, 'z');
      expect(undo).toHaveBeenCalledTimes(1);
      press(field);
      expect(save).toHaveBeenCalledTimes(2);
      removePopoutWindow(popout);
      press(popout.document.body);
      expect(save).toHaveBeenCalledTimes(2);
      // A window opened after the shortcuts are installed is listened to as well.
      addPopoutWindow(popout);
      press(popout.document.body);
      expect(save).toHaveBeenCalledTimes(3);
    } finally {
      stop();
      press(popout.document.body);
      expect(save).toHaveBeenCalledTimes(3);
      removePopoutWindow(popout);
      frame.remove();
    }
  });

  it('runs the handler outside a text field and the browser default inside one', () => {
    const undo = vi.fn();
    const save = vi.fn();
    const stop = installShortcuts({ undo, save });
    const textarea = document.createElement('textarea');
    document.body.append(textarea);
    try {
      const inField = new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
      textarea.dispatchEvent(inField);
      expect(undo).not.toHaveBeenCalled();
      expect(inField.defaultPrevented).toBe(false);

      const outside = new KeyboardEvent('keydown', { key: 'z', ctrlKey: true, bubbles: true, cancelable: true });
      document.body.dispatchEvent(outside);
      expect(undo).toHaveBeenCalledTimes(1);
      expect(outside.defaultPrevented).toBe(true);

      const saveInField = new KeyboardEvent('keydown', { key: 's', ctrlKey: true, bubbles: true, cancelable: true });
      textarea.dispatchEvent(saveInField);
      expect(save).toHaveBeenCalledTimes(1);
      expect(saveInField.defaultPrevented).toBe(true);
    } finally {
      stop();
      textarea.remove();
    }
  });
});

describe('editing in the shell', () => {
  const SID = editableSession().id;
  const note = '1 older edit could not be restored after the restart';
  const session = editableSession({
    dirty: ['sub/leaf.yaml'],
    files: editableSession().files.map((file) => ({ ...file, dirty: file.path === 'sub/leaf.yaml' })),
    history: { can_undo: true, can_redo: false, undo_kind: 'set_param', redo_kind: null, seq: 3, note },
  });

  beforeEach(() => {
    useView.getState().resetScope();
    useDialogs.getState().close();
    useSessionStore.getState().setSid(SID);
    document.title = 'AA-SI Recipe Manager';
  });

  afterEach(() => {
    useSessionStore.getState().setSid(null);
  });

  function renderEditedApp() {
    const fetchMock = mockApi({
      'GET /api/health': { status: 'ok', version: '9.8.7', dev: true, state_dir: '/state', docs_url: 'https://docs.example.org/' },
      'GET /api/sessions': [],
      'GET /api/recipes': { roots: [], recipes: [] },
      'GET /api/ops': { schema: 1, counts: { ops: 0, implementations: 0, sources_resolved: 0 }, categories: [], ops: [] },
      [`GET /api/sessions/${SID}`]: session,
      [`GET /api/sessions/${SID}/runs`]: [],
      [`POST /api/sessions/${SID}/sync`]: { session, reloaded: [], conflicts: [], entries: [] },
      [`POST /api/sessions/${SID}/undo`]: { session, changed: false, changed_hashes: [], entries: [] },
    });
    render(<App client={testQueryClient()} />);
    return fetchMock;
  }

  it('marks unsaved edits in the status bar and the page title', async () => {
    renderEditedApp();
    expect(await screen.findByText('session: with_sub (unsaved: 1 file)')).toBeInTheDocument();
    await waitFor(() => expect(document.title).toBe('*AA-SI Recipe Manager'));
  });

  it('enables Undo from the history, with the restart note, and undoes', async () => {
    const user = userEvent.setup();
    const fetchMock = renderEditedApp();
    await screen.findByText('session: with_sub (unsaved: 1 file)');
    await user.click(screen.getByRole('button', { name: 'Edit' }));
    const undo = screen.getByRole('menuitem', { name: /Undo parameter change/ });
    expect(undo).not.toHaveAttribute('aria-disabled', 'true');
    expect(undo).toHaveAttribute('title', note);
    expect(screen.getByRole('menuitem', { name: /^Redo/ })).toHaveAttribute('aria-disabled', 'true');
    await user.click(undo);
    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([url]) => url === `/api/sessions/${SID}/undo`)).toBe(true),
    );
  });

  it('reverts after the user confirms', async () => {
    const user = userEvent.setup();
    const fetchMock = renderEditedApp();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    await screen.findByText('session: with_sub (unsaved: 1 file)');
    const posted = () => fetchMock.mock.calls.filter(([url]) => url === `/api/sessions/${SID}/revert`).length;

    await user.click(screen.getByRole('button', { name: 'File' }));
    await user.click(screen.getByRole('menuitem', { name: /^Revert/ }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(posted()).toBe(0);

    await user.click(screen.getByRole('button', { name: 'File' }));
    await user.click(screen.getByRole('menuitem', { name: /^Revert/ }));
    await waitFor(() => expect(posted()).toBe(1));
    confirm.mockRestore();
  });

  it('opens Save with Ctrl+S', async () => {
    const user = userEvent.setup();
    renderEditedApp();
    await screen.findByText('session: with_sub (unsaved: 1 file)');
    await user.keyboard('{Control>}s{/Control}');
    const dialog = await screen.findByRole('dialog', { name: 'Save' });
    expect(within(dialog).getByText('sub/leaf.yaml')).toBeInTheDocument();
  });

  it('opens Save As with Ctrl+Shift+S', async () => {
    const user = userEvent.setup();
    renderEditedApp();
    await screen.findByText('session: with_sub (unsaved: 1 file)');
    await user.keyboard('{Control>}{Shift>}S{/Shift}{/Control}');
    expect(await screen.findByRole('dialog', { name: 'Save As' })).toBeInTheDocument();
  });
});

