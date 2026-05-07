# aa-recipe-manager

A Python package for defining, sharing, generating, and executing standardized scientific workflow recipes.

A recipe is a YAML file that describes a complete data processing pipeline as a directed acyclic graph (DAG) of steps, along with all the inputs needed to reproduce it. The package sits between the scientist and the code: it does not replace any existing library, but provides a thin structured layer that references existing libraries, maps parameters, and produces runnable artifacts (notebooks, scripts, background jobs) from a single declarative source of truth.

## Features

- **Recipe files** (YAML) capture pipeline structure, steps, dependencies, and parameters without containing implementation code
- **Step registry** defines scientific specifications for each operation along with implementation mappings to real functions
- **Code generation** produces Jupyter notebooks or Python scripts from a recipe
- **Direct execution** runs the DAG as a background process, optionally using Dask or Prefect
- **Hybrid mode** executes early steps directly, then generates interactive code for the rest
- **Round trip** captures parameters from an interactive session back to a recipe file

## Installation

```bash
# Clone the repository
git clone https://github.com/nmfs-ost/AA-SI_recipe_manager.git
cd AA-SI_recipe_manager

# Install in development mode
pip install -e ".[dev]"

# Set up pre-commit hooks
pre-commit install
```

Optional extras for distributed execution:

```bash
pip install -e ".[dask]"     # Dask executor
pip install -e ".[prefect]"  # Prefect executor
```

## Usage

```bash
# Validate a recipe
aa-recipe-manager dry-run my_recipe.yaml

# Generate a Jupyter notebook
aa-recipe-manager generate my_recipe.yaml

# Generate a Python script
aa-recipe-manager generate my_recipe.yaml --format script

# Run a pipeline directly
aa-recipe-manager run my_recipe.yaml --input raw_folder=/data/survey
```

Python API:

```python
from aa_recipe_manager import api

api.generate("my_recipe.yaml", output_format="script")
api.generate("my_recipe.yaml", output_format="notebook")
```

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
