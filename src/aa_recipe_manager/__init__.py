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
    "execute",
    "clean",
]


def execute(
    recipe,
    *,
    inputs=None,
    executor="sequential",
    output_dir=None,
    implementation_override=None,
    force=False,
    no_checkpoints=False,
    skip_sinks=False,
    regenerate_outputs=False,
    outputs_dir=None,
    log_destination="file",
    save_provenance=None,
    progress=None,
):
    """Execute a recipe's DAG directly in the current process.

    Thin re-export of :func:`aa_recipe_manager.api.execute`. Returns the
    :class:`ExecutionResult`.
    """
    from aa_recipe_manager.api import execute as _execute

    return _execute(
        recipe,
        inputs=inputs,
        executor=executor,
        output_dir=output_dir,
        implementation_override=implementation_override,
        force=force,
        no_checkpoints=no_checkpoints,
        skip_sinks=skip_sinks,
        regenerate_outputs=regenerate_outputs,
        outputs_dir=outputs_dir,
        log_destination=log_destination,
        save_provenance=save_provenance,
        progress=progress,
    )


def clean(recipe, output_dir, *, inputs=None, mode="intermediate", dry_run=False):
    """Remove checkpoint files for ``recipe`` under ``output_dir``.

    Thin re-export of :func:`aa_recipe_manager.api.clean`.
    """
    from aa_recipe_manager.api import clean as _clean

    return _clean(
        recipe, output_dir, inputs=inputs, mode=mode, dry_run=dry_run
    )
