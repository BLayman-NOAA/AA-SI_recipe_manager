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

from aa_recipe_manager.executor.checkpoint import PROVENANCE_DIR
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
    from aa_recipe_manager.model.types import CheckpointMode, PipelineDAG, Recipe


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

    # Generated notebooks/scripts import aa_recipe_manager itself (for
    # PipelineTracker, ProvenanceRecorder, etc.), and notebooks need
    # ipykernel/ipywidgets to run interactively. env create otherwise only
    # installs the per-step dependencies declared by the recipe, so add these
    # runtime essentials here.
    self_spec = _self_install_spec(local_overrides)
    if self_spec is not None:
        kind, value = self_spec
        if kind == "editable":
            local_pkgs.append(value)
        else:
            regular_pkgs.append(value)
    regular_pkgs.extend(["ipykernel", "ipywidgets"])

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


def _self_install_spec(
    local_overrides: dict[str, str],
) -> tuple[str, str] | None:
    """Return how to install aa-recipe-manager into the new venv.

    Returns ``("editable", path)`` to install from a local source tree, or
    ``("pypi", "aa-recipe-manager==<version>")`` to install from PyPI. Returns
    ``None`` if the user already supplied an override for this package.
    """
    if "aa-recipe-manager" in local_overrides:
        # Caller is handling the install themselves via --local-pkg.
        return None

    import aa_recipe_manager as _self_pkg

    pkg_dir = Path(_self_pkg.__file__).resolve().parent
    # Walk up looking for a pyproject.toml that belongs to aa-recipe-manager.
    for candidate in (pkg_dir.parent, pkg_dir.parent.parent):
        pyproject = candidate / "pyproject.toml"
        if pyproject.is_file():
            try:
                text = pyproject.read_text(encoding="utf-8")
            except OSError:
                continue
            if 'name = "aa-recipe-manager"' in text:
                return ("editable", str(candidate))

    # Fall back to a versioned PyPI install.
    try:
        from importlib.metadata import version as _pkg_version

        return ("pypi", f"aa-recipe-manager=={_pkg_version('aa-recipe-manager')}")
    except Exception:
        return ("pypi", "aa-recipe-manager")


def create_env_from_provenance(
    provenance_path: str | Path,
    env_path: str | Path,
    *,
    python: str | Path | None = None,
    local_overrides: dict[str, str] | None = None,
) -> EnvCreateResult:
    """Create a virtual environment from a saved provenance YAML file.

    Reads the ``resolved_dependencies`` section of a provenance file produced
    by a previous recipe run and installs each package at its recorded (pinned)
    version. This reproduces the exact library environment of that run without
    requiring the original recipe file.

    Parameters
    ----------
    provenance_path:
        Path to a ``provenance.yaml`` file previously written by
        ``aa-recipe-manager run`` or a generated notebook.
    env_path:
        Filesystem path for the new virtual environment.
    python:
        Python executable used to create the environment. Defaults to the
        currently running interpreter. A warning is emitted when the
        interpreter's version does not match ``python_version_number`` recorded
        in the provenance file.
    local_overrides:
        Map of package name to local filesystem path. Named packages are
        installed as editable installs from the given path instead of PyPI.
    """
    import subprocess
    import warnings

    provenance_path = Path(provenance_path)
    env_path = Path(env_path)
    local_overrides = local_overrides or {}

    # Parse the provenance YAML.
    try:
        from ruamel.yaml import YAML as _YAML

        _yaml = _YAML()
        raw: dict[str, Any] = _yaml.load(provenance_path)
    except Exception as exc:
        raise ValueError(
            f"Could not parse provenance file {provenance_path}: {exc}"
        ) from exc

    recorded_version: str | None = raw.get("python_version_number")
    resolved_deps: dict[str, str] = raw.get("resolved_dependencies") or {}

    # Warn if the Python version doesn't match.
    if recorded_version is not None:
        import platform as _platform

        current_version = _platform.python_version()
        if python is None and current_version != recorded_version:
            warnings.warn(
                f"Current Python {current_version} does not match the recorded "
                f"version {recorded_version}. Pass python= to specify the "
                "correct interpreter.",
                UserWarning,
                stacklevel=2,
            )

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

    for pkg_name, pkg_info in resolved_deps.items():
        if pkg_name in local_overrides:
            local_pkgs.append(local_overrides[pkg_name])
            continue

        # Support both the new rich format {installed_version, source, url}
        # and the old flat string format for backward compatibility.
        if isinstance(pkg_info, dict):
            source = pkg_info.get("source", "pypi")
            url = pkg_info.get("url")
            installed_version = pkg_info.get("installed_version", "")
        else:
            # Legacy flat string: just a version string.
            source = "pypi"
            url = None
            installed_version = str(pkg_info) if pkg_info else ""

        if source == "git":
            install_url = url or pkg_name
            regular_pkgs.append(f"git+{install_url}")
        elif source == "local":
            # Local packages without an override path are skipped; the user
            # must supply --local-pkg.
            result.skipped_local.append(pkg_name)
            warnings.warn(
                f"Package {pkg_name!r} is a local dependency and cannot be "
                "installed from PyPI or a git URL. Re-run with "
                f"--local-pkg {pkg_name}=/path/to/{pkg_name}.",
                UserWarning,
                stacklevel=2,
            )
        else:
            spec = (
                f"{pkg_name}=={installed_version}"
                if installed_version and installed_version != "unknown"
                else pkg_name
            )
            regular_pkgs.append(spec)

    # Also install aa-recipe-manager itself and notebook runtime essentials.
    self_spec = _self_install_spec(local_overrides)
    if self_spec is not None:
        kind, value = self_spec
        if kind == "editable":
            local_pkgs.append(value)
        else:
            regular_pkgs.append(value)
    regular_pkgs.extend(["ipykernel", "ipywidgets"])

    if local_pkgs:
        editable_args: list[str] = []
        for pkg_path in local_pkgs:
            editable_args += ["-e", pkg_path]
        subprocess.run(
            [str(python_in_env), "-m", "pip", "install"] + editable_args,
            check=True,
        )
        result.installed.extend(f"-e {p}" for p in local_pkgs)

    # Install PyPI packages individually so that a package not found on PyPI
    # (e.g. a local/private package without a --local-pkg override) does not
    # abort the entire install and leave PyPI packages like echopype uninstalled.
    for spec in regular_pkgs:
        pkg_result = subprocess.run(
            [str(python_in_env), "-m", "pip", "install", spec],
            capture_output=True,
        )
        if pkg_result.returncode == 0:
            result.installed.append(spec)
        else:
            # Strip the version pin and treat as a local package that needs a
            # --local-pkg override. This is the typical case for packages like
            # aa-si-utils that are not published to PyPI.
            pkg_name = spec.split("==")[0]
            result.skipped_local.append(pkg_name)
            warnings.warn(
                f"Could not install {spec!r} from PyPI \u2014 it may be a local "
                "package. Re-run with --local-pkg "
                f"{pkg_name}=/path/to/{pkg_name} to install it.",
                UserWarning,
                stacklevel=2,
            )

    return result


def execute(
    recipe: str | Path | Recipe,
    *,
    inputs: dict[str, Any] | None = None,
    executor: str = "sequential",
    output_dir: str | Path | None = None,
    implementation_override: str | None = None,
    force: bool = False,
    no_checkpoints: bool = False,
    skip_sinks: bool = False,
    regenerate_outputs: bool = False,
    outputs_dir: str | Path | None = None,
    log_destination: str = "file",
    checkpoint_mode: CheckpointMode | str | None = None,
    checkpoint_steps: list[str] | None = None,
    checkpoint_format: str | None = None,
    save_provenance: str | Path | None = None,
    progress: Any = None,
) -> Any:
    """Execute a recipe's pipeline directly in the current process.

    Parameters
    ----------
    recipe:
        Recipe file path or pre-loaded Recipe object.
    inputs:
        Pipeline-level input values, used both for DAG resolution and as
        runtime values for ``${inputs.x}`` references.
    executor:
        Executor backend name. Only ``"sequential"`` is implemented in Stage 6;
        ``"dask"`` and ``"prefect"`` are reserved for Stage 9.
    output_dir:
        Directory used for step-output checkpoints. When ``None`` no checkpoint
        files are written; explicit checkpoint options require a non-None
        ``output_dir``.
    implementation_override:
        Force a single implementation key for every step.
    force:
        Re-run every step even if a valid checkpoint exists.
    no_checkpoints:
        Skip both checkpoint reads and writes for this run.
    skip_sinks:
        Skip steps marked ``sink: true`` in their spec.
    regenerate_outputs:
        Force side-effect steps (sinks / steps with no declared outputs, e.g.
        plotting and logging) to re-run even when their cached marker matches.
        Use this when a checkpoint cache was shared without its on-disk
        artifacts so the plots/logs are regenerated locally. Has no effect on
        steps whose outputs are loaded from a data checkpoint.
    outputs_dir:
        Directory for user-facing outputs (images under ``outputs_dir/images``
        and logs under ``outputs_dir/logs/standard_out.txt``). When ``None``
        it defaults to a sibling of ``output_dir`` named ``outputs`` (e.g.
        ``recipe_cache`` -> ``outputs``).
    log_destination:
        Where per-step stdout/stderr is sent. ``"file"`` (default) writes only
        to ``standard_out.txt``; ``"console"`` returns the captured text for
        display without writing a file; ``"both"`` writes the file and returns
        the text. The captured text is available on ``result.console_log``.
    checkpoint_mode:
        Override the recipe's ``execution.checkpoint_mode``. One of
        ``"explicit"`` (default; only steps marked ``checkpoint: always``),
        ``"eager"`` (save every step), ``"terminal"`` (only leaf steps), or
        ``"none"``. Cannot be combined with ``no_checkpoints``.
    checkpoint_steps:
        Ad-hoc list of step ids to checkpoint regardless of mode. Useful
        for pinning resume points without editing the recipe.
    checkpoint_format:
        Serialization format for checkpoint files. One of ``"zarr"`` (default),
        ``"netcdf"``, or ``"pickle"``. Overrides the recipe's
        ``execution.checkpoint_format`` setting when provided.
    save_provenance:
        If provided, write the captured provenance as YAML at this path. When
        ``output_dir`` is set and this is None, a default sidecar named
        ``provenance.yaml`` is written under ``output_dir/other``.

    Returns the :class:`ExecutionResult`.
    """
    from aa_recipe_manager.executor import SequentialExecutor

    if no_checkpoints and (checkpoint_mode or checkpoint_steps):
        raise ValueError(
            "no_checkpoints=True cannot be combined with checkpoint_mode or "
            "checkpoint_steps; pick one."
        )
    if output_dir is None and not no_checkpoints and (
        checkpoint_steps
        or (checkpoint_mode is not None and checkpoint_mode != "none")
    ):
        raise ValueError(
            "checkpoint_mode / checkpoint_steps require output_dir; pass an "
            "output_dir or set no_checkpoints=True."
        )

    dag = _load_dag(
        recipe,
        input_values=inputs,
        implementation_override=implementation_override,
        check_versions=False,
    )

    if executor == "sequential":
        impl = SequentialExecutor()
    else:
        raise ValueError(
            f"executor backend {executor!r} is not implemented yet "
            "(only 'sequential' is available in Stage 6)"
        )

    result = impl.execute(
        dag,
        inputs=inputs,
        output_dir=output_dir,
        force=force,
        no_checkpoints=no_checkpoints,
        skip_sinks=skip_sinks,
        regenerate_outputs=regenerate_outputs,
        outputs_dir=outputs_dir,
        log_destination=log_destination,
        checkpoint_mode=checkpoint_mode,
        checkpoint_steps=checkpoint_steps,
        checkpoint_format=checkpoint_format,
        progress=progress,
    )

    sidecar_path: Path | None = None
    if save_provenance is not None:
        sidecar_path = Path(save_provenance)
    elif result.outputs_dir is not None:
        sidecar_path = result.outputs_dir / PROVENANCE_DIR / "provenance.yaml"
    if sidecar_path is not None and result.provenance is not None:
        sidecar_path.parent.mkdir(parents=True, exist_ok=True)
        _write_provenance_sidecar(result.provenance, sidecar_path)

    return result


def _write_provenance_sidecar(provenance: Any, path: Path) -> None:
    """Serialize a Provenance object to YAML."""
    from aa_recipe_manager.provenance.recorder import to_yaml

    path.write_text(to_yaml(provenance), encoding="utf-8")


def clean(
    recipe: str | Path | Recipe,
    output_dir: str | Path,
    *,
    inputs: dict[str, Any] | None = None,
    mode: str = "intermediate",
    dry_run: bool = False,
) -> list[Path]:
    """Remove checkpoint files for a recipe under ``output_dir``.

    Modes are ``"intermediate"`` (default), ``"all"``, and ``"stale"``. When
    ``dry_run`` is true the files that would be deleted are returned without
    being removed.
    """
    from aa_recipe_manager.executor import (
        CheckpointManager,
        compute_step_hashes,
    )

    if mode not in {"intermediate", "all", "stale"}:
        raise ValueError(
            f"clean mode must be 'intermediate', 'all', or 'stale'; got {mode!r}"
        )
    dag = _load_dag(recipe, input_values=inputs, check_versions=False)
    manager = CheckpointManager(
        output_dir,
        compute_step_hashes(dag, inputs or {}),
    )
    return manager.clean(dag, mode=mode, dry_run=dry_run)  # type: ignore[arg-type]
