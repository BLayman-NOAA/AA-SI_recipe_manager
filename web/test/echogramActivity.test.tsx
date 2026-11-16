import { act, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { queryKeys } from '../src/api/queries';
import {
  CHECKING_TEXT,
  EchogramActivity,
  LOADING_TEXT,
  type TileCounter,
} from '../src/echogram/EchogramActivity';
import { useEchogramStore } from '../src/echogram/store';
import { useSessionStore } from '../src/state/session';
import { renderWithClient, testQueryClient } from './helpers';

const SID = '20261013-153012-a1b2c3';

/** A stand in for the panel's plot element and a panel whose tile count the test sets. */
function setup() {
  const plot = document.createElement('div');
  document.body.append(plot);
  const tiles = { loading: 0, uploading: 0 };
  const panel: TileCounter = { view: { info: { tiles } } };
  return { plot, panel, tiles };
}

beforeEach(() => {
  useSessionStore.getState().setSid(SID);
  useEchogramStore.setState({ busy: null });
});

afterEach(() => {
  useSessionStore.getState().setSid(null);
  document.body.innerHTML = '';
});

describe('the echogram activity notice', () => {
  it('says the caches are being checked while the open session previews, then goes', async () => {
    const { plot, panel } = setup();
    const client = testQueryClient();
    let answer: (value: unknown) => void = () => undefined;
    void client.fetchQuery({
      queryKey: [...queryKeys.previews(SID), 'all'],
      queryFn: () => new Promise((resolve) => (answer = resolve)),
    });
    renderWithClient(<EchogramActivity plot={plot} panel={panel} />, client);

    expect(await screen.findByText(CHECKING_TEXT)).toBeInTheDocument();
    expect(plot.querySelector('.rc-echogram-activity')).not.toBeNull();
    expect(screen.queryByText(LOADING_TEXT)).toBeNull();

    await act(async () => answer({ steps: [] }));
    await waitFor(() => expect(screen.queryByText(CHECKING_TEXT)).toBeNull());
  });

  it('ignores another session previewing', async () => {
    const { plot, panel } = setup();
    const client = testQueryClient();
    void client.fetchQuery({
      queryKey: [...queryKeys.previews('another'), 'all'],
      queryFn: () => new Promise(() => undefined),
    });
    renderWithClient(<EchogramActivity plot={plot} panel={panel} />, client);
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(screen.queryByText(CHECKING_TEXT)).toBeNull();
  });

  it('says data is loading while the panel has tiles on their way', async () => {
    const { plot, panel, tiles } = setup();
    tiles.loading = 3;
    renderWithClient(<EchogramActivity plot={plot} panel={panel} />);

    expect(await screen.findByText(LOADING_TEXT)).toBeInTheDocument();
    tiles.loading = 0;
    await waitFor(() => expect(screen.queryByText(LOADING_TEXT)).toBeNull());
  });

  it('says data is loading while a layer is looked up', async () => {
    const { plot } = setup();
    useEchogramStore.setState({ busy: 'survey_echogram_store' });
    renderWithClient(<EchogramActivity plot={plot} panel={null} />);
    expect(await screen.findByText(LOADING_TEXT)).toBeInTheDocument();
  });

  it('shows nothing when idle', async () => {
    const { plot, panel } = setup();
    renderWithClient(<EchogramActivity plot={plot} panel={panel} />);
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(plot.querySelector('.rc-echogram-activity')).toBeNull();
  });

  it('shows nothing before the panel exists', async () => {
    useEchogramStore.setState({ busy: 'survey_echogram_store' });
    renderWithClient(<EchogramActivity plot={null} panel={null} />);
    await new Promise((resolve) => setTimeout(resolve, 600));
    expect(document.querySelector('.rc-echogram-activity')).toBeNull();
  });
});
