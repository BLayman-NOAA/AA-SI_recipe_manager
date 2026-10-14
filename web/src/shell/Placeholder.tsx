interface PlaceholderProps {
  title: string;
  milestone: number;
  children?: string;
}

/** Stand in for a pane that a later milestone fills. */
export function Placeholder({ title, milestone, children }: PlaceholderProps) {
  return (
    <div className="rc-placeholder">
      <div>
        <div>
          {title} arrives in milestone {milestone}.
        </div>
        {children && <div className="rc-field-help">{children}</div>}
      </div>
    </div>
  );
}
