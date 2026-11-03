import type { DiskConflict } from '../api/models';
import { useReload } from '../api/queries';
import { useEditingStore } from './store';
import { openConflicts } from './useDiskSync';

/** The banner's sentence for one conflict. */
export function conflictText(conflict: DiskConflict): string {
  if (conflict.error === null) return `${conflict.path} changed on disk and has unsaved edits here.`;
  return `${conflict.path} changed on disk but does not load: ${conflict.error.replace(/\.\s*$/, '')}.`;
}

/**
 * Above the diagram, one line per file changed on disk that the session could
 * not take in by itself: it has unsaved edits here, or the disk text does not
 * load. Reload takes the disk's text (undoable); Keep hides the line until the
 * disk changes again.
 */
export function DiskBanner({ sid, conflicts }: { sid: string; conflicts: DiskConflict[] }) {
  const kept = useEditingStore((state) => state.kept);
  const keep = useEditingStore((state) => state.keep);
  const reload = useReload(sid);
  const shown = openConflicts(conflicts, kept);
  if (shown.length === 0) return null;
  return (
    <div className="rc-banner rc-disk-banner" role="alert">
      {shown.map((conflict) => (
        <div key={conflict.path} className="rc-disk-conflict">
          <span className="rc-row-main">{conflictText(conflict)}</span>
          {conflict.error === null && (
            <button
              type="button"
              className="rc-button rc-small"
              disabled={reload.isPending}
              onClick={() => reload.mutate([conflict.path])}
            >
              Reload from disk
            </button>
          )}
          <button
            type="button"
            className="rc-button rc-small"
            onClick={() => keep(conflict.path, conflict.disk_sha256)}
          >
            Keep the session's version
          </button>
        </div>
      ))}
    </div>
  );
}
