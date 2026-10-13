# Installation

The recipe manager needs Python 3.10 or newer. It installs the `aa-recipe`
command.

## Google Cloud Workstations

This clones the repository, creates a Conda environment, installs the recipe
manager with every library the built-in steps use, and registers the
environment as a Jupyter kernel:

```bash
cd ~ && \
git clone https://github.com/BLayman-NOAA/AA-SI_recipe_manager.git && \
cd AA-SI_recipe_manager && \
source "$(conda info --base)/etc/profile.d/conda.sh" && \
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/main && \
conda tos accept --override-channels --channel https://repo.anaconda.com/pkgs/r && \
conda create -y -n recipe-manager python=3.12 && \
conda activate recipe-manager && \
python -m pip install --upgrade pip && \
python -m pip install -e ".[all-builtin-specs]" && \
python -m ipykernel install --user --name recipe-manager --display-name "Python (recipe-manager)"
```

Then select **Python (recipe-manager)** as the notebook kernel. If
`conda tos accept` is not available in your Conda installation, skip those two
lines.

## Any other machine

```bash
git clone https://github.com/BLayman-NOAA/AA-SI_recipe_manager.git
cd AA-SI_recipe_manager
python -m pip install -e ".[all-builtin-specs]"
```

`all-builtin-specs` installs the libraries the built-in steps call: echopype,
and the AA-SI utilities, calibration, machine learning, visualization and
EchogramGL packages, plus `ipykernel` for notebooks. Without it the recipe
manager can still check recipes and generate notebooks, but cannot run the
steps.

## Optional extras

| Extra | Adds | Install |
| --- | --- | --- |
| `gcs` | Reading and writing `gs://` buckets for the cache, outputs and scratch | `pip install -e ".[gcs]"` |
| `dask` | The Dask executor, `--executor dask` | `pip install -e ".[dask]"` |
| `prefect` | The Prefect executor, `--executor prefect` | `pip install -e ".[prefect]"` |
| `docs` | Building this site | `pip install -e ".[docs]"` |
| `dev` | Tests, linting and type checking | `pip install -e ".[dev]"` |

Extras combine: `pip install -e ".[all-builtin-specs,gcs,dask]"`.

For `gs://` storage, credentials come from Application Default Credentials:

```bash
gcloud auth application-default login
```

## Check the install

```bash
aa-recipe --version
aa-recipe doctor
```

`doctor` reports the installed version of every AA-SI package, the machine's
memory, and whether the configured cache and output directories can be
written to. Include its output when reporting a problem.

## An environment for one recipe

A recipe declares the libraries each of its steps needs, so the recipe manager
can build an environment for exactly that recipe:

```bash
aa-recipe env create my_recipe.yaml --path .venv-my-recipe
```

See [Environments](reference/environments.md), including how to rebuild the
environment a past run used from its provenance file.
