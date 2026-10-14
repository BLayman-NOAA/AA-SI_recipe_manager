import { useEffect, useRef, useState } from 'react';
import { itemTooltip, type Menu, type MenuItem } from './menus';

interface MenuBarProps {
  menus: Menu[];
}

interface MenuListProps {
  items: MenuItem[];
  label: string;
  onDone: () => void;
}

function MenuEntry({ item, onDone }: { item: MenuItem; onDone: () => void }) {
  const [submenuOpen, setSubmenuOpen] = useState(false);
  const hasSubmenu = item.items !== undefined;

  const activate = () => {
    if (!item.enabled) return;
    if (hasSubmenu) {
      setSubmenuOpen((open) => !open);
      return;
    }
    item.action?.();
    onDone();
  };

  return (
    <>
      {item.separator && <li className="rc-menu-separator" role="separator" />}
      <li
        className="rc-menu-item"
        onMouseEnter={() => hasSubmenu && item.enabled && setSubmenuOpen(true)}
        onMouseLeave={() => hasSubmenu && setSubmenuOpen(false)}
      >
        <button
          type="button"
          role={item.checked === undefined ? 'menuitem' : 'menuitemcheckbox'}
          aria-checked={item.checked === undefined ? undefined : item.checked}
          aria-disabled={!item.enabled}
          aria-haspopup={hasSubmenu ? 'menu' : undefined}
          aria-expanded={hasSubmenu ? submenuOpen : undefined}
          title={itemTooltip(item)}
          onClick={activate}
        >
          <span className="rc-menu-check" aria-hidden="true">
            {item.checked ? '✓' : ''}
          </span>
          <span className="rc-menu-label">{item.label}</span>
          {item.shortcut && <span className="rc-menu-shortcut">{item.shortcut}</span>}
          {hasSubmenu && <span aria-hidden="true">{'▸'}</span>}
        </button>
        {hasSubmenu && submenuOpen && (
          <MenuList items={item.items ?? []} label={item.label} onDone={onDone} />
        )}
      </li>
    </>
  );
}

function MenuList({ items, label, onDone }: MenuListProps) {
  return (
    <ul className="rc-menu-list" role="menu" aria-label={label}>
      {items.map((item) => (
        <MenuEntry key={item.id} item={item} onDone={onDone} />
      ))}
    </ul>
  );
}

/** The application menu bar. Menus open on click and follow the pointer once one is open. */
export function MenuBar({ menus }: MenuBarProps) {
  const [openId, setOpenId] = useState<string | null>(null);
  const barRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (openId === null) return;
    const onPointer = (event: MouseEvent) => {
      if (barRef.current && !barRef.current.contains(event.target as Node)) setOpenId(null);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpenId(null);
    };
    document.addEventListener('mousedown', onPointer);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onPointer);
      document.removeEventListener('keydown', onKey);
    };
  }, [openId]);

  return (
    <div className="rc-menubar" role="menubar" ref={barRef}>
      {menus.map((menu) => {
        const open = openId === menu.id;
        return (
          <div className="rc-menu" key={menu.id}>
            <button
              type="button"
              className="rc-menu-button"
              aria-haspopup="menu"
              aria-expanded={open}
              onClick={() => setOpenId(open ? null : menu.id)}
              onMouseEnter={() => openId !== null && setOpenId(menu.id)}
            >
              {menu.label}
            </button>
            {open && <MenuList items={menu.items} label={menu.label} onDone={() => setOpenId(null)} />}
          </div>
        );
      })}
    </div>
  );
}
