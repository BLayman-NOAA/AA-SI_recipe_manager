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

## The client on a Cloud Workstation

The browser client runs its server on the workstation and is opened from your
laptop's browser through a tunnel. Everything comes from GitHub and the package
managers; nothing is copied from another machine.

### Install

In the `recipe-manager` environment from the section above:

```bash
cd ~/AA-SI_recipe_manager && \
git pull && \
conda install -y -c conda-forge "nodejs>=22" && \
python -m pip install -e ".[all-builtin-specs,server,echogram,gcs,dask]" && \
cd web && npm ci && npm run build && cd ..
```

- Node is needed only to build the web client. It goes into the same Conda
  environment, so nothing is installed system wide.
- `npm run build` writes the client into `src/aa_recipe_manager/server/static/`,
  which git does not track, so build it after every pull that changes `web/`.
- `echogram` installs EchogramGL's server half at the commit `pyproject.toml`
  pins. Its browser half is already in the repository, in `web/vendor/`.

Sign in for `gs://` caches, and clone the recipes:

```bash
gcloud auth application-default login
cd ~ && git clone https://github.com/BLayman-NOAA/AA-SI_Full_Pipeline_Example.git
```

### Start the server

Run it in `tmux` (or `screen`) so it keeps running when the terminal closes:

```bash
tmux new -s client
conda activate recipe-manager
aa-recipe serve --recipe-root ~/AA-SI_Full_Pipeline_Example/example_recipes
```

It listens on `127.0.0.1:8765`, holds its sessions in `~/.aa-recipe/client`,
and finds each recipe's run config the way `aa-recipe run` does, starting from
the recipe's own folder. Detach with `Ctrl-b d`; `tmux attach -t client`
returns to it.

### Open it from your laptop

On the laptop, open a tunnel to the workstation's port 8765, then browse to
`http://localhost:8765`:

```bash
gcloud workstations start-tcp-tunnel \
  --project=PROJECT --region=REGION --cluster=CLUSTER --config=CONFIG \
  WORKSTATION 8765 --local-host-port=localhost:8765
```

If you reach the workstation over plain SSH instead, the same tunnel is
`ssh -L 8765:127.0.0.1:8765 <workstation>`.

- **Use the same port on both ends.** The server answers only requests addressed
  to `localhost:8765` or `127.0.0.1:8765`; through a tunnel from another local
  port every request gets `403 forbidden_host`. If 8765 is taken on the laptop,
  start the server with `--port` set to a free port and tunnel that port to the
  same number.
- **Open it as `localhost`**, not by the workstation's address. The echogram
  draws with WebGPU, which browsers allow only on `https://` pages and on
  `localhost`. Use a current Chrome or Edge.
- **Check it:** Help > About shows the server's version, and the Recipes pane
  lists the recipe folders.

### Update

```bash
cd ~/AA-SI_recipe_manager && git pull && \
python -m pip install -e ".[all-builtin-specs,server,echogram,gcs,dask]" && \
python -m pip install --force-reinstall --no-deps \
  git+https://github.com/BLayman-NOAA/AA-SI_Utils.git \
  git+https://github.com/BLayman-NOAA/AA-SI_calibration.git \
  git+https://github.com/BLayman-NOAA/AA-SI_ML.git \
  git+https://github.com/BLayman-NOAA/AA-SI_Visualization.git \
  "$(grep -o 'aa-si-echogram-gl\[server\] @ git+[^"]*' pyproject.toml)" && \
cd web && npm ci && npm run build && cd ..
```

The AA-SI libraries are installed from their main branches without a version
pin, so pip does not see a newer commit as an upgrade; `--force-reinstall
--no-deps` takes the latest, and the last line reinstalls EchogramGL at the
commit `pyproject.toml` now pins. Then stop the server (`Ctrl-c` in its `tmux`
window) and start it again: it reads the op specs once, at start.

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
| `server` | The browser client's server, `aa-recipe serve` | `pip install -e ".[server]"` |
| `echogram` | The client's echogram pane (EchogramGL's server routes) | `pip install -e ".[echogram]"` |
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
