import { renderHook } from '@testing-library/react';
import type { ReactNode } from 'react';
import { describe, expect, it } from 'vitest';
import { createViewStore, useView, useViewStore, VIEW_KEY, ViewStoreContext } from '../src/state/view';

describe('createViewStore', () => {
  it('gives each store its own selection, drill and expansion', () => {
    const one = createViewStore();
    const two = createViewStore();
    one.getState().select({ kind: 'step', id: 'seed' });
    one.getState().setDrill(['inc:sub/chain.yaml']);
    one.getState().toggleStepExpanded('seed');
    expect(two.getState().selected).toBeNull();
    expect(two.getState().drill).toEqual([]);
    expect(two.getState().expandedSteps.size).toBe(0);
    expect(useView.getState().selected).toBeNull();
  });

  it('saves preferences under its own storage key', () => {
    const site = createViewStore({ storageKey: 'rc.site.view' });
    site.getState().setTextSize(1.5);
    expect(JSON.parse(window.localStorage.getItem('rc.site.view')!)).toMatchObject({ textSize: 1.5 });
    expect(window.localStorage.getItem(VIEW_KEY)).toBeNull();
    expect(createViewStore({ storageKey: 'rc.site.view' }).getState().textSize).toBe(1.5);
  });

  it('keeps nothing with a null storage key', () => {
    const store = createViewStore({ storageKey: null });
    store.getState().setDirection('DOWN');
    store.getState().setConnected(true);
    expect(window.localStorage.length).toBe(0);
  });

  it('starts from the default preferences, with valid stored values over them', () => {
    expect(createViewStore().getState()).toMatchObject({ direction: 'RIGHT', textSize: 1.15 });
    window.localStorage.setItem('rc.site.view', JSON.stringify({ textSize: 1, direction: 'sideways' }));
    const store = createViewStore({ storageKey: 'rc.site.view' });
    expect(store.getState()).toMatchObject({ direction: 'RIGHT', textSize: 1 });
  });

  it('saves the application store under rc.view as before', () => {
    const before = useView.getState().textSize;
    useView.getState().setTextSize(1.3);
    expect(JSON.parse(window.localStorage.getItem(VIEW_KEY)!)).toMatchObject({ textSize: 1.3 });
    useView.setState({ textSize: before });
  });
});

describe('useViewStore', () => {
  it('is the application store without a provider', () => {
    const { result } = renderHook(() => useViewStore());
    expect(result.current).toBe(useView);
  });

  it('is the provider store inside one', () => {
    const store = createViewStore();
    const wrapper = ({ children }: { children: ReactNode }) => (
      <ViewStoreContext.Provider value={store}>{children}</ViewStoreContext.Provider>
    );
    const { result } = renderHook(() => useViewStore(), { wrapper });
    expect(result.current).toBe(store);
  });
});
