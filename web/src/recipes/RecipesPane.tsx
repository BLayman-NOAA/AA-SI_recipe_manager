import { useState, type MouseEvent } from 'react';
import type { RecipeEntry } from '../api/models';
import { useCloseSession, useRecipes, useSessions } from '../api/queries';
import { useSessionStore } from '../state/session';
import { readStored, writeStored } from '../state/storage';
import { Prose } from '../Prose';
import { firstSentence, plainText } from '../text';
import { formatTime, groupRecipes, sessionFolder, shortPath } from './recipeList';
import { useOpenRecipe } from './useOpenRecipe';

/** Remembers the "Show sub-recipes" toggle per browser. */
const SUB_RECIPES_KEY = 'rc.recipes.subRecipes';

/** Keep a button's clicks from reaching the row's double click handler. */
function stop(event: MouseEvent) {
  event.stopPropagation();
}

interface RecipeRowProps {
  recipe: RecipeEntry;
  pending: boolean;
  onOpen: (path: string, fresh?: boolean) => void;
}

function RecipeRow({ recipe, pending, onOpen }: RecipeRowProps) {
  const broken = Boolean(recipe.error);
  return (
    <li
      className="rc-row"
      onDoubleClick={broken ? undefined : () => onOpen(recipe.path)}
      title={plainText(recipe.description) || undefined}
    >
      <div className="rc-row-main">
        <div className="rc-row-title">
          {recipe.name}{' '}
          {broken && <span className="rc-badge rc-badge-warning">does not parse</span>}
          {recipe.included_by.length > 0 && (
            <span className="rc-badge" title={recipe.included_by.join('\n')}>
              included by {recipe.included_by.length}
            </span>
          )}
        </div>
        {broken ? (
          <div className="rc-row-sub rc-status-warning" title={recipe.error ?? undefined}>
            {recipe.error}
          </div>
        ) : (
          recipe.description && <div className="rc-row-sub">
              <Prose text={firstSentence(recipe.description)} />
            </div>
        )}
        <div className="rc-row-sub rc-mono">{recipe.relative}</div>
        <div className="rc-row-sub">modified {formatTime(recipe.modified)}</div>
      </div>
      {!broken && (
        <div className="rc-row-actions">
          <button
            type="button"
            className="rc-button rc-small"
            disabled={pending}
            title="Open the recipe, or switch to its newest session"
            onClick={() => onOpen(recipe.path)}
            onDoubleClick={stop}
          >
            Open
          </button>
          <button
            type="button"
            className="rc-button rc-small"
            disabled={pending}
            title="Open the recipe in a new session"
            onClick={() => onOpen(recipe.path, true)}
            onDoubleClick={stop}
          >
            New session
          </button>
        </div>
      )}
    </li>
  );
}

function RecentSessions({ recipes }: { recipes: RecipeEntry[] }) {
  const sessions = useSessions();
  const sid = useSessionStore((state) => state.sid);
  const setSid = useSessionStore((state) => state.setSid);
  const deleteSession = useCloseSession();
  const list = sessions.data ?? [];

  const confirmDelete = (id: string, title: string) => {
    if (window.confirm(`Delete the session "${title}"? Its saved inputs and settings are removed.`)) {
      deleteSession.mutate(id);
    }
  };

  return (
    <section>
      <h3>Recent sessions</h3>
      {list.length === 0 && <div className="rc-empty">No sessions yet.</div>}
      <ul className="rc-list">
        {list.map((session) => (
          <li
            key={session.id}
            className={`rc-row${session.id === sid ? ' rc-selected' : ''}`}
            onDoubleClick={() => setSid(session.id)}
            title={session.origin}
          >
            <div className="rc-row-main">
              <div className="rc-row-title">
                {session.title} <span className="rc-row-sub rc-mono">{sessionFolder(session, recipes)}</span>
              </div>
              <div className="rc-row-sub">opened {formatTime(session.created)}</div>
            </div>
            <div className="rc-row-actions">
              <button
                type="button"
                className="rc-button rc-small"
                disabled={session.id === sid}
                onClick={() => setSid(session.id)}
                onDoubleClick={stop}
              >
                Reopen
              </button>
              <button
                type="button"
                className="rc-button rc-small"
                title="Delete the session and its saved state"
                onClick={() => confirmDelete(session.id, session.title)}
                onDoubleClick={stop}
              >
                Delete session
              </button>
            </div>
          </li>
        ))}
      </ul>
    </section>
  );
}

function readShowSubRecipes(): boolean {
  return readStored(SUB_RECIPES_KEY) === 'true';
}

/** Recent sessions, then the recipes under the server's recipe roots. */
export function RecipesPane() {
  const recipes = useRecipes();
  const { open, pending, error } = useOpenRecipe();
  const [filter, setFilter] = useState('');
  const [showSubRecipes, setShowSubRecipes] = useState(readShowSubRecipes);
  const groups = recipes.data ? groupRecipes(recipes.data, filter, showSubRecipes) : [];

  const toggleSubRecipes = (show: boolean) => {
    setShowSubRecipes(show);
    writeStored(SUB_RECIPES_KEY, String(show));
  };

  return (
    <div className="rc-pane">
      <RecentSessions recipes={recipes.data?.recipes ?? []} />
      <h3>Recipes</h3>
      <div className="rc-pane-toolbar">
        <input
          className="rc-input"
          type="search"
          placeholder="Filter recipes"
          aria-label="Filter recipes"
          value={filter}
          onChange={(event) => setFilter(event.target.value)}
        />
        <button type="button" className="rc-button rc-small" onClick={() => recipes.refetch()}>
          Refresh
        </button>
      </div>
      <label className="rc-check">
        <input
          type="checkbox"
          checked={showSubRecipes}
          onChange={(event) => toggleSubRecipes(event.target.checked)}
        />
        Show sub-recipes
      </label>
      {pending && <div className="rc-empty">Opening...</div>}
      {error && <div className="rc-field-error">{error}</div>}
      {recipes.isPending && <div className="rc-empty">Loading recipes...</div>}
      {recipes.error && <div className="rc-field-error">{recipes.error.message}</div>}
      {recipes.data && groups.length === 0 && <div className="rc-empty">No recipes match.</div>}
      {groups.map((group) => (
        <section key={group.root}>
          <h3 className="rc-root" title={group.root}>
            {shortPath(group.root)}
          </h3>
          <ul className="rc-list">
            {group.recipes.map((recipe) => (
              <RecipeRow key={recipe.path} recipe={recipe} pending={pending} onOpen={open} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}
