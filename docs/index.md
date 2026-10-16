# AA-SI Recipe Manager

The recipe manager turns an active acoustics processing pipeline into a single
YAML file, a **recipe**, that anyone can read, share, check and run. A recipe
lists the steps of the pipeline, how data flows from one step to the next, and
every parameter each step uses. The recipe manager can then:

- **check it** without running anything (`aa-recipe dry-run`), including a
  diagram of the pipeline;
- **run it** directly, on one machine or spread across a Dask cluster, with
  every step's result cached so an interrupted run resumes where it stopped;
- **turn it into a Jupyter notebook or Python script** (`aa-recipe generate`)
  you can step through and edit;
- **record what it did**: the inputs, the resolved parameters and the exact
  package versions, so a result can be reproduced later.

A recipe contains no processing code. Each step names an **op**, a built-in
operation such as `compute_sv` or `remove_background_noise`, and the recipe
manager maps it to the library function that does the work: echopype, the
AA-SI utilities, the calibration package, and others. See the
[op reference](ops/index.md) for all of them.

## Where to start

<div class="grid cards" markdown>

- **[Installation](installation.md)**

    Install the package and the libraries its steps use.

- **[Quickstart](quickstart.md)**

    Fetch a few files from NCEI, compute Sv and plot an echogram with a
    five-step recipe.

- **[Processing levels](levels/index.md)**

    How the recipe manager implements each level of the AA-SI data road map,
    with each level drawn as an interactive flow diagram.

- **[Calibration](calibration/index.md)**

    The calibration pipeline: from manufacturer calibration files to the
    calibration applied at level 2.

</div>

## How the site is organized

[Concepts](concepts/recipes.md) explains the recipe format: steps, how they are
wired together, sub-recipes, per-file fan-out and caching.

[Processing levels](levels/index.md) follows the
[AA-SI data road map](https://github.com/nmfs-ost/AA-SI_DataRoadMap). Each level
has a page showing the sub-recipe that implements it for two example surveys,
HB1603 and HB2407, as an interactive diagram you can drill into, search and
inspect step by step, as YAML, and as the notebook the recipe manager
generates from it.

[Op reference](ops/index.md) documents every built-in step, with links to its
source code.

[Reference](reference/cli.md) covers the command line, executors, config files
and environments.
