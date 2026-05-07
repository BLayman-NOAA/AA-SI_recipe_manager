# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""
aa_recipe_manager: define, share, generate, and execute standardized
scientific workflow recipes.
"""

from importlib.metadata import PackageNotFoundError, version
from typing import Any

try:
    __version__ = version("aa-recipe-manager")
except PackageNotFoundError:
    __version__ = "0.0.0.dev"


def export_schema() -> dict[str, Any]:
    """Return the JSON Schema for the Recipe model.

    Generated from the Pydantic model definitions. Useful for validating
    raw recipe dicts before constructing a Recipe object.
    """
    from aa_recipe_manager.model.types import Recipe

    return Recipe.model_json_schema()


def load_recipe(path):
    """Parse a YAML recipe file into a Recipe object.

    Thin re-export of ``aa_recipe_manager.parser.yaml_reader.load_recipe``.
    Raises ``RecipeParseError`` on file/YAML/validation failures.
    """
    from aa_recipe_manager.parser.yaml_reader import load_recipe as _load

    return _load(path)


def generate(dag, output_path, backend="notebook", options=None):
    """Generate code (default: Jupyter notebook) from a validated PipelineDAG.

    Parameters
    ----------
    dag:
        A PipelineDAG produced by ``build_dag()``.
    output_path:
        Destination path for the generated file.
    backend:
        Code generation backend name. Built-in backends are ``"notebook"``
        and ``"script"``.
    options:
        Backend-specific options dict. For the notebook backend accepted keys
        are ``recipe_path`` (str, embedded in the tracker init cell) and
        ``save_recipe_output`` (str, filename passed to ``tracker.save_recipe()``).

    Returns the resolved output path as a ``pathlib.Path``.
    """
    from aa_recipe_manager.generator.core import CodeGenerator
    from aa_recipe_manager.registry.loader import load_builtin_registry

    registry = load_builtin_registry()
    gen = CodeGenerator(registry)
    return gen.generate(dag, output_path, backend=backend, options=options)


def dry_run(recipe, *, inputs=None, visualize=False, check_versions=True):
    """Validate a recipe and return a DryRunReport without executing or generating code.

    Parameters
    ----------
    recipe:
        Recipe file path (str or Path) or pre-loaded Recipe object.
    inputs:
        Optional dict of pipeline-level input values for path-existence checks.
    visualize:
        If True, include a Mermaid DAG diagram string in the report.
    check_versions:
        If True, verify installed library versions against implementation declarations.

    Returns a DryRunReport. Never raises; errors are captured inside the report.
    """
    from aa_recipe_manager.api import dry_run as _dry_run

    return _dry_run(recipe, inputs=inputs, visualize=visualize, check_versions=check_versions)


def export_dependencies(recipe, *, format="text", output=None):
    """Resolve and export all implementation dependencies for a recipe.

    Parameters
    ----------
    recipe:
        Recipe file path (str or Path) or pre-loaded Recipe object.
    format:
        One of "text" (default), "requirements", "conda", or "pyproject".
    output:
        If provided, write the result to this file path and return the Path.
        Otherwise return the result as a string.
    """
    from aa_recipe_manager.api import export_dependencies as _export_dependencies

    return _export_dependencies(recipe, format=format, output=output)


def create_env(recipe, env_path, *, python=None, inputs=None, local_overrides=None):
    """Create a virtual environment with dependencies declared by a recipe.

    Parameters
    ----------
    recipe:
        Recipe file path (str or Path) or pre-loaded Recipe object.
    env_path:
        Filesystem path for the new virtual environment.
    python:
        Python executable used to create the environment. Defaults to the
        currently running interpreter.
    inputs:
        Optional pipeline-level input values used to resolve path references
        during DAG construction.
    local_overrides:
        Map of package name to local filesystem path. Named packages are
        installed as editable installs instead of from PyPI.
    """
    from aa_recipe_manager.api import create_env as _create_env

    return _create_env(
        recipe, env_path, python=python, inputs=inputs, local_overrides=local_overrides
    )


__all__ = [
    "__version__",
    "export_schema",
    "load_recipe",
    "generate",
    "dry_run",
    "export_dependencies",
    "create_env",
]
