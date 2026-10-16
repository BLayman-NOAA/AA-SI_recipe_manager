/**
 * One diagram on a docs page: the application's diagram, inspector and YAML
 * text over a graph from the site, read only, in its own view store.
 */
import { useEffect, useMemo, useRef, useState, type KeyboardEvent, type RefObject } from 'react';
import type { Graph } from '../api/graph';
import { DiagramView } from '../diagram/DiagramView';
import { DocsEnvContext, type DocsEnv } from '../env';
import { InspectorBody } from '../inspector/InspectorBody';
import { ViewStoreContext, type ViewStore } from '../state/view';
import { deepLink } from './siteData';
import { useMaterialTheme } from './theme';
import { ViewerToolbar, type MainView } from './ViewerToolbar';
import { ViewerYaml } from './ViewerYaml';

/** A viewer at least this wide opens with its inspector shown. */
export const SIDE_OPEN_WIDTH = 1100;

export interface SiteViewerProps {
  graph: Graph;
  /** The manifest entry this viewer documents; also its deep link name. */
  slug: string;
  store: ViewStore;
  /** The docs site root, ending in "/". */
  siteRoot: string;
  /** The root recipe's folder relative to the site root. */
  recipeDir: string;
  /** Pages documenting the graph's includes. */
  pages: DocsEnv['pages'];
  /** Whether the inspector starts open (main.tsx: when the page gives the viewer room). */
  sideOpenAtStart?: boolean;
}

function isTyping(target: EventTarget): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName) || target.isContentEditable;
}

/** Full screen for one element: the page overlay class, and the browser's full screen when allowed. */
function useFullscreen(element: RefObject<HTMLElement | null>, store: ViewStore) {
  const [full, setFull] = useState(false);

  useEffect(() => {
    if (!full) return;
    const onChange = () => {
      if (!document.fullscreenElement) setFull(false);
    };
    document.addEventListener('fullscreenchange', onChange);
    document.documentElement.classList.add('rc-viewer-no-scroll');
    return () => {
      document.removeEventListener('fullscreenchange', onChange);
      document.documentElement.classList.remove('rc-viewer-no-scroll');
      if (document.fullscreenElement && document.fullscreenElement === element.current) {
        void document.exitFullscreen?.().catch(() => {});
      }
    };
  }, [full, element]);

  // Fit once the new size is laid out, entering and leaving.
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    const frame = window.requestAnimationFrame(() => store.getState().requestFit());
    return () => window.cancelAnimationFrame(frame);
  }, [full, store]);

  const enter = () => {
    setFull(true);
    element.current?.requestFullscreen?.().catch(() => {});
  };
  return { full, enter, exit: () => setFull(false) };
}

export function SiteViewer({ graph, slug, store, siteRoot, recipeDir, pages, sideOpenAtStart }: SiteViewerProps) {
  const root = useRef<HTMLDivElement>(null);
  const theme = useMaterialTheme(store);
  const selected = store((state) => state.selected);
  const drill = store((state) => state.drill);
  const [main, setMain] = useState<MainView>('diagram');
  const [inspector, setInspector] = useState(Boolean(sideOpenAtStart));
  const { full, enter, exit } = useFullscreen(root, store);
  const docsEnv = useMemo<DocsEnv>(() => ({ docsUrl: siteRoot, linkTarget: '_self', pages }), [siteRoot, pages]);

  // Full screen shows the inspector; one opened for it closes again on leaving,
  // unless the reader toggled it meanwhile.
  const inspectorForFull = useRef(false);
  const toggleInspector = () => {
    inspectorForFull.current = false;
    setInspector((open) => !open);
  };
  const enterFull = () => {
    if (!inspector) {
      inspectorForFull.current = true;
      setInspector(true);
    }
    enter();
  };
  useEffect(() => {
    if (full || !inspectorForFull.current) return;
    inspectorForFull.current = false;
    setInspector(false);
  }, [full]);

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return;
    const view = store.getState();
    let handled = true;
    if (event.key === 'Escape') {
      if (full) exit();
      else if (view.selected) view.select(null);
      else if (view.drill.length > 0) view.drillOut();
      else handled = false;
    } else if (isTyping(event.target)) {
      handled = false;
    } else if (event.key.toLowerCase() === 'f' && !event.shiftKey && main === 'diagram') {
      view.requestFit();
    } else if (event.key === '/' && main === 'diagram') {
      view.requestFind();
    } else {
      handled = false;
    }
    if (handled) {
      // Material's search listens for F and / on the page.
      event.preventDefault();
      event.stopPropagation();
    }
  };

  return (
    <ViewStoreContext.Provider value={store}>
      <DocsEnvContext.Provider value={docsEnv}>
        <div
          ref={root}
          className={`rc-viewer${full ? ' rc-viewer-fullscreen' : ''}`}
          data-theme={theme}
          tabIndex={-1}
          onKeyDown={onKeyDown}
        >
          <ViewerToolbar
            main={main}
            onMain={setMain}
            inspector={inspector}
            onInspector={toggleInspector}
            fullscreen={full}
            onFullscreen={full ? exit : enterFull}
          />
          <div className="rc-viewer-body">
            <div className="rc-viewer-main">
              {/* The diagram stays mounted under the YAML, so switching back keeps its layout. */}
              <div className="rc-viewer-layer" inert={main !== 'diagram'}>
                <DiagramView graph={graph} scope={slug} wheel={full ? 'zoom' : 'page'} />
              </div>
              {main === 'yaml' && (
                <div className="rc-viewer-layer rc-viewer-yaml">
                  <ViewerYaml graph={graph} siteRoot={siteRoot} recipeDir={recipeDir} />
                </div>
              )}
            </div>
            {inspector && (
              // A div, not an aside: Material sets its own font and colour on aside.
              <div className="rc-viewer-side" role="complementary" aria-label="Inspector">
                <div className="rc-viewer-side-head">
                  <span className="rc-viewer-side-title">Inspector</span>
                  <span className="rc-spacer" />
                  {selected?.kind === 'step' && (
                    <a
                      className="rc-viewer-link"
                      href={deepLink(slug, selected.id)}
                      title="A link to this page with this step selected"
                    >
                      Link to this step
                    </a>
                  )}
                </div>
                <div className="rc-viewer-side-body">
                  <InspectorBody graph={graph} selected={selected} drill={drill} />
                </div>
              </div>
            )}
          </div>
        </div>
      </DocsEnvContext.Provider>
    </ViewStoreContext.Provider>
  );
}
