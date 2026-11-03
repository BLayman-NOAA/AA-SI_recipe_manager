import { useEditingStore } from './store';

/** The editing notice ("x.yaml reloaded from disk"), which clears itself. */
export function Notice() {
  const notice = useEditingStore((state) => state.notice);
  if (!notice) return null;
  return <span className="rc-notice">{notice}</span>;
}

/** The last editing failure, until the next successful edit. */
export function EditingError() {
  const error = useEditingStore((state) => state.error);
  if (!error) return null;
  return (
    <span className="rc-status-error rc-editing-error" title={error}>
      {error}
    </span>
  );
}
