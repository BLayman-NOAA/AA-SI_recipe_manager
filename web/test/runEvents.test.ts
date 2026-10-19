import { QueryClientProvider } from '@tanstack/react-query';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createElement, type ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { parseRunEvents } from '../src/runs/events';
import { useRunStore } from '../src/runs/store';
import { useFollowRuns, useRunEvents } from '../src/runs/useRunEvents';
import { mockApi, testQueryClient } from './helpers';
import flatText from './fixtures/flat.events.jsonl?raw';

const flat = parseRunEvents(flatText);
const SID = '20261013-153012-a1b2c3';

/** A stand in for the browser's EventSource that the test drives. */
class FakeEventSource {
  static instances: FakeEventSource[] = [];
  url: string;
  closed = false;
  onmessage: ((message: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(url: string) {
    this.url = url;
    FakeEventSource.instances.push(this);
  }

  close() {
    this.closed = true;
  }

  send(event: unknown) {
    this.onmessage?.({ data: JSON.stringify(event) });
  }
}

function wrapper(client = testQueryClient()) {
  return {
    client,
    wrapper: ({ children }: { children: ReactNode }) => createElement(QueryClientProvider, { client }, children),
  };
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  useRunStore.getState().resetFor(SID);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('useRunEvents', () => {
  it('opens after the last seq held, applies messages and closes itself on run_finished', async () => {
    const store = useRunStore.getState();
    store.begin('r1');
    store.apply('r1', flat.slice(0, 4));
    const { client, wrapper: Wrapper } = wrapper();
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    renderHook(() => useRunEvents('r1'), { wrapper: Wrapper });

    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/runs/r1/events?after=4');

    act(() => source.send(flat[4]));
    await waitFor(() => expect(useRunStore.getState().current?.steps.bump.status).toBe('running'));

    act(() => {
      for (const event of flat.slice(5)) source.send(event);
    });
    expect(source.closed).toBe(true);
    const state = useRunStore.getState();
    expect(state.current).toBeNull();
    expect(state.last?.runId).toBe('r1');
    expect(state.last?.steps.scaled.status).toBe('computed');
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['preview', SID] });
    expect(invalidate).toHaveBeenCalledWith({ queryKey: ['runs', SID] });
  });

  it('ignores a replayed event and does nothing without a run', () => {
    const store = useRunStore.getState();
    store.begin('r1');
    const { wrapper: Wrapper } = wrapper();
    renderHook(() => useRunEvents(null), { wrapper: Wrapper });
    expect(FakeEventSource.instances).toHaveLength(0);

    renderHook(() => useRunEvents('r1'), { wrapper: Wrapper });
    const source = FakeEventSource.instances[0];
    act(() => {
      source.send(flat[0]);
      source.send(flat[0]);
      source.send(flat.at(-1));
    });
    expect(useRunStore.getState().last?.lastSeq).toBe(flat.length);
    expect(useRunStore.getState().last?.seen.size).toBe(2);
  });

  it('closes on an error once the run has finished', () => {
    const store = useRunStore.getState();
    store.begin('r1');
    store.apply('r1', flat);
    const { wrapper: Wrapper } = wrapper();
    renderHook(() => useRunEvents('r1'), { wrapper: Wrapper });
    const source = FakeEventSource.instances[0];
    act(() => source.onerror?.());
    expect(source.closed).toBe(true);
  });
});

describe('useFollowRuns', () => {
  it('replays the newest run of a session after a reload', async () => {
    mockApi({
      [`GET /api/sessions/${SID}/runs`]: [{ id: 'r9', status: 'completed', session: SID }],
    });
    const { wrapper: Wrapper } = wrapper();
    renderHook(() => useFollowRuns(SID), { wrapper: Wrapper });
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/runs/r9/events?after=0');
    act(() => {
      for (const event of flat) source.send(event);
    });
    expect(source.closed).toBe(true);
    expect(useRunStore.getState().last?.runId).toBe('r9');
    expect(useRunStore.getState().last?.status).toBe('completed');
  });

  it('replays an older run chosen to show on the diagram', async () => {
    mockApi({ [`GET /api/sessions/${SID}/runs`]: [] });
    const { wrapper: Wrapper } = wrapper();
    renderHook(() => useFollowRuns(SID), { wrapper: Wrapper });
    act(() => useRunStore.getState().show('r3'));
    await waitFor(() => expect(FakeEventSource.instances).toHaveLength(1));
    const source = FakeEventSource.instances[0];
    expect(source.url).toBe('/api/runs/r3/events?after=0');
    act(() => {
      for (const event of flat) source.send(event);
    });
    const state = useRunStore.getState();
    expect(state.replay?.status).toBe('completed');
    expect(state.shown).toBe('r3');
    expect(state.current).toBeNull();
  });
});
