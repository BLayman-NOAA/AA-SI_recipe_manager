// The Run Config pane (FR-84 to FR-86, UC-13): the file in use and who shares
// it, Choose Config and Create Config, and the form's two ways to apply it.
import type { QueryClient } from '@tanstack/react-query';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it } from 'vitest';
import type { InputField, SessionDocument } from '../src/api/models';
import { queryKeys, STALE_VERSION_MESSAGE } from '../src/api/queries';
import { SurveyWarningDialog } from '../src/editing/SurveyWarningDialog';
import { useEditingStore } from '../src/editing/store';
import { RunConfigPane } from '../src/runs/RunConfigPane';
import {
  configInputFields,
  NO_EDITS,
  planEdits,
  saveRequest,
  sessionRequests,
  settingFields,
} from '../src/runs/runConfigForm';
import { useSessionStore } from '../src/state/session';
import {
  apiErrorResponse,
  CONFIG_PATH,
  makePreview,
  makeSession,
  mockApi,
  previewStep,
  renderWithClient,
  runConfigBlock,
  testQueryClient,
} from './helpers';

const SID = '20261013-153012-a1b2c3';
const BASE = `/api/sessions/${SID}`;

/** n (int) from the config, m from the session; the file also has an input no recipe declares. */
function declared(nSource: InputField['source'] = 'config', nValue: unknown = 5): InputField[] {
  const n = makeSession().inputs.declared[0];
  return [
    { ...n, source: nSource, value: nValue },
    { name: 'm', type: 'int', description: null, default: 1, required: false, value: 2, source: 'session' },
  ];
}

function configSession(overrides: Partial<SessionDocument> = {}): SessionDocument {
  return makeSession({
    inputs: { declared: declared(), values: { n: 5, m: 2 } },
    run_config: runConfigBlock({
      user_cache_dir: './u',
      executor: 'dask',
      overrides: { executor: 'dask' },
      contents: { inputs: { n: 5, stray: 'x' }, settings: { user_cache_dir: './u', temp_dir: null } },
      unused_inputs: ['stray'],
      shared_with: ['C:/recipes/other.yaml'],
    }),
    ...overrides,
  });
}

/** The JSON bodies sent with a method to a path. */
function bodies(fetchMock: ReturnType<typeof mockApi>, method: string, path: string): unknown[] {
  return fetchMock.mock.calls
    .filter(([url, init]) => init?.method === method && String(url) === path)
    .map(([, init]) => JSON.parse(String(init?.body)));
}

/** A config save's answer: the file changed, with the steps whose hash changed. */
function saveAnswer(session: SessionDocument, changedHashes: string[] | null = [], cacheChanged = false) {
  return { session, changed: true, shared_with: [], changed_hashes: changedHashes, cache_changed: cacheChanged };
}

/** A client holding a run all preview in which seed is survey backed. */
function clientWithPreview(): QueryClient {
  const client = testQueryClient();
  const survey = previewStep('seed', { held_by: ['survey'], planned: 'hit-survey-cache', tier: 'survey' });
  client.setQueryData(queryKeys.preview(SID, [], false, false, []), makePreview([survey, previewStep('total')]));
  return client;
}

function open(session: SessionDocument, routes: Record<string, unknown> = {}) {
  const fetchMock = mockApi({ [`GET ${BASE}`]: session, ...routes });
  useSessionStore.getState().setSid(SID);
  return fetchMock;
}

/** Save to the config file, for every recipe sharing it, as the pane asks for a shared file. */
async function saveForAll(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: 'Save to config file' }));
  await user.click(await screen.findByRole('button', { name: /^All recipes in this folder/ }));
}

beforeEach(() => {
  useSessionStore.getState().setSid(null);
  useEditingStore.getState().reset();
});

describe('the run config form', () => {
  const session = configSession();
  const block = session.run_config;
  const fields = (edits = NO_EDITS) => configInputFields(block, session.inputs.declared, edits);

  it('lists the file inputs in file order, then the added ones, with every run setting', () => {
    const edits = { inputs: { m: { draft: '4' } }, settings: {} };
    expect(fields(edits).map((field) => [field.name, field.inFile, field.type, field.field?.name ?? null])).toEqual([
      ['n', true, 'int', 'n'],
      ['stray', true, 'str', null],
      ['m', false, 'int', 'm'],
    ]);
    const settings = settingFields(block);
    expect(settings.map((field) => field.name)).toEqual([
      'user_cache_dir',
      'survey_cache_dir',
      'outputs_dir',
      'temp_dir',
      'executor',
      'dask_scheduler',
      'dask_workers',
      'storage_options',
    ]);
    expect(settings[4]).toEqual({ name: 'executor', fileValue: undefined, effective: 'dask', overridden: true });
  });

  it('saves the whole inputs mapping and only the settings that change', () => {
    const edits = {
      inputs: { n: { draft: '9' }, stray: { remove: true as const }, m: { draft: '4' } },
      settings: { temp_dir: './t', user_cache_dir: '', outputs_dir: '' },
    };
    const listed = fields(edits);
    const settings = settingFields(block);
    const plan = planEdits(listed, settings, edits);
    expect(saveRequest(listed, settings, plan)).toEqual({
      inputs: { n: 9, m: 4 },
      settings: { temp_dir: './t', user_cache_dir: null },
    });
    expect(saveRequest(fields(), settings, planEdits(fields(), settings, NO_EDITS))).toBeNull();
  });

  it('tries the edited values over the session values it leaves alone', () => {
    const edits = { inputs: { n: { draft: '9' }, stray: { draft: 'y' } }, settings: { temp_dir: './t', executor: '' } };
    const listed = fields(edits);
    const settings = settingFields(block);
    const plan = planEdits(listed, settings, edits);
    expect(sessionRequests(listed, settings, plan, session.inputs.declared, block.overrides)).toEqual({
      inputs: { m: 2, n: 9 },
      config: { executor: null, temp_dir: './t' },
      skipped: ['stray'],
    });
  });

  it('reports values that do not parse', () => {
    const edits = { inputs: { n: { draft: '1.5' } }, settings: { dask_workers: '0', storage_options: '[1]' } };
    const plan = planEdits(fields(edits), settingFields(block), edits);
    expect(plan.errors).toEqual({
      'input:n': 'Enter a whole number.',
      'setting:dask_workers': 'Enter a whole number above 0.',
      'setting:storage_options': 'Enter a JSON object.',
    });
  });
});

describe('the Run Config pane', () => {
  it('names the file in use and the recipes sharing it, and says a save writes the file now', async () => {
    open(configSession());
    renderWithClient(<RunConfigPane />);
    expect(await screen.findByText(CONFIG_PATH)).toBeInTheDocument();
    expect(screen.getByText('as the CLI finds it')).toBeInTheDocument();
    expect(screen.getByTitle('C:/recipes/other.yaml')).toHaveTextContent('other.yaml');
    expect(screen.getByText(/Undo does not take it back/)).toHaveTextContent(
      'The file also applies to 1 other recipe in this folder.',
    );
    expect(screen.getByLabelText('n')).toHaveValue(5);
    expect(screen.getByText('not used by this recipe')).toBeInTheDocument();
    expect(screen.getByLabelText('User cache')).toHaveValue('./u');
    expect(screen.getByLabelText('Executor')).toHaveValue('');
    expect(screen.getByText('session: dask')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Try in this session' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Save to config file' })).toBeDisabled();
  });

  it('tries values in the session with PUT inputs and PUT config, keeping the drafts for a save', async () => {
    const user = userEvent.setup();
    const session = configSession();
    const fetchMock = open(session, {
      [`PUT ${BASE}/inputs`]: session,
      [`PUT ${BASE}/config`]: session,
    });
    renderWithClient(<RunConfigPane />);
    const n = await screen.findByLabelText('n');
    await user.clear(n);
    await user.type(n, '9');
    const cache = screen.getByLabelText('User cache');
    await user.clear(cache);
    await user.type(cache, './x');
    await user.click(screen.getByRole('button', { name: 'Try in this session' }));
    await waitFor(() => expect(bodies(fetchMock, 'PUT', `${BASE}/config`)).toHaveLength(1));
    expect(bodies(fetchMock, 'PUT', `${BASE}/inputs`)).toEqual([{ values: { m: 2, n: 9 } }]);
    expect(bodies(fetchMock, 'PUT', `${BASE}/config`)).toEqual([{ executor: 'dask', user_cache_dir: './x' }]);
    expect(screen.getByLabelText('n')).toHaveValue(9);
    expect(screen.getByRole('button', { name: 'Save to config file' })).toBeEnabled();
  });

  it('saves to the config file with the whole inputs, the changed settings and the version', async () => {
    const user = userEvent.setup();
    const session = configSession();
    const saved = configSession({ version: 1 });
    const fetchMock = open(session, {
      [`POST ${BASE}/config-file/save`]: saveAnswer(saved),
    });
    renderWithClient(<RunConfigPane />);
    const n = await screen.findByLabelText('n');
    await user.clear(n);
    await user.type(n, '9');
    const stray = screen.getByLabelText('stray').closest('.rc-field') as HTMLElement;
    await user.click(within(stray).getByRole('button', { name: 'Remove' }));
    await user.selectOptions(screen.getByLabelText('Add an input'), 'm');
    await user.type(screen.getByLabelText('Temp folder'), './t');
    await user.clear(screen.getByLabelText('User cache'));
    await saveForAll(user);
    await waitFor(() => expect(bodies(fetchMock, 'POST', `${BASE}/config-file/save`)).toHaveLength(1));
    expect(bodies(fetchMock, 'POST', `${BASE}/config-file/save`)).toEqual([
      { inputs: { n: 9, m: 2 }, settings: { user_cache_dir: null, temp_dir: './t' }, scope: 'shared', version: 0 },
    ]);
    // The drafts clear once the file holds them.
    await waitFor(() => expect(screen.getByLabelText('n')).toHaveValue(5));
    expect(screen.queryByLabelText('m')).toBeNull();
  });

  it('asks where the changes to a shared file go, and writes a copy for this recipe alone', async () => {
    const user = userEvent.setup();
    const fetchMock = open(configSession(), {
      [`POST ${BASE}/config-file/save`]: saveAnswer(configSession({ version: 1 })),
    });
    renderWithClient(<RunConfigPane />);
    const n = await screen.findByLabelText('n');
    await user.clear(n);
    await user.type(n, '9');
    await user.click(screen.getByRole('button', { name: 'Save to config file' }));
    const dialog = await screen.findByRole('dialog', { name: 'Save to config file' });
    expect(dialog).toHaveTextContent('Apply these changes to:');
    expect(dialog).toHaveTextContent(
      'Only this recipe writes with_sub.config.yaml beside it as a copy of with_sub.config.yaml with these changes',
    );
    expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus();
    expect(
      within(dialog).getByRole('button', { name: 'All recipes in this folder (1 other uses with_sub.config.yaml)' }),
    ).toBeInTheDocument();
    // A click outside leaves the dialog open.
    fireEvent.mouseDown(dialog.parentElement!);
    expect(screen.getByRole('dialog', { name: 'Save to config file' })).toBe(dialog);
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(bodies(fetchMock, 'POST', `${BASE}/config-file/save`)).toEqual([]);
    const nField = () => screen.getByLabelText('n').closest('.rc-field') as HTMLElement;
    expect(within(nField()).getByText('not saved')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save to config file' }));
    await user.click(await screen.findByRole('button', { name: 'Only this recipe' }));
    await waitFor(() => expect(bodies(fetchMock, 'POST', `${BASE}/config-file/save`)).toHaveLength(1));
    expect(bodies(fetchMock, 'POST', `${BASE}/config-file/save`)).toEqual([
      { inputs: { n: 9, stray: 'x' }, scope: 'recipe', version: 0 },
    ]);
    await waitFor(() => expect(within(nField()).queryByText('not saved')).toBeNull());
  });

  it('marks a typed setting not saved until it matches the file again', async () => {
    const user = userEvent.setup();
    open(configSession());
    renderWithClient(<RunConfigPane />);
    const cache = await screen.findByLabelText('User cache');
    const field = cache.closest('.rc-field') as HTMLElement;
    const before = (cache as HTMLInputElement).value;
    expect(within(field).queryByText('not saved')).toBeNull();
    await user.type(cache, 'x');
    expect(within(field).getByText('not saved')).toBeInTheDocument();
    await user.clear(cache);
    await user.type(cache, before);
    expect(within(field).queryByText('not saved')).toBeNull();
  });

  it('opens the survey warning, without Undo, when a save changes the hash of a survey backed step', async () => {
    const user = userEvent.setup();
    const session = configSession();
    open(session, {
      [`POST ${BASE}/config-file/save`]: saveAnswer(configSession({ version: 1 }), ['seed', 'total']),
    });
    renderWithClient(
      <>
        <RunConfigPane />
        <SurveyWarningDialog sid={SID} graph={session.graph} />
      </>,
      clientWithPreview(),
    );
    const n = await screen.findByLabelText('n');
    await user.clear(n);
    await user.type(n, '9');
    await saveForAll(user);
    await waitFor(() =>
      expect(useEditingStore.getState().surveyWarning).toEqual({
        changedHashes: ['seed', 'total'],
        lost: ['seed'],
        config: true,
      }),
    );
    const dialog = screen.getByRole('dialog', { name: 'Survey backed steps' });
    expect(dialog).toHaveTextContent('The save to the run config file takes 1 survey backed step out of the survey cache');
    expect(within(dialog).queryByRole('button', { name: 'Undo' })).toBeNull();
    expect(within(dialog).getByRole('button', { name: 'Keep change' })).toHaveFocus();
  });

  it('compares a fresh preview when a save changes a cache setting without changing a hash', async () => {
    const user = userEvent.setup();
    const after = makePreview([previewStep('seed'), previewStep('total')]);
    let answerPreview: (value: unknown) => void = () => {};
    const fetchMock = open(configSession(), {
      [`POST ${BASE}/config-file/save`]: saveAnswer(configSession({ version: 1 }), [], true),
      [`GET ${BASE}/preview`]: () =>
        new Promise((resolve) => {
          answerPreview = resolve;
        }),
    });
    const client = clientWithPreview();
    renderWithClient(<RunConfigPane />, client);
    const cache = await screen.findByLabelText('Survey cache');
    await user.type(cache, './elsewhere');
    await saveForAll(user);
    // The save is done before the preview answers.
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === `${BASE}/preview`)).toBe(true));
    await waitFor(() => expect(client.isMutating()).toBe(0));
    expect(useEditingStore.getState().surveyWarning).toBeNull();

    await act(async () => answerPreview(after));
    await waitFor(() =>
      expect(useEditingStore.getState().surveyWarning).toEqual({ changedHashes: [], lost: ['seed'], config: true }),
    );
  });

  it('warns about nothing when the save keeps every survey hit', async () => {
    const user = userEvent.setup();
    const fetchMock = open(configSession(), {
      [`POST ${BASE}/config-file/save`]: saveAnswer(configSession({ version: 1 }), ['total'], true),
      [`GET ${BASE}/preview`]: makePreview([previewStep('seed', { held_by: ['survey'] }), previewStep('total')]),
    });
    const client = clientWithPreview();
    renderWithClient(<RunConfigPane />, client);
    const cache = await screen.findByLabelText('Survey cache');
    await user.type(cache, './elsewhere');
    await saveForAll(user);
    await waitFor(() => expect(client.isMutating()).toBe(0));
    await waitFor(() => expect(screen.getByLabelText('Survey cache')).toHaveValue(''));
    // The fresh preview is compared after the save; it keeps seed in the survey tier.
    await waitFor(() => expect(fetchMock.mock.calls.some(([url]) => String(url) === `${BASE}/preview`)).toBe(true));
    await waitFor(() => expect(client.isFetching()).toBe(0));
    expect(useEditingStore.getState().surveyWarning).toBeNull();
  });

  it('will not write back an input the file holds as NaN, and refuses NaN in storage options', async () => {
    const user = userEvent.setup();
    open(
      configSession({
        run_config: runConfigBlock({
          contents: { inputs: { n: 5, stray: '.nan' }, settings: { storage_options: { token: '.inf' } } },
        }),
      }),
    );
    renderWithClient(<RunConfigPane />);
    expect(await screen.findByLabelText('stray')).toHaveValue('.nan');
    const save = screen.getByRole('button', { name: 'Save to config file' });
    await user.clear(screen.getByLabelText('n'));
    await user.type(screen.getByLabelText('n'), '9');
    expect(save).toBeDisabled();
    expect(screen.getByText(/would turn the NaN or infinity of stray into text/)).toBeInTheDocument();
    const options = screen.getByLabelText('Storage options');
    expect(options).toHaveValue('{\n  "token": ".inf"\n}');
    await user.click(options);
    await user.keyboard('{End} ');
    expect(screen.getByText('NaN and infinity can only be written in the file itself.')).toBeInTheDocument();
  });

  it('refetches the session and says so on a stale version', async () => {
    const user = userEvent.setup();
    const fetchMock = open(configSession(), {
      [`POST ${BASE}/config-file/save`]: apiErrorResponse(409, 'StaleVersion', 'stale', { version: 3 }),
    });
    renderWithClient(<RunConfigPane />);
    const cache = await screen.findByLabelText('User cache');
    await user.clear(cache);
    await user.type(cache, './x');
    await saveForAll(user);
    await waitFor(() => expect(useEditingStore.getState().error).toBe(STALE_VERSION_MESSAGE));
    const sessionGets = () => fetchMock.mock.calls.filter(([url, init]) => String(url) === BASE && !init?.method);
    await waitFor(() => expect(sessionGets().length).toBeGreaterThan(1));
    expect(screen.queryByText('stale')).toBeNull();
    expect(screen.getByLabelText('User cache')).toHaveValue('./x');
  });

  it('chooses a config file, and goes back to finding it as the CLI does', async () => {
    const user = userEvent.setup();
    const chosen = configSession({
      run_config: { ...configSession().run_config, chosen: 'C:/recipes/other.config.yaml' },
    });
    const fetchMock = open(configSession(), { [`PUT ${BASE}/config-file`]: chosen });
    renderWithClient(<RunConfigPane />);
    await user.type(await screen.findByLabelText('Choose Config'), 'other.config.yaml');
    await user.click(screen.getByRole('button', { name: 'Use this file' }));
    await waitFor(() => expect(screen.getByText('chosen for this session')).toBeInTheDocument());
    await user.click(screen.getByRole('button', { name: 'Find it as the CLI does' }));
    await waitFor(() => expect(bodies(fetchMock, 'PUT', `${BASE}/config-file`)).toHaveLength(2));
    expect(bodies(fetchMock, 'PUT', `${BASE}/config-file`)).toEqual([
      { path: 'other.config.yaml', version: 0 },
      { path: null, version: 0 },
    ]);
  });

  it('shows a refused choice', async () => {
    const user = userEvent.setup();
    open(configSession(), {
      [`PUT ${BASE}/config-file`]: apiErrorResponse(403, 'PathNotAllowed', 'Not under a recipe root.'),
    });
    renderWithClient(<RunConfigPane />);
    await user.type(await screen.findByLabelText('Choose Config'), 'C:/elsewhere/x.yaml');
    await user.click(screen.getByRole('button', { name: 'Use this file' }));
    expect(await screen.findByText('Not under a recipe root.')).toBeInTheDocument();
  });

  it('offers to use the file Create Config finds there already', async () => {
    const user = userEvent.setup();
    const none = makeSession();
    const existing = 'C:/recipes/with_sub.config.yaml';
    const fetchMock = open(none, {
      [`POST ${BASE}/config-file`]: apiErrorResponse(409, 'TargetExists', 'with_sub.config.yaml exists already.', {
        paths: [existing],
      }),
      [`PUT ${BASE}/config-file`]: configSession(),
    });
    renderWithClient(<RunConfigPane />);
    expect(await screen.findByText(/No run config file is found/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create Config' }));
    expect(await screen.findByText(/with_sub.config.yaml exists already/)).toBeInTheDocument();
    expect(bodies(fetchMock, 'POST', `${BASE}/config-file`)).toEqual([{ version: 0 }]);
    await user.click(screen.getByRole('button', { name: 'Use it' }));
    await waitFor(() => expect(bodies(fetchMock, 'PUT', `${BASE}/config-file`)).toHaveLength(1));
    expect(bodies(fetchMock, 'PUT', `${BASE}/config-file`)).toEqual([{ path: existing, version: 0 }]);
    expect(await screen.findByText(CONFIG_PATH)).toBeInTheDocument();
  });

  it('creates a config for a copied recipe and fills its required input (UC-13)', async () => {
    const user = userEvent.setup();
    const n = { ...makeSession().inputs.declared[0], required: true, source: 'missing' as const, value: null };
    const none = makeSession({ inputs: { declared: [n], values: {} } });
    const created = makeSession({
      version: 1,
      inputs: { declared: [n], values: {} },
      run_config: runConfigBlock({ contents: { inputs: { n: null }, settings: { user_cache_dir: null } } }),
    });
    const filled = makeSession({
      version: 2,
      inputs: { declared: [{ ...n, source: 'config', value: 4 }], values: { n: 4 } },
      run_config: runConfigBlock({ contents: { inputs: { n: 4 }, settings: { user_cache_dir: './c' } } }),
    });
    const fetchMock = open(none, {
      [`POST ${BASE}/config-file`]: created,
      [`POST ${BASE}/config-file/save`]: saveAnswer(filled, null),
    });
    renderWithClient(<RunConfigPane />);
    await user.click(await screen.findByRole('button', { name: 'Create Config' }));
    const input = await screen.findByLabelText('n');
    expect(input).toHaveValue(null);
    expect(within(input.closest('.rc-field') as HTMLElement).getByText('required')).toBeInTheDocument();
    await user.type(input, '4');
    await user.type(screen.getByLabelText('User cache'), './c');
    await user.click(screen.getByRole('button', { name: 'Save to config file' }));
    await waitFor(() => expect(bodies(fetchMock, 'POST', `${BASE}/config-file/save`)).toHaveLength(1));
    // The file is the recipe's own, so there is nothing to ask.
    expect(screen.queryByRole('dialog')).toBeNull();
    expect(bodies(fetchMock, 'POST', `${BASE}/config-file/save`)).toEqual([
      { inputs: { n: 4 }, settings: { user_cache_dir: './c' }, scope: 'shared', version: 1 },
    ]);
    await waitFor(() => expect(screen.getByLabelText('n')).toHaveValue(4));
    const field = screen.getByLabelText('n').closest('.rc-field') as HTMLElement;
    expect(within(field).getByText('run config')).toBeInTheDocument();
  });
});
