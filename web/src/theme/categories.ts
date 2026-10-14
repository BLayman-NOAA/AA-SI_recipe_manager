/**
 * Op category colors for the Ops pane.
 *
 * Keys are the categories of the op catalog (`aa-recipe ops --json`). The docs
 * site has no category colors yet; this palette is the reference for both.
 */

export const CATEGORY_COLORS: Record<string, string> = {
  data_retrieval: '#4e79a7',
  io: '#76b7b2',
  calibration: '#f28e2b',
  preprocessing: '#59a14f',
  processing: '#8cb369',
  'level 2': '#499894',
  noise_removal: '#e15759',
  masking: '#b07aa1',
  detection: '#edc948',
  consolidate: '#9c755f',
  ml_preprocessing: '#ff9da7',
  ml_clustering: '#d37295',
  annotation: '#a0cbe8',
  visualization: '#f1ce63',
  utility: '#bab0ac',
};

/** A category's color; neutral for no category or one missing from the palette. */
export function categoryColor(category: string | null | undefined): string {
  return (category && CATEGORY_COLORS[category]) || 'var(--rc-muted)';
}

/** Human label for a category key. */
export function categoryLabel(category: string | null | undefined): string {
  if (!category) return 'uncategorized';
  return category.replace(/_/g, ' ');
}
