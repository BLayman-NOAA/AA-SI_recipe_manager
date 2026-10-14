import { useRecipes } from '../api/queries';
import { parentsOf } from '../recipes/recipeList';
import { useOpenRecipe } from '../recipes/useOpenRecipe';

/** Says so when the open file is a sub-recipe, with links that open its parents. */
export function SubRecipeBanner({ origin }: { origin: string }) {
  const recipes = useRecipes();
  const { open, pending } = useOpenRecipe();
  const parents = parentsOf(origin, recipes.data?.recipes ?? []);
  if (parents.length === 0) return null;
  return (
    <div className="rc-banner" role="note">
      This file is a sub-recipe, included by {parents.map((parent) => parent.name).join(', ')}. Opening the
      parent recipe is usually what you want:{' '}
      {parents.map((parent, index) => (
        <span key={parent.path}>
          {index > 0 && ', '}
          <button
            type="button"
            className="rc-link-button"
            disabled={pending}
            title={parent.path}
            onClick={() => open(parent.path)}
          >
            open {parent.name}
          </button>
        </span>
      ))}
    </div>
  );
}
