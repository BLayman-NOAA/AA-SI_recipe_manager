import { useState } from 'react';
import { errorPaths, isApiError } from '../api/client';
import type { SessionDocument } from '../api/models';
import { useMutationError, useReload, useSave } from '../api/queries';
import { Dialog } from '../shell/Dialog';
import { plural } from '../text';
import { reloadNotice, useEditingStore } from './store';

/** The labels of the session files whose origins a 409's `details.paths` names; others as given. */
export function labelsOfPaths(session: SessionDocument, paths: string[]): string[] {
  return paths.map((path) => session.files.find((file) => file.origin === path)?.path ?? path);
}

/** The notice after a save, with the files the server reloaded from disk before it. */
export function savedNotice(written: string[], reloaded: string[] = []): string {
  const saved =
    written.length === 0 ? 'Nothing to save: the files match the disk' : `Saved ${plural(written.length, 'file')}`;
  return reloaded.length === 0 ? saved : `${saved}; ${reloadNotice(reloaded)}`;
}

/** File > Save: lists the edited files, then writes them over their originals. */
export function SaveDialog({ session, onClose }: { session: SessionDocument; onClose: () => void }) {
  const save = useSave(session.id);
  const reload = useReload(session.id);
  const error = useMutationError(save);
  /** Files changed on disk since the session read them, after a 409 ChangedOnDisk. */
  const [changed, setChanged] = useState<string[] | null>(null);
  const setNotice = useEditingStore((state) => state.setNotice);

  const write = (overwrite: boolean) =>
    save.mutate(overwrite, {
      onSuccess: (response) => {
        setNotice(savedNotice(response.written, response.reloaded));
        onClose();
      },
      onError: (failure) => {
        if (isApiError(failure, 'ChangedOnDisk')) setChanged(labelsOfPaths(session, errorPaths(failure)));
      },
    });

  const origins = new Map(session.files.map((file) => [file.path, file.origin]));

  if (changed) {
    return (
      <Dialog title="Save" onClose={onClose}>
        <p>These files changed on disk since the session read them:</p>
        <ul className="rc-dialog-list">
          {changed.map((path) => (
            <li key={path} className="rc-mono">
              {path}
            </li>
          ))}
        </ul>
        <p className="rc-row-sub">
          Overwrite writes the session's version over them. Reload from disk takes the disk's text and replaces the
          session's edits to them; Undo brings the edits back.
        </p>
        <div className="rc-dialog-actions">
          <button type="button" className="rc-button rc-primary" disabled={save.isPending} onClick={() => write(true)}>
            Overwrite
          </button>
          <button
            type="button"
            className="rc-button"
            disabled={reload.isPending}
            onClick={() => {
              reload.mutate(changed);
              onClose();
            }}
          >
            Reload from disk
          </button>
          <button type="button" className="rc-button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </Dialog>
    );
  }

  return (
    <Dialog title="Save" onClose={onClose}>
      {session.dirty.length === 0 ? (
        <p>No file has unsaved edits.</p>
      ) : (
        <>
          <p>{`Write ${plural(session.dirty.length, 'edited file')} over the original:`}</p>
          <ul className="rc-dialog-list">
            {session.dirty.map((path) => (
              <li key={path}>
                <span className="rc-mono">{path}</span>
                <span className="rc-row-sub rc-mono"> {origins.get(path) ?? ''}</span>
              </li>
            ))}
          </ul>
        </>
      )}
      {error && !isApiError(save.error, 'ChangedOnDisk') && <p className="rc-error-text">{error}</p>}
      <div className="rc-dialog-actions">
        <button
          type="button"
          className="rc-button rc-primary"
          autoFocus
          disabled={save.isPending || session.dirty.length === 0}
          onClick={() => write(false)}
        >
          Save
        </button>
        <button type="button" className="rc-button" onClick={onClose}>
          Cancel
        </button>
      </div>
    </Dialog>
  );
}
