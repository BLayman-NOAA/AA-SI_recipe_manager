# Building this site

The site is built with [MkDocs](https://www.mkdocs.org/) and the Material
theme, from the `docs/` folder of the recipe manager repository, and published
to GitHub Pages by the `docs` GitHub Actions workflow on every push to `main`.

## What is generated, and where

| Content | Source |
| --- | --- |
| Hand-written pages | `docs/**/*.md` |
| Example recipes | `docs/recipes/`, listed in `docs/site_recipes.yaml` |
| Flow diagrams, YAML and Notebook tabs, op data | `docs/data/`, written by `aa-recipe site-data` and committed |
| Op pages, the CLI page and the recipe JSON Schema | Generated during the build by `docs/gen_pages.py`, from `docs/data/ops.json` and the installed package |

`aa-recipe site-data` needs the full scientific stack installed, because it
imports each op's implementation to link its source code on GitHub. That is
why its output is committed: the build machine only needs the `docs` extra.

## After changing an op spec or a documented recipe

```bash
aa-recipe site-data           # rewrite docs/data
aa-recipe site-data --check   # what the workflow checks; exits 1 when docs/data is stale
```

Then commit `docs/data` with the change. The workflow fails while it is out of
date.

## Adding a recipe to the site

1. Put the recipe under `docs/recipes/`. A sub-recipe needs its parent recipe
   there too.
2. Add an entry to `docs/site_recipes.yaml` with its `slug`, `path`, `title`,
   `page`, and `parent` if it is included by another recipe.
3. Embed it on its page with
   `--8<-- "data/partials/<slug>.md"`.
4. Run `aa-recipe site-data`.

## Previewing locally

```bash
pip install -e ".[docs]"
mkdocs serve
```

then open <http://127.0.0.1:8000/>. `mkdocs build --strict` is what the
workflow runs; it fails on any broken link.
