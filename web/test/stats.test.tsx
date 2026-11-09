import { act, screen, waitFor, within } from '@testing-library/react';
import { QueryClient } from '@tanstack/react-query';
import userEvent from '@testing-library/user-event';
import type { DockviewApi, IDockviewPanelProps } from 'dockview-react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../src/api/client';
import type { Graph, StepNode } from '../src/api/graph';
import type { Artifact, StepArtifacts } from '../src/api/models';
import { queryKeys } from '../src/api/queries';
import { Inspector } from '../src/inspector/Inspector';
import { NO_RUN_CONTROLS, RunControlsContext, type RunControls } from '../src/runs/useRunControls';
import { useDock } from '../src/shell/dock';
import { PANE_COMPONENTS } from '../src/shell/DockLayout';
import { useSessionStore } from '../src/state/session';
import { useView } from '../src/state/view';
import { barLayout, barPath, barTitle, GroupedBars } from '../src/stats/GroupedBars';
import { groupColor } from '../src/stats/palette';
import { chooseOutput, errorText, jsonOutputs, paneMessage, paneState, sizeText } from '../src/stats/paneState';
import { linearScale, niceStep, niceTicks, yAxis } from '../src/stats/scale';
import { StatsPane } from '../src/stats/StatsPane';
import { StatsView } from '../src/stats/StatsView';
import { useStatsStore } from '../src/stats/store';
import { groupsOf, isNoise, maxLabel, parseTable, seriesFor, type Table } from '../src/stats/table';
// The table of tests/_stage9_helpers.py stats_table(), dumped with json.dumps(indent=1),
// so the core, server and client tests read one table.
import tableFixture from './fixtures/table.json';
import { apiErrorResponse, makeSession, mockApi, renderWithClient, testQueryClient, withSubGraph } from './helpers';

const SID = '20261013-153012-a1b2c3';
const ARTIFACTS = `/api/sessions/${SID}/steps/total/artifacts?resolve=false`;
const JSON_URL = (output: string) => `/api/sessions/${SID}/steps/total/artifacts/${output}/json`;

function fixtureTable(data: unknown = tableFixture): Table {
  const parsed = parseTable(structuredClone(data));
  if (!parsed.ok) throw new Error(parsed.reason);
  return parsed.table;
}

/** with_sub with dict outputs on `total` and on the mapped `leaf_add`. */
function dictGraph(): Graph {
  const graph = structuredClone(withSubGraph);
  for (const id of ['total', 'leaf_add']) {
    const step = graph.nodes.find((node) => node.id === id) as StepNode;
    step.outputs[0].type = 'dict';
  }
  return graph;
}

function artifact(output: string, format: string, instance: number | null = null): Artifact {
  return {
    output,
    url: `C:/cache/total/abcd1234/run/${format}/${output}.${format}`,
    format,
    tier: 'user',
    step_hash: 'abcd1234',
    instance_index: instance,
    item: null,
    created_at: '2026-11-03T10:00:00+00:00',
    run_id: 'run',
    size_bytes: null,
  };
}

function stepArtifacts(artifacts: Artifact[], note: string | null = null): StepArtifacts {
  return {
    step_id: 'total',
    step_hash: 'abcd1234',
    fanned_out: false,
    held_by: artifacts.length > 0 ? ['user'] : [],
    instances: null,
    found: artifacts.length,
    marker: false,
    note,
    artifacts,
    echogram: { available: false, reason: null, resolution: null },
  };
}

function jsonAnswer(output: string, data: unknown, hash = 'abcd1234') {
  return { step_id: 'total', step_hash: hash, output, tier: 'user', data };
}

function urls(fetchMock: ReturnType<typeof mockApi>): string[] {
  return fetchMock.mock.calls.map(([url]) => String(url));
}

beforeEach(() => {
  useSessionStore.getState().setSid(null);
  useView.getState().resetScope();
  useStatsStore.getState().choose(null);
});

describe('parseTable', () => {
  it('reads the fixture table', () => {
    const table = fixtureTable();
    expect(table.title).toBe('Cluster statistics');
    expect(table.groupColumn).toBe('group');
    expect(table.features.map((feature) => feature.label)).toEqual(['18 kHz', '38 kHz', '38 kHz - 18 kHz']);
  });

  it('refuses what is not a table, saying why', () => {
    const reason = (data: unknown) => {
      const parsed = parseTable(data);
      return parsed.ok ? null : parsed.reason;
    };
    expect(reason(8)).toBe('a number, not a table');
    expect(reason([1])).toBe('a list, not a table');
    expect(reason({})).toBe('not an aa-table/1 table (schema missing)');
    expect(reason({ schema: 'aa-table/2' })).toBe('not an aa-table/1 table (schema "aa-table/2")');
    expect(reason({ ...tableFixture, rows: {} })).toBe('rows is not a list');
    expect(reason({ ...tableFixture, group_column: 'nope' })).toBe('group_column names no column');
    expect(reason({ ...tableFixture, extra: 1 })).toBeNull();
  });
});

describe('table series', () => {
  it('lists the groups with noise last', () => {
    const groups = groupsOf(fixtureTable());
    expect(groups.map((group) => group.label)).toEqual(['Cluster 0', 'Cluster 1', 'Noise']);
    expect(groups.map((group) => group.id)).toEqual([0, 1, -1]);
    expect(groups[0]).toMatchObject({ n: 1234, percentage: 41.2 });
    expect(groups.map(isNoise)).toEqual([false, false, true]);
    expect(maxLabel(fixtureTable())).toBe(1);
  });

  it('makes one series per kind, Sv first, with bars from the tagged columns', () => {
    const [sv, difference, ...rest] = seriesFor(fixtureTable());
    expect(rest).toEqual([]);
    expect(sv).toMatchObject({ kind: 'sv', label: 'Sv', unit: 'dB' });
    expect(sv.features.map((feature) => feature.name)).toEqual(['18kHz', '38kHz']);
    expect(sv.bars[0][0]).toEqual({ mean: -71.3, std: 4.1 });
    expect(sv.bars[2][1]).toEqual({ mean: -86.1, std: 7.4 });
    expect(difference).toMatchObject({ kind: 'difference', label: 'Sv difference', unit: 'dB' });
    expect(difference.bars[0][0]).toEqual({ mean: 2.4, std: 1.8 });
    // Noise's difference SD is null in the table.
    expect(difference.bars[2][0]).toEqual({ mean: 2.3, std: null });
  });

  it('leaves a gap where a mean is missing', () => {
    const data = structuredClone(tableFixture) as typeof tableFixture;
    (data.rows[1] as Record<string, unknown>)['38kHz_mean'] = null;
    const [sv] = seriesFor(fixtureTable(data));
    expect(sv.bars[1][1]).toBeNull();
    expect(sv.bars[1][0]).not.toBeNull();
  });
});

describe('groupColor', () => {
  it("matches the echogram labels layer's palette", () => {
    expect(groupColor(0, 1)).toBe('rgb(0, 243, 252)');
    expect(groupColor(1, 1)).toBe('rgb(53, 226, 0)');
    expect(groupColor(-1, 1)).toBe('rgb(0, 0, 0)');
    // Label 11 is the first generated colour, hsv(0, 0.9, 0.8).
    const byte = (value: number) => Math.floor(value * 255);
    expect(groupColor(11, 11)).toBe(`rgb(${byte(0.8)}, ${byte(0.8 * 0.1)}, ${byte(0.8 * 0.1)})`);
  });

  it('gives a label the same colour whatever the largest label', () => {
    for (let label = -1; label < 15; label += 1) {
      for (const highest of [label + 3, 63]) {
        expect(groupColor(label, Math.max(label, 0))).toBe(groupColor(label, highest));
      }
    }
  });
});

describe('scale', () => {
  it('picks nice steps and ticks', () => {
    expect(niceStep(10, 5)).toBe(2);
    expect(niceStep(32.3, 4)).toBe(10);
    expect(niceTicks(0, 10, 5)).toEqual([0, 2, 4, 6, 8, 10]);
    expect(niceTicks(-96.8, -63.8, 4)).toEqual([-90, -80, -70]);
    expect(niceTicks(0.1, 0.35, 5)).toEqual([0.1, 0.15, 0.2, 0.25, 0.3, 0.35]);
    expect(linearScale([0, 10], [100, 0])(5)).toBe(50);
  });

  it('grows Sv bars from the padded floor and differences from zero', () => {
    const [sv, difference] = seriesFor(fixtureTable());
    const svAxis = yAxis(sv);
    expect(svAxis.domain[0]).toBeCloseTo(-96.8);
    expect(svAxis.domain[1]).toBeCloseTo(-63.8);
    expect(svAxis.baseline).toBe(svAxis.domain[0]);
    const differenceAxis = yAxis(difference);
    expect(differenceAxis.domain[0]).toBe(0);
    expect(differenceAxis.domain[1]).toBeCloseTo(5.12);
    expect(differenceAxis.baseline).toBe(0);
  });
});

describe('GroupedBars', () => {
  const colorOf = () => 'rgb(1, 2, 3)';

  it('lays out bars at most 24px and widens a chart its labels do not fit', () => {
    const features = fixtureTable().features;
    expect(barLayout(3, features, 520)).toEqual({ bar: 24, band: 150, width: 520 });
    const many = Array.from({ length: 10 }, (_, i) => ({ name: `f${i}`, label: `${i} kHz - 18 kHz`, kind: 'sv' }));
    const narrow = barLayout(5, many, 200);
    expect(narrow.bar).toBe(4);
    expect(narrow.width).toBeGreaterThan(200);
  });

  it('rounds the data end of a bar and keeps the baseline square', () => {
    expect(barPath(0, 10, 100, 50)).toBe('M0,100V54A4,4 0 0 1 4,50H6A4,4 0 0 1 10,54V100Z');
    expect(barPath(0, 10, 50, 100)).toBe('M0,50V96A4,4 0 0 0 4,100H6A4,4 0 0 0 10,96V50Z');
    expect(barPath(0, 10, 100, 98)).toBe('M0,100V100A2,2 0 0 1 2,98H8A2,2 0 0 1 10,100V100Z');
  });

  it('draws one bar per group and feature, noise outlined, each with its values', () => {
    const [sv] = seriesFor(fixtureTable());
    renderWithClient(<GroupedBars series={sv} colorOf={colorOf} />);
    expect(document.querySelectorAll('.rc-stats-bar')).toHaveLength(6);
    expect(document.querySelectorAll('.rc-stats-noise')).toHaveLength(2);
    const titles = [...document.querySelectorAll('.rc-stats-mark > title')].map((title) => title.textContent);
    expect(titles).toHaveLength(6);
    for (const title of titles) expect(title).toMatch(/mean .*, SD /);
    expect(titles[0]).toBe('Cluster 0, 18 kHz: mean \u221271.3 dB, SD 4.1 dB, n 1,234 (41.2 %)');
    expect(screen.getAllByText('18 kHz')).toHaveLength(1);
    expect(screen.getByText('Sv (dB)')).toBeInTheDocument();
  });

  it('draws difference bars from a baseline at zero', () => {
    const [, difference] = seriesFor(fixtureTable());
    renderWithClient(<GroupedBars series={difference} colorOf={colorOf} />);
    const { domain } = yAxis(difference);
    const zero = linearScale(domain, [190, 8])(0);
    const baseline = document.querySelector('.rc-stats-baseline')!;
    expect(Number(baseline.getAttribute('y1'))).toBe(zero);
    for (const bar of document.querySelectorAll('.rc-stats-bar')) {
      expect(bar.getAttribute('d')).toMatch(new RegExp(`^M[\\d.]+,${zero}V`));
    }
    // Noise's difference has no SD, so no error bar.
    expect(document.querySelectorAll('.rc-stats-err')).toHaveLength(2);
    expect(barTitle(difference.groups[2], difference.features[0], difference.bars[2][0], 'dB')).toBe(
      'Noise, 38 kHz - 18 kHz: mean 2.3 dB, no SD, n 300 (10.0 %)',
    );
  });

  it('leaves a missing mean empty and says so', () => {
    const data = structuredClone(tableFixture) as typeof tableFixture;
    (data.rows[1] as Record<string, unknown>)['38kHz_mean'] = null;
    const [sv] = seriesFor(fixtureTable(data));
    renderWithClient(<GroupedBars series={sv} colorOf={colorOf} />);
    expect(document.querySelectorAll('.rc-stats-bar')).toHaveLength(5);
    expect(document.querySelectorAll('.rc-stats-mark')).toHaveLength(6);
    expect(screen.getByText('Cluster 1, 38 kHz: no value')).toBeInTheDocument();
  });

  it('gives the values in each bar title only, with no readout line', () => {
    const [sv] = seriesFor(fixtureTable());
    renderWithClient(<GroupedBars series={sv} colorOf={colorOf} />);
    expect(document.querySelector('.rc-stats-readout')).toBeNull();
    expect(document.querySelector('.rc-stats-mark[tabindex]')).toBeNull();
  });
});

describe('StatsView', () => {
  it('shows the title, counts, legend and a chart per kind', () => {
    renderWithClient(<StatsView table={fixtureTable()} stepId="stats" output="table" />);
    expect(screen.getByText('Cluster statistics')).toBeInTheDocument();
    expect(document.querySelector('.rc-stats-summary')).toHaveTextContent('stats.table, 2,995 samples in 2 clusters');
    const counts = document.querySelector('.rc-stats-counts') as HTMLElement;
    const first = within(counts).getByText('Cluster 0').closest('tr')!;
    expect(first).toHaveTextContent('1,234');
    expect(first).toHaveTextContent('41.2 %');
    expect(within(counts).getByText('Noise').closest('tr')).toHaveTextContent('10.0 %');
    const legend = screen.getByRole('list', { name: 'Groups' });
    expect(within(legend).getAllByRole('listitem')).toHaveLength(3);
    expect(legend.querySelectorAll('.rc-stats-swatch-noise')).toHaveLength(1);
    const charts = document.querySelectorAll('.rc-stats-chart');
    expect([...charts].map((chart) => chart.querySelector('h3')?.textContent)).toEqual(['Sv', 'Sv difference']);
    const values = charts[1].querySelector('.rc-stats-values') as HTMLElement;
    expect(within(values).getByText('2.4 ± 1.8')).toBeInTheDocument();
    expect(within(values).getByText('2.3 ± none')).toBeInTheDocument();
  });

  it('says when the table has no rows', () => {
    renderWithClient(<StatsView table={fixtureTable({ ...tableFixture, rows: [] })} stepId="stats" output="table" />);
    expect(screen.getByText('The table has no rows.')).toBeInTheDocument();
    expect(document.querySelector('.rc-stats-chart')).toBeNull();
  });
});

describe('pane state', () => {
  it('lists the saved json outputs of a plain step', () => {
    const found = stepArtifacts([
      artifact('table', 'json'),
      artifact('ds', 'zarr'),
      artifact('summary', 'json'),
      artifact('each', 'json', 0),
    ]);
    expect(jsonOutputs(found)).toEqual(['table', 'summary']);
    expect(jsonOutputs(undefined)).toEqual([]);
  });

  it('takes the chosen output when it is saved for the step', () => {
    const outputs = ['table', 'summary'];
    expect(chooseOutput('total', outputs, null)).toBe('table');
    expect(chooseOutput('total', outputs, { step: 'total', output: 'summary' })).toBe('summary');
    expect(chooseOutput('total', outputs, { step: 'other', output: 'summary' })).toBe('table');
    expect(chooseOutput('total', outputs, { step: 'total', output: 'gone' })).toBe('table');
    expect(chooseOutput('total', [], null)).toBeNull();
  });

  it('looks again rather than show a table from an older hash', () => {
    const step = dictGraph().nodes.find((node) => node.id === 'total') as StepNode;
    const inputs = {
      sid: SID,
      step,
      choice: null,
      artifacts: stepArtifacts([artifact('total', 'json')]),
      artifactsError: null,
      output: 'total',
      json: jsonAnswer('total', tableFixture),
      jsonError: null,
    };
    expect(paneState(inputs).kind).toBe('table');
    expect(paneState({ ...inputs, json: { ...inputs.json, step_hash: 'old00000' } })).toEqual({
      kind: 'looking',
      step: 'total',
    });
  });

  it('puts the lookup note in brackets', () => {
    expect(paneMessage({ kind: 'unsaved', step: 'total', note: 'no cache configured' })).toBe(
      'total has no saved table for the current settings (no cache configured). Run to total to compute it.',
    );
  });

  it('says a too large table by its limit, without the server path', () => {
    const tooLarge = (limit: unknown) =>
      new ApiError(413, 'ArtifactTooLarge', 'total.total at C:/x.json is larger than 1 bytes', { limit });
    const where =
      "Raise max_artifact_json_bytes in server.yaml in the client's state folder (~/.aa-recipe/client by default).";
    expect(errorText(tooLarge(1 << 20), 'total', 'total')).toBe(
      `total.total is larger than the 1 MB the server reads. ${where}`,
    );
    expect(errorText(tooLarge(512 * 1024), 'total', 'table')).toBe(
      `total.table is larger than the 512 KB the server reads. ${where}`,
    );
    expect(errorText(tooLarge(null), 'total', 'total')).toBe(
      `total.total is larger than what the server reads. ${where}`,
    );
    expect([sizeText(16), sizeText(1536), sizeText(5 * 1024 * 1024 + 1)]).toEqual(['16 bytes', '1.5 KB', '5 MB']);
  });
});

describe('StatsPane', () => {
  function open(
    routes: Record<string, unknown>,
    step: string | null = 'total',
    client = testQueryClient(),
    controls: RunControls = NO_RUN_CONTROLS,
  ) {
    const fetchMock = mockApi({ [`GET /api/sessions/${SID}`]: makeSession({ graph: dictGraph() }), ...routes });
    useSessionStore.getState().setSid(SID);
    if (step) useView.getState().select({ kind: 'step', id: step });
    renderWithClient(
      <RunControlsContext.Provider value={controls}>
        <StatsPane />
      </RunControlsContext.Provider>,
      client,
    );
    return fetchMock;
  }

  it('asks for a recipe without a session', () => {
    mockApi({});
    renderWithClient(<StatsPane />);
    expect(
      screen.getByText('Open a recipe, then select a step with a statistics table output.'),
    ).toBeInTheDocument();
  });

  it('asks for a step when nothing is selected', async () => {
    open({}, null);
    expect(
      await screen.findByText(/^Select a step with a statistics table output, such as cluster_statistics,/),
    ).toBeInTheDocument();
  });

  it('says a step without a dict output has no table, asking nothing', async () => {
    const fetchMock = open({}, 'seed');
    expect(
      await screen.findByText(
        'seed has no statistics table output. Select a step with a statistics table output, such as cluster_statistics.',
      ),
    ).toBeInTheDocument();
    expect(urls(fetchMock).some((url) => url.includes('/artifacts'))).toBe(false);
  });

  it('shows plain steps only', async () => {
    const fetchMock = open({}, 'leaf_add');
    expect(await screen.findByText(/^leaf_add runs once per item/)).toBeInTheDocument();
    expect(urls(fetchMock).some((url) => url.includes('/artifacts'))).toBe(false);
  });

  it('says a mapped step without a dict output has no table, not that it runs per item', async () => {
    open({}, 'chain_inc');
    expect(await screen.findByText(/^chain_inc has no statistics table output\./)).toBeInTheDocument();
    expect(screen.queryByText(/runs once per item/)).toBeNull();
  });

  it('says when nothing is saved, without reading json', async () => {
    const fetchMock = open({ [`GET ${ARTIFACTS}`]: stepArtifacts([]) });
    expect(
      await screen.findByText('total has no saved table for the current settings. Run to total to compute it.'),
    ).toBeInTheDocument();
    expect(urls(fetchMock).some((url) => url.includes('/json'))).toBe(false);
  });

  it('offers to run to the step when nothing is saved', async () => {
    const user = userEvent.setup();
    const runToHere = vi.fn();
    open({ [`GET ${ARTIFACTS}`]: stepArtifacts([]) }, 'total', testQueryClient(), {
      ...NO_RUN_CONTROLS,
      canRun: true,
      runToHere,
    });
    const button = await screen.findByRole('button', { name: 'Run to total' });
    expect(button).toBeEnabled();
    expect(button).not.toHaveAttribute('title');
    await user.click(button);
    expect(runToHere).toHaveBeenCalledWith('total');
  });

  it('disables the run offer while a run is going, saying why', async () => {
    open({ [`GET ${ARTIFACTS}`]: stepArtifacts([]) }, 'total', testQueryClient(), {
      ...NO_RUN_CONTROLS,
      isActive: true,
    });
    const button = await screen.findByRole('button', { name: 'Run to total' });
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute('title', 'A run is going');
  });

  it('offers no run while it shows a table', async () => {
    open({
      [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json')]),
      [`GET ${JSON_URL('total')}`]: jsonAnswer('total', tableFixture),
    });
    expect(await screen.findByText('Cluster statistics')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Run to total' })).toBeNull();
  });

  it('reads the saved json output and draws the table', async () => {
    const fetchMock = open({
      [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json')]),
      [`GET ${JSON_URL('total')}`]: jsonAnswer('total', tableFixture),
    });
    expect(await screen.findByText('Cluster statistics')).toBeInTheDocument();
    expect(urls(fetchMock)).toContain(JSON_URL('total'));
    expect(document.querySelectorAll('.rc-stats-chart')).toHaveLength(2);
    expect(screen.queryByRole('combobox')).toBeNull();
  });

  it('says where to raise the limit when the table is too large, without retrying', async () => {
    // The app retries failed queries by default; the json query must not retry a 413.
    const client = new QueryClient({ defaultOptions: { queries: { retry: 3, retryDelay: 0 } } });
    const fetchMock = open(
      {
        [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json')]),
        [`GET ${JSON_URL('total')}`]: () =>
          apiErrorResponse(413, 'ArtifactTooLarge', 'total.total at C:/x.json is larger than 1048576 bytes', {
            limit: 1 << 20,
          }),
      },
      'total',
      client,
    );
    expect(
      await screen.findByText(
        'total.total is larger than the 1 MB the server reads. Raise max_artifact_json_bytes in server.yaml ' +
          "in the client's state folder (~/.aa-recipe/client by default).",
      ),
    ).toBeInTheDocument();
    expect(urls(fetchMock).filter((url) => url === JSON_URL('total'))).toHaveLength(1);
  });

  it('shows the server message when the artifact is gone', async () => {
    open({
      [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json')]),
      [`GET ${JSON_URL('total')}`]: () =>
        apiErrorResponse(404, 'ArtifactNotFound', "total has no saved 'total' at its current hash"),
    });
    expect(await screen.findByText("total has no saved 'total' at its current hash")).toBeInTheDocument();
  });

  it('says when json is not a table', async () => {
    open({
      [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json')]),
      [`GET ${JSON_URL('total')}`]: jsonAnswer('total', 8),
    });
    expect(await screen.findByText('total.total is JSON but not a table: a number, not a table.')).toBeInTheDocument();
  });

  it('draws the table once a finished run refreshes the saved artifacts', async () => {
    let saved = false;
    const client = testQueryClient();
    open(
      {
        [`GET ${ARTIFACTS}`]: () => stepArtifacts(saved ? [artifact('total', 'json')] : []),
        [`GET ${JSON_URL('total')}`]: jsonAnswer('total', tableFixture),
      },
      'total',
      client,
    );
    expect(await screen.findByText(/^total has no saved table/)).toBeInTheDocument();
    saved = true;
    // What useRunEvents invalidates when a run finishes.
    await act(async () => {
      await client.invalidateQueries({ queryKey: queryKeys.allArtifacts(SID) });
      await client.invalidateQueries({ queryKey: queryKeys.allArtifactJson(SID) });
    });
    expect(await screen.findByText('Cluster statistics')).toBeInTheDocument();
  });

  it('never shows a missing table message from before an edit once a run saves the table', async () => {
    // The server: total's hash and whether a table is saved at it.
    const server = { hash: 'abcd1234', saved: true };
    let answerJson: () => void = () => {};
    const client = testQueryClient();
    const fetchMock = open(
      {
        [`GET ${ARTIFACTS}`]: () => ({
          ...stepArtifacts(server.saved ? [{ ...artifact('total', 'json'), step_hash: server.hash }] : []),
          step_hash: server.hash,
        }),
        [`GET ${JSON_URL('total')}`]: () => {
          if (!server.saved) {
            return apiErrorResponse(404, 'ArtifactNotFound', "total has no saved 'total' at its current hash");
          }
          // The answer waits, as a read from GCS does, until the test lets it go.
          return new Promise((resolve) => {
            answerJson = () => resolve(jsonAnswer('total', tableFixture, server.hash));
          });
        },
      },
      'total',
      client,
    );
    const reads = () => urls(fetchMock).filter((url) => url === JSON_URL('total')).length;
    await waitFor(() => expect(reads()).toBe(1));
    act(() => answerJson());
    expect(await screen.findByText('Cluster statistics')).toBeInTheDocument();
    const shown: string[] = [];
    const observer = new MutationObserver(() => shown.push(document.body.textContent ?? ''));
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });

    // An edit changes total's hash; what takeSession invalidates. The table is not read again.
    server.hash = 'beef5678';
    server.saved = false;
    await act(() => client.invalidateQueries({ queryKey: queryKeys.allArtifacts(SID) }));
    expect(await screen.findByText(/^total has no saved table for the current settings/)).toBeInTheDocument();
    expect(reads()).toBe(1);

    // A run saves the table at the new hash; what useRunEvents invalidates.
    server.saved = true;
    await act(async () => {
      await client.invalidateQueries({ queryKey: queryKeys.allArtifacts(SID) });
      await client.invalidateQueries({ queryKey: queryKeys.allArtifactJson(SID) });
    });
    await waitFor(() => expect(reads()).toBe(2));
    expect(screen.getByText('Looking up total...')).toBeInTheDocument();
    act(() => answerJson());
    expect(await screen.findByText('Cluster statistics')).toBeInTheDocument();
    observer.disconnect();
    expect(shown.some((text) => text.includes("has no saved 'total'"))).toBe(false);
  });

  it('reads a chosen json output of a step without a dict output, saying it is not a table', async () => {
    const seedArtifacts = `/api/sessions/${SID}/steps/seed/artifacts?resolve=false`;
    const seedJson = `/api/sessions/${SID}/steps/seed/artifacts/value/json`;
    useStatsStore.getState().choose({ step: 'seed', output: 'value' });
    const fetchMock = open(
      {
        [`GET ${seedArtifacts}`]: { ...stepArtifacts([artifact('value', 'json')]), step_id: 'seed' },
        [`GET ${seedJson}`]: { ...jsonAnswer('value', [1, 2]), step_id: 'seed' },
      },
      'seed',
    );
    expect(await screen.findByText(/^seed\.value is JSON but not a table: /)).toBeInTheDocument();
    expect(urls(fetchMock)).toContain(seedJson);
  });

  it('asks for nothing while the pane is a hidden tab, and reads once shown', async () => {
    const listeners: ((event: { isVisible: boolean }) => void)[] = [];
    const api = {
      isVisible: false,
      onDidVisibilityChange: (listener: (event: { isVisible: boolean }) => void) => {
        listeners.push(listener);
        return { dispose: () => listeners.splice(listeners.indexOf(listener), 1) };
      },
    };
    const fetchMock = mockApi({
      [`GET /api/sessions/${SID}`]: makeSession({ graph: dictGraph() }),
      [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json')]),
      [`GET ${JSON_URL('total')}`]: jsonAnswer('total', tableFixture),
    });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'total' });
    const Pane = PANE_COMPONENTS.stats;
    renderWithClient(<Pane {...({ api } as unknown as IDockviewPanelProps)} />);
    expect(await screen.findByText('Looking up total...')).toBeInTheDocument();
    await waitFor(() => expect(urls(fetchMock)).toContain(`/api/sessions/${SID}`));
    expect(urls(fetchMock).some((url) => url.includes('/artifacts'))).toBe(false);

    act(() => {
      api.isVisible = true;
      for (const listener of [...listeners]) listener({ isVisible: true });
    });
    expect(await screen.findByText('Cluster statistics')).toBeInTheDocument();
    expect(urls(fetchMock)).toContain(ARTIFACTS);
  });

  it('offers a choice of json outputs and follows the store', async () => {
    const user = userEvent.setup();
    const summary = { ...tableFixture, title: 'Summary table' };
    useStatsStore.getState().choose({ step: 'total', output: 'summary' });
    open({
      [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json'), artifact('summary', 'json')]),
      [`GET ${JSON_URL('total')}`]: jsonAnswer('total', tableFixture),
      [`GET ${JSON_URL('summary')}`]: jsonAnswer('summary', summary),
    });
    expect(await screen.findByText('Summary table')).toBeInTheDocument();
    const select = screen.getByRole('combobox');
    expect(select).toHaveValue('summary');
    await user.selectOptions(select, 'total');
    expect(await screen.findByText('Cluster statistics')).toBeInTheDocument();
    expect(useStatsStore.getState().choice).toEqual({ step: 'total', output: 'total' });
  });
});

describe('Show table', () => {
  afterEach(() => {
    useDock.setState({ api: null });
  });

  it('opens the statistics pane on a json output from Saved files', async () => {
    const user = userEvent.setup();
    mockApi({
      [`GET /api/sessions/${SID}`]: makeSession({ graph: dictGraph() }),
      [`GET ${ARTIFACTS}`]: stepArtifacts([artifact('total', 'json'), artifact('ds', 'zarr')]),
    });
    const setActive = vi.fn();
    const getPanel = vi.fn(() => ({ api: { setActive } }));
    useDock.setState({ api: { getPanel } as unknown as DockviewApi });
    useSessionStore.getState().setSid(SID);
    useView.getState().select({ kind: 'step', id: 'total' });
    renderWithClient(<Inspector />);

    await user.click(await screen.findByText('Saved files'));
    const buttons = await screen.findAllByRole('button', { name: 'Show table' });
    // The zarr row has none.
    expect(buttons).toHaveLength(1);
    expect(screen.getByText('ds').closest('tr')!.querySelector('button')).toBeNull();
    await user.click(buttons[0]);
    expect(useStatsStore.getState().choice).toEqual({ step: 'total', output: 'total' });
    expect(getPanel).toHaveBeenCalledWith('stats');
    expect(setActive).toHaveBeenCalledTimes(1);
  });
});
