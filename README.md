# aa-recipe-manager

A Python package for defining, sharing, generating, and executing standardized scientific workflow recipes.

The package installs the CLI as `aa-recipe`. The legacy `aa-recipe-manager`
command remains available as a compatibility alias.

A recipe is a YAML file that describes a complete data processing pipeline as a directed acyclic graph (DAG) of steps, along with all the inputs needed to reproduce it. The package sits between the scientist and the code: it does not replace any existing library, but provides a thin structured layer that references existing libraries, maps parameters, and produces runnable artifacts (notebooks, scripts, background jobs) from a single declarative source of truth.

## Features

- **Recipe files** (YAML) capture pipeline structure, steps, dependencies, and parameters without containing implementation code
- **Step registry** defines scientific specifications for each operation along with implementation mappings to real functions
- **Code generation** produces Jupyter notebooks or Python scripts from a recipe
- **Direct execution** runs the DAG in process with the sequential executor, checkpoint/resume, and progress reporting
- **Hybrid mode** (planned) executes early steps directly, then generates interactive code for the rest
- **Round trip** captures parameters from an interactive session back to a recipe file

## Installation

### Google Cloud Workstations

Run the following command in a terminal to clone the repository, create a Conda
environment, install the recipe manager with all built-in recipe dependencies,
and register the environment as a Jupyter kernel:

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

After installation, select **Python (recipe-manager)** as the notebook kernel.
If `conda tos accept` is not available in your Conda installation, skip those
two lines and rerun the command starting at `conda create`.

### Development Install

```bash
# Clone the repository
git clone https://github.com/BLayman-NOAA/AA-SI_recipe_manager.git
cd AA-SI_recipe_manager

# Install in development mode
pip install -e ".[dev]"

# Set up pre-commit hooks
pre-commit install
```

Optional extras reserved for planned distributed execution backends:

```bash
pip install -e ".[dask]"     # planned Dask executor
pip install -e ".[prefect]"  # planned Prefect executor
```

## Usage

```bash
# Validate a recipe
aa-recipe dry-run my_recipe.yaml

# Generate a Jupyter notebook
aa-recipe generate my_recipe.yaml

# Generate a Python script
aa-recipe generate my_recipe.yaml --format script

# Run a pipeline directly with checkpoint/resume
aa-recipe run my_recipe.yaml --input raw_folder=/data/survey --output-dir ./outputs

# Keep only selected checkpoint save points
aa-recipe run my_recipe.yaml --checkpoint-mode explicit --checkpoint calibrated_sv
```

Python API:

```python
from aa_recipe_manager import api

api.generate("my_recipe.yaml", format="script")
api.generate("my_recipe.yaml", format="notebook")
api.execute("my_recipe.yaml", output_dir="./outputs")
```

### Google Cloud Storage (gs://) storage

The three run storage locations can each live on a GCS bucket instead of local
disk — useful on Cloud Workstations whose disk cannot hold a full survey. Each
is independent and defaults to local; a location goes remote only when you pass
a `gs://` URL.

```bash
pip install "aa-recipe-manager[gcs]"          # adds gcsfs
gcloud auth application-default login          # credentials (ADC)

# Checkpoint cache + outputs on the bucket; scratch stays local (default).
aa-recipe run my_recipe.yaml \
    --output-dir gs://my-bucket/surveys/HB1603/recipe_cache \
    --outputs-dir gs://my-bucket/surveys/HB1603/outputs

# Big survey that won't fit on disk: put exe_temp on the bucket too.
aa-recipe run my_recipe.yaml \
    --output-dir gs://my-bucket/surveys/HB1603/recipe_cache \
    --temp-dir   gs://my-bucket/surveys/HB1603/exe_temp
```

```python
api.execute(
    "my_recipe.yaml",
    output_dir="gs://my-bucket/surveys/HB1603/recipe_cache",
    temp_dir="gs://my-bucket/surveys/HB1603/exe_temp",
    outputs_dir="gs://my-bucket/surveys/HB1603/outputs",
)
```

Notes:
- `--temp-dir`/`--outputs-dir` default to siblings of `--output-dir` under the
  same scheme, so a remote cache implies remote scratch/outputs unless you
  override them. Remote scratch trades local disk for extra bucket traffic
  (intermediates are written, read once by the combine step, then deleted);
  pass a local `--temp-dir` to keep scratch on disk when it fits.
- Credentials come from Application Default Credentials — never put them in a
  recipe. Run the workstation in the bucket's region for speed and no egress.
- A remote checkpoint cache must use `--checkpoint-format zarr` (the default);
  NetCDF requires seekable local writes.
- Generated notebooks/scripts assume local paths; remote storage applies to
  `aa-recipe run` / `api.execute`.

## Development

```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=aa_recipe_manager

# Lint and format
ruff check src/ tests/
ruff format src/ tests/

# Type check
mypy src/aa_recipe_manager
```

## License

Apache License 2.0. See [LICENSE](LICENSE) for details.

## Disclaimer

This repository is a scientific product and is not official communication of the National Oceanic and Atmospheric Administration, or the United States Department of Commerce. All NOAA GitHub project code is provided on an 'as is' basis and the user assumes responsibility for its use. Any claims against the Department of Commerce or Department of Commerce bureaus stemming from the use of this GitHub project will be governed by all applicable Federal law. Any reference to specific commercial products, processes, or services by service mark, trademark, manufacturer, or otherwise, does not constitute or imply their endorsement, recommendation or favoring by the Department of Commerce. The Department of Commerce seal and logo, or the seal and logo of a DOC bureau, shall not be used in any manner to imply endorsement of any commercial product or activity by DOC or the United States Government.
