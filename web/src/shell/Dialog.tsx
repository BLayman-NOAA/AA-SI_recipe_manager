import { useEffect, type ReactNode } from 'react';
import { createPortal } from 'react-dom';

interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
  /** The document to show in: the main window's, or a popout's for a pane there. */
  doc?: Document;
}

/** A small modal dialog; Escape or its Close button closes it, a click outside does not. */
export function Dialog({ title, onClose, children, doc = document }: DialogProps) {
  useEffect(() => {
    const target = doc.defaultView ?? window;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    target.addEventListener('keydown', onKey, true);
    return () => target.removeEventListener('keydown', onKey, true);
  }, [doc, onClose]);

  return createPortal(
    <div className="rc-dialog-backdrop">
      <div className="rc-dialog" role="dialog" aria-modal="true" aria-label={title}>
        <div className="rc-dialog-head">
          <span className="rc-spacer">{title}</span>
          <button type="button" className="rc-button rc-small" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="rc-dialog-body">{children}</div>
      </div>
    </div>,
    doc.body,
  );
}
