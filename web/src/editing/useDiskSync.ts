/** Keep the session in step with hand edits to its files on disk. */
import { useIsMutating } from '@tanstack/react-query';
import { useEffect, useRef } from 'react';
import type { DiskConflict } from '../api/models';
import { EDITING_KEY, useSync } from '../api/queries';
import { onPopoutWindows } from '../shell/dock';
import { noticeReloaded } from './store';

/** How often the client checks the disk while the window is visible. */
export const DISK_SYNC_MS = 30000;

/** Conflicts the banner shows: those not kept against the disk text they have now. */
export function openConflicts(conflicts: DiskConflict[], kept: Record<string, string>): DiskConflict[] {
  return conflicts.filter((conflict) => kept[conflict.path] !== conflict.disk_sha256);
}

/** How many files the user kept the session's version of while the disk still differs. */
export function keptCount(conflicts: DiskConflict[], kept: Record<string, string>): number {
  return conflicts.length - openConflicts(conflicts, kept).length;
}

/**
 * Post a sync when a session opens, when the window or a popout window gains
 * focus or becomes visible, and every DISK_SYNC_MS while the window is
 * visible. Syncs never overlap and wait while an edit is being sent; a sync
 * is an editing mutation itself, so edits wait for it too. Reloaded files get
 * a notice.
 */
export function useDiskSync(sid: string | null) {
  const { mutateAsync: sync } = useSync(sid);
  const editing = useIsMutating({ mutationKey: EDITING_KEY });
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const busy = useRef(false);

  useEffect(() => {
    if (!sid) return;
    const check = (doc: Document) => {
      if (busy.current || editingRef.current > 0 || doc.visibilityState !== 'visible') return;
      busy.current = true;
      sync()
        .then((response) => noticeReloaded(response.reloaded))
        .catch(() => {})
        .finally(() => {
          busy.current = false;
        });
    };
    const watch = (target: Window) => {
      const onEvent = () => check(target.document);
      target.addEventListener('focus', onEvent);
      target.document.addEventListener('visibilitychange', onEvent);
      return () => {
        target.removeEventListener('focus', onEvent);
        target.document.removeEventListener('visibilitychange', onEvent);
      };
    };
    check(document);
    const stopMain = watch(window);
    const stopPopouts = onPopoutWindows(watch);
    const timer = window.setInterval(() => check(document), DISK_SYNC_MS);
    return () => {
      stopMain();
      stopPopouts();
      window.clearInterval(timer);
    };
  }, [sid, sync]);
}
