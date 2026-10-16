# Building this site

The site is built with [MkDocs](https://www.mkdocs.org/) and the Material
theme, from the `docs/` folder of the recipe manager repository, and published
to GitHub Pages by the `docs` GitHub Actions workflow on every push to `main`.

## What is generated, and where

| Content | Source |
| --- | --- |
| Hand-written pages | `docs/**/*.md` |
| Example recipes | `docs/recipes/`, listed in `docs/site_recipes.yaml` |
| Flow diagram, YAML and Notebook tabs, recipe graphs, op data | `docs/data/`, written by `aa-recipe site-data` and committed; graphs are built by `aa_recipe_manager.graph` |
| The diagram viewer | `docs/assets/viewer/`, built from `web/` by `npm run build:viewer`; not committed |
| Op pages, the CLI page and the recipe JSON Schema | Generated during the build by `docs/gen_pages.py`, from `docs/data/ops.json` and the installed package |

`aa-recipe site-data` needs the full scientific stack installed, because it
imports each op's implementation to link its source code on GitHub. That is
why its output is committed: the build machine only needs the `docs` extra and
Node.

## The diagram viewer

Every Flow diagram tab is drawn by the diagram viewer, a read only bundle
built from the diagram code in `web/` (entry `web/src/viewer/`). It has no
server: it reads the root recipe's graph from `docs/data/recipes/<root>.json`,
the documented recipes from `docs/data/manifest.json`, and the recipe files
from `docs/recipes/`.
Each partial holds a `<div class="recipe-viewer">` naming the root recipe in
`data-recipe`, its own entry in `data-slug`, and, for a sub-recipe, the
include to start drilled into in `data-drill`. The plain step list inside it
is what readers see without JavaScript, or if the diagram fails to load.

The bundle is not committed. The workflow installs Node and runs
`npm ci` and `npm run build:viewer` in `web/` before `mkdocs build --strict`.
A change to the diagram code therefore reaches the site with the next
build, with no change to `docs/data`.

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

Build the diagram viewer once with Node 24, then serve the site:

```bash
pip install -e ".[docs]"
cd web && npm ci && npm run build:viewer && cd ..
mkdocs serve
```

then open <http://127.0.0.1:8000/>. Without the viewer build the pages still
load, but every diagram shows only its plain step list. When working on the
diagram code, run `npm run dev:viewer` in `web/` instead, which rebuilds
the bundle on every change; `mkdocs serve` picks up the new files.
`mkdocs build --strict` is what the workflow runs; it fails on any broken
link.
