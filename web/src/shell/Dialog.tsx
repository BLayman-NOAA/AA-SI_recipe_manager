import { useEffect, type ReactNode } from 'react';

interface DialogProps {
  title: string;
  onClose: () => void;
  children: ReactNode;
}

/** A small modal dialog; Escape or a click outside closes it. */
export function Dialog({ title, onClose, children }: DialogProps) {
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onClose();
      }
    };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);

  return (
    <div className="rc-dialog-backdrop" onMouseDown={onClose}>
      <div
        className="rc-dialog"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="rc-dialog-head">
          <span className="rc-spacer">{title}</span>
          <button type="button" className="rc-button rc-small" onClick={onClose}>
            Close
          </button>
        </div>
        <div className="rc-dialog-body">{children}</div>
      </div>
    </div>
  );
}
