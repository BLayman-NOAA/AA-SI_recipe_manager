# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Python API: user-facing wrappers over core pipeline logic.

Each function accepts a recipe path (str or Path) or a pre-loaded Recipe/PipelineDAG
object, builds the necessary internal objects, and delegates to the appropriate
Layer 2/3 component. No business logic lives here.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.validation import DryRunEngine, DryRunReport
from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
    RecipeParseError,
    RecipeValidationError,
    SpecNotFoundError,
)

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG, Recipe


@dataclass
class EnvCreateResult:
    """Result of create_env()."""

    env_path: Path
    installed: list[str] = field(default_factory=list)
    skipped_local: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _apply_recipe_overrides(
    recipe: Recipe,
    *,
    implementation_override: str | None = None,
) -> Recipe:
    """Return a recipe copy with any requested call-site overrides applied."""
    if implementation_override is None:
        return recipe

    overridden_steps = [
        step.model_copy(update={"implementation_override": implementation_override})
        for step in recipe.steps
    ]
    return recipe.model_copy(update={"steps": overridden_steps})


def _load_dag(
    recipe: str | Path | Recipe,
    *,
    input_values: dict[str, Any] | None = None,
    implementation_override: str | None = None,
    check_versions: bool = False,
) -> PipelineDAG:
    """Build a PipelineDAG from a recipe path or Recipe object."""
    from aa_recipe_manager.model.types import Recipe as RecipeModel
    from aa_recipe_manager.parser.dag_builder import build_dag
    from aa_recipe_manager.parser.yaml_reader import load_recipe
    from aa_recipe_manager.registry.loader import load_builtin_registry

    if isinstance(recipe, (str, Path)):
        loaded = load_recipe(recipe)
    elif isinstance(recipe, RecipeModel):
        loaded = recipe
    else:
        raise TypeError(
            f"recipe must be a path (str or Path) or a Recipe object, got {type(recipe)!r}"
        )

    loaded = _apply_recipe_overrides(
        loaded,
        implementation_override=implementation_override,
    )

    registry = load_builtin_registry()
    return build_dag(loaded, registry, input_values=input_values, check_versions=check_versions)


def load(recipe_path: str | Path) -> PipelineDAG:
    """Parse a recipe file and build a validated PipelineDAG.

    Raises RecipeParseError or RecipeValidationError on failure.
    """
    return _load_dag(recipe_path, check_versions=False)


def generate(
    recipe: str | Path | Recipe,
    *,
    output: str | Path | None = None,
    output_format: str = "notebook",
    overwrite: bool = False,
    include_provenance: bool = True,
    include_tracker: bool = True,
    implementation_override: str | None = None,
    cache_aware: bool = False,
    inputs: dict[str, Any] | None = None,
) -> Path:
    """Generate a Jupyter notebook or Python script from a recipe.

    Parameters
    ----------
    recipe:
        Recipe file path or pre-loaded Recipe object.
    output:
        Output path. Defaults to <recipe_name>.ipynb next to the recipe file
        (or in the current directory when a Recipe object is passed).
    output_format:
        "notebook" (default) or "script". This is the canonical Python API
        keyword for selecting the generated artifact type.
    overwrite:
        Overwrite the output file if it already exists.
    include_provenance:
        Include a provenance cell in the generated output.
    include_tracker:
        Include tracker setup, step wrappers, and recipe save cell in the
        generated output.
    implementation_override:
        Force a specific implementation key for all steps.
    cache_aware:
        Emit cache-aware step cells that check for existing outputs.
    inputs:
        Optional pipeline-level input values used to resolve path references
        during DAG construction. Does not affect the generated inputs cell;
        the recipe's declared defaults are always used there.

    Returns the path to the written output file.
    """
    from aa_recipe_manager.generator.core import CodeGenerator
    from aa_recipe_manager.registry.loader import load_builtin_registry

    recipe_path = Path(recipe) if isinstance(recipe, (str, Path)) else None
    dag = _load_dag(recipe, input_values=inputs, implementation_override=implementation_override, check_versions=False)

    if output is None:
        recipe_name = dag.recipe.name
        ext = ".py" if output_format == "script" else ".ipynb"
        if recipe_path is not None:
            output = recipe_path.with_name(f"{recipe_name}{ext}")
        else:
            output = Path(f"{recipe_name}{ext}")

    output = Path(output)
    if output.exists() and not overwrite:
        raise FileExistsError(
            f"Output file already exists: {output}. Pass overwrite=True to overwrite."
        )

    options: dict[str, Any] = {
        "include_provenance": include_provenance,
        "include_tracker": include_tracker,
        "cache_aware": cache_aware,
    }
    if recipe_path is not None:
        options["recipe_path"] = str(recipe_path)
    if implementation_override is not None:
        options["implementation_override"] = implementation_override

    registry = load_builtin_registry()
    gen = CodeGenerator(registry)
    return gen.generate(dag, output, backend=output_format, options=options)


def dry_run(
    recipe: str | Path | Recipe,
    *,
    inputs: dict[str, Any] | None = None,
    visualize: bool = False,
    check_versions: bool = True,
) -> DryRunReport:
    """Validate a recipe and return a structured DryRunReport.

    No files are written and no pipeline steps are executed. All package-level
    errors are caught and returned as errors inside the DryRunReport; this
    function never raises.
    """
    try:
        dag = _load_dag(recipe, input_values=inputs, check_versions=check_versions)
    except (
        RecipeParseError,
        RecipeValidationError,
        SpecNotFoundError,
        ImplementationNotFoundError,
        AmbiguousImplementationError,
        DependencyVersionError,
        FileNotFoundError,
        TypeError,
    ) as exc:
        recipe_label = str(recipe) if isinstance(recipe, (str, Path)) else "Recipe"
        report = DryRunReport(
            is_valid=False,
            errors=[str(exc)],
            recipe_label=f"Recipe: {recipe_label}",
        )
        return report

    engine = DryRunEngine()
    return engine.run(dag, inputs=inputs, visualize=visualize, check_versions=check_versions)


def export_dependencies(
    recipe: str | Path | Recipe,
    *,
    format: str = "text",
    output: str | Path | None = None,
) -> Path | str:
    """Resolve and export all implementation dependencies for a recipe.

    Parameters
    ----------
    recipe:
        Recipe file path or pre-loaded Recipe object.
    format:
        One of "text", "requirements", "conda", or "pyproject".
    output:
        If provided, write the result to this file path and return the Path.
        Otherwise return the result as a string.
    """
    from aa_recipe_manager.resolver.dependencies import resolve_dependencies

    dag = _load_dag(recipe, check_versions=False)
    resolved = resolve_dependencies(dag)

    if format == "requirements":
        content = resolved.to_requirements_txt()
    elif format == "conda":
        content = resolved.to_conda_env_yml()
    elif format == "pyproject":
        content = resolved.to_pyproject_snippet()
    else:
        lines = [f"Dependencies for recipe '{dag.recipe.name}':"]
        if not resolved.packages:
            lines.append("  (none)")
        else:
            for dep in resolved.packages.values():
                spec = dep.merged_specifier or ""
                src = f"  [{dep.source}]" if dep.source != "pypi" else ""
                steps = ", ".join(dep.requiring_steps)
                lines.append(f"  {dep.name}{spec}{src}  (used by: {steps})")
        content = "\n".join(lines)

    if output is not None:
        output = Path(output)
        output.write_text(content, encoding="utf-8")
        return output

    return content


def export_schema() -> dict[str, Any]:
    """Return the JSON Schema for the Recipe model."""
    from aa_recipe_manager.model.types import Recipe

    return Recipe.model_json_schema()


def create_env(
    recipe: str | Path | Recipe,
    env_path: str | Path,
    *,
    python: str | Path | None = None,
    inputs: dict[str, Any] | None = None,
    local_overrides: dict[str, str] | None = None,
) -> EnvCreateResult:
    """Create a virtual environment with dependencies declared by a recipe.

    Parameters
    ----------
    recipe:
        Recipe file path or pre-loaded Recipe object.
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
        installed as editable installs from the given path instead of PyPI.
        Applies to both PyPI-sourced and local-sourced dependencies.
    """
    import subprocess

    from aa_recipe_manager.resolver.dependencies import resolve_dependencies

    dag = _load_dag(recipe, input_values=inputs, check_versions=False)
    resolved = resolve_dependencies(dag)

    env_path = Path(env_path)
    local_overrides = local_overrides or {}
    result = EnvCreateResult(env_path=env_path)

    python_exe = str(python) if python is not None else sys.executable
    subprocess.run(
        [python_exe, "-m", "venv", str(env_path)],
        check=True,
    )

    if sys.platform == "win32":
        python_in_env = env_path / "Scripts" / "python.exe"
    else:
        python_in_env = env_path / "bin" / "python"

    regular_pkgs: list[str] = []
    local_pkgs: list[str] = []

    for dep in resolved.packages.values():
        if dep.name in local_overrides:
            local_pkgs.append(local_overrides[dep.name])
        elif dep.source == "local":
            if dep.url:
                local_pkgs.append(dep.url)
            else:
                result.skipped_local.append(dep.name)
        elif dep.source == "git":
            url = dep.url or dep.name
            regular_pkgs.append(f"git+{url}")
        else:
            spec = dep.merged_specifier or ""
            regular_pkgs.append(f"{dep.name}{spec}" if spec else dep.name)

    # Install all local (editable) packages in one pip call so pip can resolve
    # their interdependencies (e.g. aa-si-ml depends on aa-si-visualization).
    if local_pkgs:
        editable_args: list[str] = []
        for pkg_path in local_pkgs:
            editable_args += ["-e", pkg_path]
        subprocess.run(
            [str(python_in_env), "-m", "pip", "install"] + editable_args,
            check=True,
        )
        result.installed.extend(f"-e {p}" for p in local_pkgs)

    if regular_pkgs:
        subprocess.run(
            [str(python_in_env), "-m", "pip", "install"] + regular_pkgs,
            check=True,
        )
        result.installed.extend(regular_pkgs)

    return result
