/** Grouping, filtering and labels for the Recipes pane. */
import type { RecipeEntry, RecipesResponse, SessionSummary } from '../api/models';

export interface RecipeGroup {
  root: string;
  recipes: RecipeEntry[];
}

/** Whether a recipe matches a filter text, by name, path or description. */
export function matchesFilter(recipe: RecipeEntry, filter: string): boolean {
  const text = filter.trim().toLowerCase();
  if (!text) return true;
  return [recipe.name, recipe.relative, recipe.description ?? ''].some((value) =>
    value.toLowerCase().includes(text),
  );
}

/** A file included by another recipe. */
export function isSubRecipe(recipe: RecipeEntry): boolean {
  return recipe.included_by.length > 0;
}

/**
 * Recipes grouped by root in root order, filtered; empty groups are dropped.
 *
 * Args:
 *   response: The recipe list.
 *   filter: Text to match by name, path or description.
 *   showSubRecipes: Also list files that other recipes include.
 */
export function groupRecipes(
  response: RecipesResponse,
  filter: string,
  showSubRecipes = true,
): RecipeGroup[] {
  return response.roots
    .map((root) => ({
      root,
      recipes: response.recipes.filter(
        (recipe) =>
          recipe.root === root &&
          (showSubRecipes || !isSubRecipe(recipe)) &&
          matchesFilter(recipe, filter),
      ),
    }))
    .filter((group) => group.recipes.length > 0);
}

/** The last two segments of a path, for a short root label. */
export function shortPath(path: string): string {
  return path.split(/[\\/]/).filter(Boolean).slice(-2).join('/');
}

/** The folder of a path, without the file name. */
function folderOf(path: string): string {
  const parts = path.split('/');
  return parts.slice(0, -1).join('/');
}

/** The newest session opened from a recipe file, if any. */
export function latestSessionFor(
  sessions: SessionSummary[],
  path: string,
): SessionSummary | undefined {
  return sessions
    .filter((session) => session.origin === path)
    .sort((a, b) => b.created.localeCompare(a.created))[0];
}

/**
 * Where a session's recipe lives: its folder relative to its recipe root, or
 * the last two folders of its origin when the recipe is not in the list.
 */
export function sessionFolder(session: SessionSummary, recipes: RecipeEntry[]): string {
  const recipe = recipes.find((entry) => entry.path === session.origin);
  if (recipe) return folderOf(recipe.relative) || shortPath(recipe.root);
  return shortPath(folderOf(session.origin));
}

/** The recipes that include a file, as list entries where known. */
export function parentsOf(path: string, recipes: RecipeEntry[]): RecipeEntry[] {
  const recipe = recipes.find((entry) => entry.path === path);
  if (!recipe) return [];
  return recipe.included_by
    .map((parent) => recipes.find((entry) => entry.path === parent))
    .filter((entry): entry is RecipeEntry => entry !== undefined);
}

/** "2026-10-13 15:30" from an ISO 8601 time, in local time. */
export function formatTime(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  const pad = (value: number) => String(value).padStart(2, '0');
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}
