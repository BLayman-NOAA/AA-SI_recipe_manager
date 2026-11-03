/** Set or remove `checkpoint: always` on a step of the open session. */
import { useQueryClient } from '@tanstack/react-query';
import { useCallback } from 'react';
import { applyEdit } from '../api/queries';
import { useSessionStore } from '../state/session';
import { setCheckpoint } from './operations';

/**
 * A function that makes a step a checkpoint, or with false removes its
 * checkpoint setting; it resolves to whether the edit applied.
 *
 * Args:
 *   step: The step id.
 */
export function useSetCheckpoint(step: string): (on: boolean) => Promise<boolean> {
  const client = useQueryClient();
  const sid = useSessionStore((state) => state.sid);
  return useCallback(
    async (on: boolean) => {
      if (!sid) return false;
      return (await applyEdit(client, sid, [setCheckpoint(step, on ? 'always' : null)])) !== null;
    },
    [client, sid, step],
  );
}
