/** The open session id, remembered across reloads (FR-3). */
import { create } from 'zustand';
import { readStored, writeStored } from './storage';

export const SESSION_KEY = 'rc.sid';

interface SessionState {
  sid: string | null;
  setSid: (sid: string | null) => void;
}

export const useSessionStore = create<SessionState>((set) => ({
  sid: readStored(SESSION_KEY),
  setSid: (sid) => {
    writeStored(SESSION_KEY, sid);
    set({ sid });
  },
}));
