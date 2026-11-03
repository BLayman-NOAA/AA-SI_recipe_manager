/**
 * How the client words step statuses, shared by the marks (badges.ts) and the
 * progress lines (progress.ts). The server's tail.py words its progress lines
 * the same; test/fixtures/progress.expected.json keeps the two in step.
 */

/** How the client names the pruned and skipped statuses: a later step's saved result stands in. */
export const SKIPPED_CACHED = 'skipped: cached';

/** The tooltip of a pruned or skipped step. */
export const SKIPPED_CACHED_TITLE = "Skipped: a later step's saved result is loaded instead";

/** How the client names the untargeted status. */
export const NOT_NEEDED = 'not needed';

/** The tooltip of an untargeted step. */
export const NOT_NEEDED_TITLE = 'Not needed for the run target';

/** A step read from the survey tier. */
export const LOADED_SURVEY = 'loaded from survey cache';

/** A step read from the user tier. */
export const LOADED_USER = 'loaded from user cache';
