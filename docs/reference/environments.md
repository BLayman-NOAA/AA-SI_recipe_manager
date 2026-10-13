# Environments

Each op's spec declares the package and version range its implementation
needs, so the recipe manager knows exactly what a recipe depends on.

## List a recipe's dependencies

```bash
aa-recipe deps my_recipe.yaml                                  # readable list
aa-recipe deps my_recipe.yaml --format requirements -o requirements.txt
aa-recipe deps my_recipe.yaml --format conda
aa-recipe deps my_recipe.yaml --format pyproject
```

## Build an environment for a recipe

```bash
aa-recipe env create my_recipe.yaml --path .venv-my-recipe
```

creates a virtual environment with the recipe manager and every package the
recipe's steps call. `--python` picks the interpreter, and
`--local-pkg NAME=PATH` installs a package from a local checkout instead, for
example while developing it:

```bash
aa-recipe env create my_recipe.yaml --path .venv-dev \
  --local-pkg aa-si-utils=../AA-SI_Utils
```

## Rebuild the environment a run used

Every run's [provenance file](../concepts/provenance.md) records the exact
version of each package it called. Giving that file instead of a recipe
installs those exact versions:

```bash
aa-recipe env create outputs/provenance/provenance.yaml --path .venv-replay
```

That environment, the recipe and the run's inputs together reproduce the run.
