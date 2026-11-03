/** A pane's tab: dockview's own, with a right click menu to move the pane between windows. */
import { DockviewDefaultTab, type IDockviewPanelHeaderProps } from 'dockview-react';
import { useCallback, useEffect, useRef, useState, type MouseEvent } from 'react';
import { createPortal } from 'react-dom';
import { popoutPane, returnPane } from './dock';
import { MenuList } from './MenuBar';
import { tabMenuItems } from './menus';
import { isPaneId } from './panes';

interface OpenMenu {
  x: number;
  y: number;
  /** The document the tab is in, the main window's or a popout's. */
  doc: Document;
  poppedOut: boolean;
}

function TabMenu({ id, menu, onDone }: { id: string; menu: OpenMenu; onDone: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const onPointer = (event: globalThis.MouseEvent) => {
      if (ref.current && !ref.current.contains(event.target as Node)) onDone();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') onDone();
    };
    menu.doc.addEventListener('mousedown', onPointer);
    menu.doc.addEventListener('keydown', onKey);
    return () => {
      menu.doc.removeEventListener('mousedown', onPointer);
      menu.doc.removeEventListener('keydown', onKey);
    };
  }, [menu, onDone]);
  if (!isPaneId(id)) return null;
  const items = tabMenuItems(id, menu.poppedOut, { popoutPane, returnPane });
  return createPortal(
    <div className="rc-tab-menu" ref={ref} style={{ left: menu.x, top: menu.y }}>
      <MenuList items={items} label="Pane" onDone={onDone} />
    </div>,
    menu.doc.body,
  );
}

export function PaneTab(props: IDockviewPanelHeaderProps) {
  const [menu, setMenu] = useState<OpenMenu | null>(null);
  const close = useCallback(() => setMenu(null), []);
  const onContextMenu = (event: MouseEvent<HTMLDivElement>) => {
    event.preventDefault();
    setMenu({
      x: event.clientX,
      y: event.clientY,
      doc: event.currentTarget.ownerDocument,
      poppedOut: props.api.location.type === 'popout',
    });
  };
  return (
    <>
      <DockviewDefaultTab {...props} onContextMenu={onContextMenu} />
      {menu && <TabMenu id={props.api.id} menu={menu} onDone={close} />}
    </>
  );
}
