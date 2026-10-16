/** The docs site's colour scheme: Material keeps it on body[data-md-color-scheme]. */
import { useEffect } from 'react';
import type { Theme, ViewStore } from '../state/view';

/** The client theme matching Material's scheme: slate is dark, any other light. */
export function materialTheme(): Theme {
  return document.body?.dataset.mdColorScheme === 'slate' ? 'dark' : 'light';
}

/**
 * Keep a view store's theme on Material's scheme, following the palette toggle.
 *
 * The canvas reads its colour mode from the store, so the store holds the
 * theme and the viewer root reads it back from there.
 */
export function useMaterialTheme(store: ViewStore): Theme {
  useEffect(() => {
    const sync = () => {
      const theme = materialTheme();
      if (store.getState().theme !== theme) store.setState({ theme });
    };
    sync();
    const observer = new MutationObserver(sync);
    observer.observe(document.body, { attributes: true, attributeFilter: ['data-md-color-scheme'] });
    return () => observer.disconnect();
  }, [store]);
  return store((state) => state.theme);
}
