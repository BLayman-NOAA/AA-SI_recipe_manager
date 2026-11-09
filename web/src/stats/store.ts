/** Which json output of a step the statistics pane shows, when the step has several. */
import { create } from 'zustand';

export interface StatsChoice {
  step: string;
  output: string;
}

interface StatsState {
  choice: StatsChoice | null;
  choose: (choice: StatsChoice | null) => void;
}

export const useStatsStore = create<StatsState>((set) => ({
  choice: null,
  choose: (choice) => set({ choice }),
}));
