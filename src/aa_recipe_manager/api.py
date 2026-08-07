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

from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
    RecipeParseError,
    RecipeValidationError,
    SpecNotFoundError,
)
from aa_recipe_manager.executor.checkpoint import PROVENANCE_DIR
from aa_recipe_manager.storage import StorageLocation
from aa_recipe_manager.validation import DryRunEngine, DryRunReport

if TYPE_CHECKING:
    from collections.abc import Sequence

    from aa_recipe_manager.model.types import CheckpointMode, PipelineDAG, Recipe


@dataclass
class EnvCreateResult:
    """Result of create_env()."""

    env_path: Path
    installed: list[str] = field(default_factory=list)
    skipped_local: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class OpDocsResult:
    """Result of export_op_docs()."""

    html: str
    output: Path | None = None
    counts: dict[str, int] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    stale_links: list[str] = field(default_factory=list)


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


def export_op_docs(
    output: str | Path | None = None,
    *,
    resolve_sources: bool = True,
    registry_files: Sequence[str | Path] = (),
) -> OpDocsResult:
    """Generate the single-file HTML reference for the op registry.

    Args:
        output: Path to write the page to. When None the HTML is only
            returned.
        resolve_sources: Import each implementation's package to attach a
            source link, signature, and docstring. Importing the scientific
            packages is slow, so pass False for a fast, import-free page.
        registry_files: Extra spec YAML files to document alongside the
            built-in registry.

    Returns:
        An OpDocsResult carrying the HTML, the written path, and a summary of
        how many ops and source links the page ended up with.
    """
    from aa_recipe_manager.docs.html import render_html
    from aa_recipe_manager.docs.payload import (
        build_payload,
        stale_link_ops,
        unresolved_ops,
    )
    from aa_recipe_manager.registry.loader import (
        load_builtin_registry,
        load_registry_file,
    )

    registry = load_builtin_registry()
    for path in registry_files:
        load_registry_file(path, registry)

    payload = build_payload(registry, resolve_sources=resolve_sources)
    html = render_html(payload)

    written: Path | None = None
    if output is not None:
        written = Path(output)
        written.write_text(html, encoding="utf-8")

    return OpDocsResult(
        html=html,
        output=written,
        counts=payload["counts"],
        unresolved=unresolved_ops(payload),
        stale_links=stale_link_ops(payload),
    )


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
    user_cache_dir: str | Path | None = None,
    implementation_override: str | None = None,
    force: bool = False,
    no_checkpoints: bool = False,
    skip_sinks: bool = False,
    regenerate: str = "auto",
    outputs_dir: str | Path | None = None,
    temp_dir: str | Path | None = None,
    log_destination: str = "file",
    checkpoint_mode: CheckpointMode | str | None = None,
    checkpoint_steps: list[str] | None = None,
    checkpoint_format: str | None = None,
    storage_options: dict[str, Any] | None = None,
    survey_cache_dir: str | Path | None = None,
    cache_write_tier: str = "user",
    save_provenance: str | Path | None = None,
    progress: Any = None,
    executor_options: dict[str, Any] | None = None,
    keep_temp: bool = False,
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
        Executor backend name: ``"sequential"`` (default, in-process),
        ``"dask"`` (distributed; requires the ``dask`` extra), or ``"prefect"``
        (requires the ``prefect`` extra). When left ``"sequential"`` the
        recipe's ``execution.executor`` field is used if set.
    executor_options:
        Backend constructor options, e.g. ``{"scheduler": "processes",
        "n_workers": 4}`` for Dask. Ignored by the sequential backend.
    user_cache_dir:
        Directory used for step-output checkpoints. When ``None`` no checkpoint
        files are written; explicit checkpoint options require a non-None
        ``user_cache_dir``.
    implementation_override:
        Force a single implementation key for every step.
    force:
        Re-run every step even if a valid checkpoint exists.
    no_checkpoints:
        Skip both checkpoint reads and writes for this run.
    skip_sinks:
        Skip steps marked ``sink: true`` in their spec.
    regenerate:
        Artifact-regeneration mode. ``"auto"`` (default) honors each step's
        ``regenerate`` attribute (``if-missing`` / ``always`` / ``never``);
        ``"off"`` ignores those attributes and regenerates nothing; ``"sinks"``
        forces every sink step to re-run (cheap — upstream loads from cache);
        ``"all"`` forces every artifact-emitting step, including data steps
        (which recompute, since the artifact is a side effect of running). Use
        this when a cache was reused without its on-disk artifacts so the
        plots/logs are regenerated locally.
    outputs_dir:
        Directory for user-facing outputs (images under ``outputs_dir/images``
        and logs under ``outputs_dir/logs/standard_out.txt``). When ``None``
        it defaults to a sibling of ``user_cache_dir`` named ``outputs`` (e.g.
        ``recipe_cache`` -> ``outputs``). May be a local path or an fsspec URL
        (``gs://...``).
    temp_dir:
        Run-scoped scratch directory (``exe_temp``) for per-step intermediate
        stores. When ``None`` it follows the cache: a sibling of ``user_cache_dir``
        named ``exe_temp`` under the same scheme. May be a local path or an
        fsspec URL; remote scratch requires zarr intermediates (NetCDF cannot
        be written to object storage).
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
        ``execution.checkpoint_format`` setting when provided. ``"netcdf"`` is
        rejected for a remote (``gs://``) ``user_cache_dir``.
    storage_options:
        fsspec storage options applied to every remote (URL) storage location
        (cache, exe_temp, outputs) and to remote *data input* paths: ops read
        the dict from the execution context to access ``gs://`` input folders
        and files, and remote-input cache fingerprinting authenticates with it.
        For Google Cloud Storage these are usually left ``None`` so gcsfs picks
        up Application Default Credentials.
    survey_cache_dir:
        Root of the shared (curated) survey cache tier. When set, cache reads
        probe ``[user, survey]`` in order (the user tier is ``user_cache_dir``);
        writes still go only to the user tier unless ``cache_write_tier`` says
        otherwise. Requires ``user_cache_dir``.
    cache_write_tier:
        ``"user"`` (default) or ``"survey"``. ``"survey"`` marks this a
        *curated* run: it reads and writes only the survey tier (side-effect
        markers still go to the user tier) and rejects pickle-format
        artifacts. Access control is enforced by bucket IAM, not this client.
    save_provenance:
        If provided, write the captured provenance as YAML at this path. When
        ``user_cache_dir`` is set and this is None, a default sidecar named
        ``provenance.yaml`` is written under ``user_cache_dir/other``.

    Returns the :class:`ExecutionResult`.
    """
    from aa_recipe_manager.executor import resolve_executor

    if no_checkpoints and (checkpoint_mode or checkpoint_steps):
        raise ValueError(
            "no_checkpoints=True cannot be combined with checkpoint_mode or "
            "checkpoint_steps; pick one."
        )
    if no_checkpoints and (survey_cache_dir or cache_write_tier != "user"):
        raise ValueError(
            "no_checkpoints=True cannot be combined with survey_cache_dir or "
            "cache_write_tier; the tiered cache requires checkpointing."
        )
    if user_cache_dir is None and not no_checkpoints and (
        checkpoint_steps
        or (checkpoint_mode is not None and checkpoint_mode != "none")
    ):
        raise ValueError(
            "checkpoint_mode / checkpoint_steps require user_cache_dir; pass an "
            "user_cache_dir or set no_checkpoints=True."
        )

    dag = _load_dag(
        recipe,
        input_values=inputs,
        implementation_override=implementation_override,
        check_versions=False,
    )

    # Recipe-declared executor is the lowest-priority default; the explicit
    # ``executor`` argument (from CLI / API) wins when it is non-default.
    effective_executor = executor
    if executor == "sequential" and dag.recipe.execution is not None:
        effective_executor = dag.recipe.execution.executor or "sequential"
    impl = resolve_executor(effective_executor, **(executor_options or {}))

    result = impl.execute(
        dag,
        inputs=inputs,
        user_cache_dir=user_cache_dir,
        force=force,
        no_checkpoints=no_checkpoints,
        skip_sinks=skip_sinks,
        regenerate=regenerate,
        outputs_dir=outputs_dir,
        temp_dir=temp_dir,
        log_destination=log_destination,
        checkpoint_mode=checkpoint_mode,
        checkpoint_steps=checkpoint_steps,
        checkpoint_format=checkpoint_format,
        storage_options=storage_options,
        survey_cache_dir=survey_cache_dir,
        cache_write_tier=cache_write_tier,
        progress=progress,
        keep_temp=keep_temp,
    )

    sidecar_loc: StorageLocation | None = None
    if save_provenance is not None:
        sidecar_loc = StorageLocation.parse(save_provenance, storage_options)
    elif result.outputs_dir is not None:
        sidecar_loc = (
            StorageLocation.parse(result.outputs_dir, storage_options)
            / PROVENANCE_DIR
            / "provenance.yaml"
        )
    if sidecar_loc is not None and result.provenance is not None:
        sidecar_loc.parent.mkdir()
        _write_provenance_sidecar(result.provenance, sidecar_loc)

    return result


def execute_batch(
    recipe: str | Path | Recipe,
    input_sets: list[Any],
    *,
    executor: str = "sequential",
    executor_options: dict[str, Any] | None = None,
    user_cache_dir: str | Path | None = None,
    outputs_dir: str | Path | None = None,
    implementation_override: str | None = None,
    storage_options: dict[str, Any] | None = None,
    progress: Any = None,
    **execute_kwargs: Any,
) -> Any:
    """Run a recipe once per input set (UC-6), sharing one checkpoint cache.

    ``input_sets`` is a list of :class:`~aa_recipe_manager.executor.batch.
    InputSet` (build them with ``input_sets_from_folder`` / ``_from_csv`` /
    ``_from_lists``). The DAG is resolved once; each set runs against the shared
    ``user_cache_dir`` cache with its own ``outputs_dir/<label>/`` tree, so work
    common to several sets is computed once. Returns a ``BatchResult``.
    """
    from aa_recipe_manager.executor import BatchExecutor, resolve_executor

    def _dag_factory(set_inputs: dict[str, Any]):
        # Rebuild per set: ``${inputs.x}`` params are baked into each step's
        # cache fingerprint at build time, so each set must build its own DAG
        # for its input-dependent steps to address distinct cache entries.
        return _load_dag(
            recipe,
            input_values=set_inputs or None,
            implementation_override=implementation_override,
            check_versions=False,
        )

    impl = resolve_executor(executor, **(executor_options or {}))
    return BatchExecutor(impl).execute_batch(
        _dag_factory,
        input_sets,
        user_cache_dir=user_cache_dir,
        outputs_dir=outputs_dir,
        storage_options=storage_options,
        progress=progress,
        **execute_kwargs,
    )


def _write_provenance_sidecar(provenance: Any, loc: StorageLocation) -> None:
    """Serialize a Provenance object to YAML at a local or remote location."""
    from aa_recipe_manager.provenance.recorder import to_yaml

    loc.write_text(to_yaml(provenance))


def clean(
    recipe: str | Path | Recipe,
    user_cache_dir: str | Path,
    *,
    inputs: dict[str, Any] | None = None,
    mode: str = "intermediate",
    dry_run: bool = False,
    storage_options: dict[str, Any] | None = None,
) -> list[StorageLocation]:
    """Remove checkpoint files for a recipe under ``user_cache_dir``.

    ``user_cache_dir`` may be a local path or an fsspec URL (``gs://...``). Modes are
    ``"intermediate"`` (default), ``"all"``, and ``"stale"``. When ``dry_run`` is
    true the locations that would be deleted are returned without being removed.
    ``storage_options`` authenticates remote cache access and remote-input
    fingerprinting, mirroring :func:`execute`.
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
        user_cache_dir,
        compute_step_hashes(dag, inputs or {}, storage_options=storage_options),
        storage_options=storage_options,
    )
    return manager.clean(dag, mode=mode, dry_run=dry_run)  # type: ignore[arg-type]


def explain_cache(
    recipe: str | Path | Recipe,
    *,
    inputs: dict[str, Any] | None = None,
    user_cache_dir: str | Path,
    survey_cache_dir: str | Path | None = None,
    storage_options: dict[str, Any] | None = None,
) -> Any:
    """Explain, per step, why the recipe would hit or miss the cache tiers.

    Recomputes the recipe's current step fingerprints, probes the user (and
    optional survey) cache roots for each hash, and — for misses — locates the
    nearest stored entry with the same step id and reports exactly which
    fingerprint field(s) diverged (a param value, an input checksum, the
    cache epoch, an upstream change, ...).

    Returns a :class:`aa_recipe_manager.explain.CacheExplanation` with a
    ``format_text()`` human rendering and a ``to_dict()`` JSON form.
    """
    from aa_recipe_manager.explain import explain_cache as _explain

    dag = _load_dag(recipe, input_values=inputs, check_versions=False)
    return _explain(
        dag,
        inputs=inputs,
        user_cache_dir=user_cache_dir,
        survey_cache_dir=survey_cache_dir,
        storage_options=storage_options,
    )
