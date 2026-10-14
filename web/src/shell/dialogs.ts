/** Which shell dialog is open. */
import { create } from 'zustand';

export type DialogId = 'shortcuts' | 'about';

interface DialogState {
  open: DialogId | null;
  show: (id: DialogId) => void;
  close: () => void;
}

export const useDialogs = create<DialogState>((set) => ({
  open: null,
  show: (open) => set({ open }),
  close: () => set({ open: null }),
}));
