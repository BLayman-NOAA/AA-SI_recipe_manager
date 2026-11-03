/** Editing state the app shows around the session: errors, notices, warnings, kept files. */
import { create } from 'zustand';
import { plural } from '../text';

/** How long a notice stays before it clears itself. */
export const NOTICE_MS = 6000;

/** "x.yaml reloaded from disk", or "2 files reloaded from disk: a, b". */
export function reloadNotice(labels: string[]): string {
  if (labels.length === 1) return `${labels[0]} reloaded from disk`;
  return `${plural(labels.length, 'file')} reloaded from disk: ${labels.join(', ')}`;
}

/** The edit just applied changed the hash of steps the survey tier holds. */
export interface SurveyWarning {
  /** Every step whose hash the edit changed, in run order. */
  changedHashes: string[];
  /** The survey backed ones among them. */
  lost: string[];
  /** A save to the run config file, which is not in the session's history, so Undo cannot take it back. */
  config?: boolean;
}

interface EditingState {
  /** The last editing failure, cleared by the next successful edit. */
  error: string | null;
  /** A short message that clears itself after NOTICE_MS. */
  notice: string | null;
  surveyWarning: SurveyWarning | null;
  /**
   * Files the user chose to keep the session's version of, by path, with the
   * disk digest they were kept against; a new disk change brings the banner back.
   */
  kept: Record<string, string>;
  setError: (error: string | null) => void;
  setNotice: (notice: string | null) => void;
  showSurveyWarning: (warning: SurveyWarning) => void;
  dismissSurveyWarning: () => void;
  keep: (path: string, digest: string) => void;
  /** Forget everything, for another session. */
  reset: () => void;
}

let noticeTimer: ReturnType<typeof setTimeout> | undefined;

export const useEditingStore = create<EditingState>((set) => ({
  error: null,
  notice: null,
  surveyWarning: null,
  kept: {},
  setError: (error) => set({ error }),
  setNotice: (notice) => {
    clearTimeout(noticeTimer);
    noticeTimer = notice === null ? undefined : setTimeout(() => set({ notice: null }), NOTICE_MS);
    set({ notice });
  },
  showSurveyWarning: (surveyWarning) => set({ surveyWarning }),
  dismissSurveyWarning: () => set({ surveyWarning: null }),
  keep: (path, digest) => set((state) => ({ kept: { ...state.kept, [path]: digest } })),
  reset: () => {
    clearTimeout(noticeTimer);
    set({ error: null, notice: null, surveyWarning: null, kept: {} });
  },
}));

/** Say which files the server reloaded from disk, if any. */
export function noticeReloaded(labels: string[]) {
  if (labels.length > 0) useEditingStore.getState().setNotice(reloadNotice(labels));
}
