"""CLI entry point for aa-recipe-manager."""

from __future__ import annotations

import json
import logging
import sys
from typing import Any

import click

from aa_recipe_manager import api, config
from aa_recipe_manager.executor.checkpoint import DEFAULT_OUTPUT_ROOT
from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
    PipelineExecutionError,
    RecipeParseError,
    RecipeValidationError,
    SpecNotFoundError,
)


def _fail(message: str) -> None:
    """Print an error message to stderr and exit with code 1."""
    click.echo(f"Error: {message}", err=True)
    sys.exit(1)


def _handle_recipe_errors(exc: Exception) -> None:
    """Format and display known recipe errors, then exit 1."""
    if isinstance(exc, RecipeValidationError):
        click.echo("Recipe validation failed:", err=True)
        for e in exc.errors:
            click.echo(f"  - {e}", err=True)
        if exc.warnings:
            click.echo("Warnings:", err=True)
            for w in exc.warnings:
                click.echo(f"  - {w}", err=True)
    elif isinstance(exc, RecipeParseError):
        click.echo(f"Recipe parse error: {exc}", err=True)
    elif isinstance(exc, SpecNotFoundError):
        click.echo(f"Unknown step operation: {exc}", err=True)
    elif isinstance(exc, (ImplementationNotFoundError, AmbiguousImplementationError)):
        click.echo(f"Implementation error: {exc}", err=True)
    elif isinstance(exc, DependencyVersionError):
        click.echo(f"Dependency version error: {exc}", err=True)
    elif isinstance(exc, PipelineExecutionError):
        click.echo(f"Pipeline execution failed at step {exc.step_id!r}:", err=True)
        click.echo(f"  {exc}", err=True)
        if exc.callable_path:
            click.echo(f"  callable: {exc.callable_path}", err=True)
    elif isinstance(exc, FileExistsError):
        click.echo(f"File already exists: {exc}", err=True)
    elif isinstance(exc, FileNotFoundError):
        click.echo(f"File not found: {exc}", err=True)
    else:
        click.echo(f"Error: {exc}", err=True)
    sys.exit(1)


@click.group()
@click.version_option(package_name="aa-recipe-manager")
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    show_default=True,
)
def main(log_level: str) -> None:
    """aa-recipe-manager: define, share, generate, and execute scientific workflow recipes."""
    logging.basicConfig(level=getattr(logging, log_level))


@main.command("generate")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option("--output", "-o", default=None, help="Output file path.")
@click.option(
    "--format",
    "output_format",
    default="notebook",
    type=click.Choice(["notebook", "script"]),
    show_default=True,
    help="Output format.",
)
@click.option("--implementation", default=None, help="Override implementation key for all steps.")
@click.option("--overwrite", is_flag=True, default=False, help="Overwrite output if it exists.")
@click.option("--no-provenance", is_flag=True, default=False, help="Omit the provenance cell.")
@click.option(
    "--no-tracker",
    is_flag=True,
    default=False,
    help="Omit tracker setup and step wrappers.",
)
@click.option("--cache-aware", is_flag=True, default=False, help="Emit cache-aware step cells.")
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Supply a pipeline-level input value for path resolution (repeatable).",
)
def generate_cmd(
    recipe: str,
    output: str | None,
    output_format: str,
    implementation: str | None,
    overwrite: bool,
    no_provenance: bool,
    no_tracker: bool,
    cache_aware: bool,
    inputs: tuple[str, ...],
) -> None:
    """Generate a Jupyter notebook or Python script from RECIPE."""
    parsed_inputs: dict[str, str] = {}
    for item in inputs:
        if "=" not in item:
            _fail(f"--input value must be in NAME=VALUE format, got: {item!r}")
        name, _, value = item.partition("=")
        parsed_inputs[name.strip()] = value.strip()

    try:
        out = api.generate(
            recipe,
            output=output,
            output_format=output_format,
            overwrite=overwrite,
            include_provenance=not no_provenance,
            include_tracker=not no_tracker,
            implementation_override=implementation,
            cache_aware=cache_aware,
            inputs=parsed_inputs or None,
        )
        click.echo(f"Generated: {out}")
    except Exception as exc:
        _handle_recipe_errors(exc)


@main.command("dry-run")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option("--visualize", is_flag=True, default=False, help="Include a Mermaid DAG diagram.")
@click.option(
    "--check-versions/--no-check-versions",
    default=False,
    show_default=True,
    help="Verify installed library versions against implementation declarations.",
)
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Supply a pipeline-level input value (repeatable).",
)
def dry_run_cmd(
    recipe: str,
    visualize: bool,
    check_versions: bool,
    inputs: tuple[str, ...],
) -> None:
    """Validate RECIPE without executing or generating any artifacts."""
    parsed_inputs: dict[str, str] = {}
    for item in inputs:
        if "=" not in item:
            _fail(f"--input value must be in NAME=VALUE format, got: {item!r}")
        name, _, value = item.partition("=")
        parsed_inputs[name.strip()] = value.strip()

    report = api.dry_run(
        recipe,
        inputs=parsed_inputs or None,
        visualize=visualize,
        check_versions=check_versions,
    )

    click.echo(report.format_text())

    if visualize and report.dag_diagram:
        click.echo("\nDAG Diagram (Mermaid):")
        click.echo(report.dag_diagram)

    if not report.is_valid:
        sys.exit(1)


@main.command("deps")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--format",
    "deps_format",
    default="text",
    type=click.Choice(["text", "requirements", "conda", "pyproject"]),
    show_default=True,
    help="Output format.",
)
@click.option("--output", "-o", default=None, help="Write output to a file instead of stdout.")
def deps_cmd(recipe: str, deps_format: str, output: str | None) -> None:
    """Show or export dependencies for RECIPE."""
    try:
        result = api.export_dependencies(recipe, format=deps_format, output=output)
        if isinstance(result, str):
            click.echo(result)
        else:
            click.echo(f"Written to: {result}")
    except Exception as exc:
        _handle_recipe_errors(exc)


@main.command("schema")
@click.option("--output", "-o", default=None, help="Write schema to a file instead of stdout.")
def schema_cmd(output: str | None) -> None:
    """Export the JSON Schema for recipe files."""
    schema = api.export_schema()
    content = json.dumps(schema, indent=2)
    if output is not None:
        from pathlib import Path
        Path(output).write_text(content, encoding="utf-8")
        click.echo(f"Schema written to: {output}")
    else:
        click.echo(content)


@main.group("env")
def env_group() -> None:
    """Manage virtual environments for recipe dependencies."""


def _parse_local_pkgs(local_pkgs: tuple[str, ...]) -> dict[str, str]:
    parsed: dict[str, str] = {}
    for item in local_pkgs:
        if "=" not in item:
            _fail(f"--local-pkg value must be in NAME=PATH format, got: {item!r}")
        name, _, path = item.partition("=")
        parsed[name.strip()] = path.strip()
    return parsed


def _emit_env_result(result: Any) -> None:
    click.echo(f"Environment created: {result.env_path}")
    if result.installed:
        click.echo("Installed:")
        for pkg in result.installed:
            click.echo(f"  {pkg}")
    if result.skipped_local:
        click.echo(
            "Local packages not found on PyPI (install manually with --local-pkg):",
            err=True,
        )
        for name in result.skipped_local:
            click.echo(f"  --local-pkg {name}=/path/to/{name}", err=True)
    if result.warnings:
        for w in result.warnings:
            click.echo(f"Warning: {w}", err=True)


def _is_provenance_file(path: str) -> bool:
    """Return True if the YAML file looks like a provenance file."""
    from pathlib import Path as _Path

    try:
        from ruamel.yaml import YAML as _YAML

        raw = _YAML().load(_Path(path))
        return isinstance(raw, dict) and "resolved_dependencies" in raw
    except Exception:
        return False


@env_group.command("create")
@click.argument("source", type=click.Path(exists=True, dir_okay=False))
@click.option("--path", "env_path", default=None, help="Path for the virtual environment.")
@click.option("--python", "python_exe", default=None, help="Python executable to use.")
@click.option(
    "--local-pkg",
    "local_pkgs",
    multiple=True,
    metavar="NAME=PATH",
    help="Install a package from a local editable path instead of PyPI (repeatable).",
)
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Supply a pipeline-level input value for path resolution (recipe files only).",
)
def env_create_cmd(
    source: str,
    env_path: str | None,
    python_exe: str | None,
    local_pkgs: tuple[str, ...],
    inputs: tuple[str, ...],
) -> None:
    """Create a virtual environment from a RECIPE or PROVENANCE file.

    SOURCE can be a recipe YAML file or a provenance.yaml produced by a
    previous run. The file type is detected automatically.

    When SOURCE is a provenance file, packages are installed at the exact
    pinned versions recorded in that file. The --input option is not used
    in that case.
    """
    from pathlib import Path as _Path

    parsed_local = _parse_local_pkgs(local_pkgs)

    if env_path is None:
        env_path = f"./{_Path(source).stem}_env"

    try:
        if _is_provenance_file(source):
            if inputs:
                click.echo(
                    "Note: --input is ignored when SOURCE is a provenance file.",
                    err=True,
                )
            result = api.create_env_from_provenance(
                source,
                env_path,
                python=python_exe,
                local_overrides=parsed_local or None,
            )
        else:
            parsed_inputs: dict[str, str] = {}
            for item in inputs:
                if "=" not in item:
                    _fail(f"--input value must be in NAME=VALUE format, got: {item!r}")
                name, _, value = item.partition("=")
                parsed_inputs[name.strip()] = value.strip()
            result = api.create_env(
                source,
                env_path,
                python=python_exe,
                inputs=parsed_inputs or None,
                local_overrides=parsed_local or None,
            )
        _emit_env_result(result)
    except Exception as exc:
        _handle_recipe_errors(exc)



class _CLIProgress:
    """Simple progress reporter for the ``run`` subcommand."""

    def on_step_start(self, step_id: str, index: int, total: int) -> None:
        click.echo(f"[{index}/{total}] {step_id} ...", nl=False)

    def on_step_end(
        self,
        step_id: str,
        index: int,
        total: int,
        *,
        skipped: bool = False,
        elapsed: float = 0.0,
        error: BaseException | None = None,
    ) -> None:
        if error is not None:
            click.echo(f" FAILED ({elapsed:.2f}s)")
            return
        tag = "cached" if skipped else "ok"
        click.echo(f" {tag} ({elapsed:.2f}s)")


@main.command("run")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--executor",
    default="sequential",
    type=click.Choice(["sequential"]),
    show_default=True,
    help="Executor backend (only 'sequential' is implemented in Stage 6).",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help=(
        "Per-user run config file (output/temp/outputs dirs, storage_options, "
        "input defaults). Auto-discovered when omitted: "
        "<recipe_stem>.config.yaml next to the recipe (per-recipe), then "
        "./aa-recipe.config.yaml, then ~/.config/aa-recipe/config.yaml "
        "(or $AA_RECIPE_CONFIG). CLI flags override the config; the config "
        "overrides recipe defaults."
    ),
)
@click.option(
    "--output-dir",
    default=None,
    help=(
        "Directory for serialized step outputs and checkpoints. May be a local "
        "path or a gs:// URL (requires the 'gcs' extra; credentials via "
        "Application Default Credentials). Falls back to the config file's "
        f"output_dir, then './{DEFAULT_OUTPUT_ROOT}'."
    ),
)
@click.option(
    "--outputs-dir",
    default=None,
    help=(
        "Directory for user-facing outputs (images, logs, provenance). "
        "Defaults to a sibling of --output-dir named 'outputs'. May be a local "
        "path or a gs:// URL."
    ),
)
@click.option(
    "--temp-dir",
    default=None,
    help=(
        "Run-scoped scratch directory (exe_temp) for per-step intermediate "
        "stores. Defaults to a sibling of --output-dir named 'exe_temp' under "
        "the same scheme. May be a local path or a gs:// URL; remote scratch "
        "requires zarr intermediates (NetCDF cannot be written to a bucket)."
    ),
)
@click.option("--implementation", default=None, help="Override implementation key for all steps.")
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Supply a pipeline-level input value (repeatable).",
)
@click.option("--save-provenance", default=None, help="Path for the provenance sidecar file.")
@click.option("--skip-sinks", is_flag=True, default=False, help="Skip sink steps.")
@click.option(
    "--regenerate",
    type=click.Choice(["auto", "off", "sinks", "all"]),
    default="auto",
    show_default=True,
    help=(
        "Artifact-regeneration mode. 'auto' honors each step's 'regenerate' "
        "attribute (if-missing/always/never); 'off' regenerates nothing; "
        "'sinks' forces every sink step (plots/logs) to re-run; 'all' forces "
        "every artifact-emitting step, including data steps (which recompute). "
        "Use a non-auto mode when a cache was reused without its on-disk "
        "artifacts so they are regenerated locally."
    ),
)
@click.option("--force", is_flag=True, default=False, help="Bypass checkpoint checks.")
@click.option(
    "--log-output",
    type=click.Choice(["file", "console", "both"]),
    default="file",
    show_default=True,
    help=(
        "Where each step's stdout/stderr is sent. 'file' (default) writes only "
        "to outputs/logs/standard_out.txt; 'console' prints the captured logs "
        "to the terminal after the run; 'both' writes the file and prints."
    ),
)
@click.option(
    "--no-checkpoints",
    is_flag=True,
    default=False,
    help="Run without writing or reading any checkpoint files.",
)
@click.option(
    "--checkpoint-mode",
    default=None,
    type=click.Choice(["eager", "explicit", "terminal", "none"]),
    help=(
        "Override recipe's checkpoint policy. 'explicit' (default) saves only "
        "steps marked checkpoint: always; 'eager' saves every step; "
        "'terminal' only saves leaf steps; 'none' suppresses default saves "
        "but still honors per-step 'checkpoint: always' and --checkpoint "
        "overrides (use --no-checkpoints to disable checkpoints entirely)."
    ),
)
@click.option(
    "--checkpoint",
    "checkpoint_steps",
    multiple=True,
    metavar="STEP_ID",
    help="Force-checkpoint a step regardless of mode (repeatable).",
)
@click.option(
    "--checkpoint-format",
    default=None,
    type=click.Choice(["zarr", "netcdf", "pickle"]),
    help=(
        "Serialization format for checkpoint files. 'zarr' (default) writes "
        "Zarr stores; 'netcdf' writes NetCDF4 files; 'pickle' is a last-resort "
        "fallback. Overrides the recipe's execution.checkpoint_format setting."
    ),
)
@click.option(
    "--survey-cache-dir",
    "survey_cache_dir",
    default=None,
    metavar="DIR_OR_URL",
    help=(
        "Root of the shared (curated) survey cache tier. Cache reads probe "
        "[user, survey] in order; the user tier is --output-dir. Usually set "
        "via the run config (survey_cache_dir key) rather than this flag."
    ),
)
@click.option(
    "--cache-write-tier",
    "cache_write_tier",
    type=click.Choice(["user", "survey"]),
    default="user",
    show_default=True,
    help=(
        "Which cache tier this run writes. 'survey' marks a curated run: it "
        "reads and writes only the shared survey tier (requires a survey "
        "cache dir and credentials with write access to it — bucket IAM is "
        "the enforcement). Deliberately not a config key."
    ),
)
def run_cmd(
    recipe: str,
    executor: str,
    config_path: str | None,
    output_dir: str | None,
    outputs_dir: str | None,
    temp_dir: str | None,
    implementation: str | None,
    inputs: tuple[str, ...],
    save_provenance: str | None,
    skip_sinks: bool,
    regenerate: str,
    force: bool,
    log_output: str,
    no_checkpoints: bool,
    checkpoint_mode: str | None,
    checkpoint_steps: tuple[str, ...],
    checkpoint_format: str | None,
    survey_cache_dir: str | None,
    cache_write_tier: str,
) -> None:
    """Execute RECIPE's pipeline directly in this process."""
    # Force a non-interactive matplotlib backend before any visualization
    # module can be imported.  This prevents Tk windows from appearing and
    # avoids the "main thread is not in main loop" crash that occurs when
    # matplotlib's Tk backend is initialised from a background thread.
    # Users who explicitly set MPLBACKEND (e.g. to display plots locally)
    # are not overridden.
    import os as _os
    _os.environ.setdefault("MPLBACKEND", "Agg")

    if no_checkpoints and (checkpoint_mode or checkpoint_steps):
        _fail(
            "--no-checkpoints cannot be combined with --checkpoint-mode or "
            "--checkpoint; remove --no-checkpoints or drop the other flags."
        )

    parsed_inputs: dict[str, str] = {}
    for item in inputs:
        if "=" not in item:
            _fail(f"--input value must be in NAME=VALUE format, got: {item!r}")
        name, _, value = item.partition("=")
        parsed_inputs[name.strip()] = value.strip()

    # Per-user run config supplies storage locations and input defaults that are
    # too environment-specific for the portable recipe. Precedence for every
    # value is: explicit CLI flag > config file > recipe default > built-in.
    try:
        run_config = config.load_run_config(config_path, recipe_path=recipe)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    if run_config.source is not None:
        click.echo(f"Using run config: {run_config.source}")

    output_dir = output_dir or run_config.output_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    outputs_dir = outputs_dir or run_config.outputs_dir
    temp_dir = temp_dir or run_config.temp_dir
    survey_cache_dir = survey_cache_dir or run_config.survey_cache_dir
    if cache_write_tier == "survey" and survey_cache_dir is None:
        _fail(
            "--cache-write-tier survey requires a survey cache root; set "
            "survey_cache_dir in the run config or pass --survey-cache-dir."
        )
    # CLI --input wins over config inputs, which win over recipe defaults.
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    try:
        result = api.execute(
            recipe,
            inputs=merged_inputs or None,
            executor=executor,
            output_dir=output_dir,
            outputs_dir=outputs_dir,
            temp_dir=temp_dir,
            storage_options=run_config.storage_options,
            implementation_override=implementation,
            force=force,
            no_checkpoints=no_checkpoints,
            skip_sinks=skip_sinks,
            regenerate=regenerate,
            log_destination=log_output,
            checkpoint_mode=checkpoint_mode,
            checkpoint_steps=list(checkpoint_steps) or None,
            checkpoint_format=checkpoint_format,
            survey_cache_dir=survey_cache_dir,
            cache_write_tier=cache_write_tier,
            save_provenance=save_provenance,
            progress=_CLIProgress(),
        )
    except Exception as exc:
        _handle_recipe_errors(exc)
        return

    click.echo("")
    click.echo(
        f"Executed {len(result.executed_steps)} step(s), "
        f"skipped {len(result.skipped_steps)} (cache hits)."
    )
    if result.output_dir is not None:
        click.echo(f"Cache in: {result.output_dir}")
    if survey_cache_dir is not None:
        hit_count = sum(
            1
            for record in result.step_dispositions.values()
            if record.disposition == "hit-survey-cache"
        )
        click.echo(f"Survey cache: {survey_cache_dir} ({hit_count} hit(s))")
    if result.outputs_dir is not None:
        click.echo(f"Outputs in: {result.outputs_dir}")
    if result.manifest_file is not None:
        click.echo(f"Manifest: {result.manifest_file}")
    if result.log_file is not None:
        click.echo(f"Logs in: {result.log_file}")
    if log_output in ("console", "both") and result.console_log:
        click.echo("")
        click.echo("--- step logs ---")
        click.echo(result.console_log)


@main.command("clean")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(dir_okay=False),
    help=(
        "Run-config file supplying the cache location and credentials "
        "(same discovery as 'run' when omitted)."
    ),
)
@click.option(
    "--output-dir",
    default=None,
    help=(
        "Directory containing checkpoint files. Defaults to the run config's "
        f"output_dir, else ./{DEFAULT_OUTPUT_ROOT}. Cleans the user cache "
        "tier only — the shared survey tier is curated/manual."
    ),
)
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Supply a pipeline-level input value (repeatable).",
)
@click.option("--all", "clean_all", is_flag=True, default=False, help="Remove every checkpoint.")
@click.option("--stale", is_flag=True, default=False, help="Remove only stale checkpoints.")
@click.option("--dry-run", is_flag=True, default=False, help="Show files without removing them.")
def clean_cmd(
    recipe: str,
    config_path: str | None,
    output_dir: str | None,
    inputs: tuple[str, ...],
    clean_all: bool,
    stale: bool,
    dry_run: bool,
) -> None:
    """Remove checkpoint files for RECIPE under --output-dir."""
    if clean_all and stale:
        _fail("--all and --stale cannot be combined")
    mode = "all" if clean_all else ("stale" if stale else "intermediate")

    parsed_inputs: dict[str, str] = {}
    for item in inputs:
        if "=" not in item:
            _fail(f"--input value must be in NAME=VALUE format, got: {item!r}")
        name, _, value = item.partition("=")
        parsed_inputs[name.strip()] = value.strip()

    # Same config discovery as 'run', so a gs:// cache configured there is
    # cleanable without repeating the URL and credentials on the command line.
    try:
        run_config = config.load_run_config(config_path, recipe_path=recipe)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    if run_config.source is not None:
        click.echo(f"Using run config: {run_config.source}")
    output_dir = output_dir or run_config.output_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    try:
        removed = api.clean(
            recipe,
            output_dir,
            inputs=merged_inputs or None,
            mode=mode,
            dry_run=dry_run,
            storage_options=run_config.storage_options,
        )
    except Exception as exc:
        _handle_recipe_errors(exc)
        return

    verb = "Would remove" if dry_run else "Removed"
    if not removed:
        click.echo("Nothing to remove.")
        return
    click.echo(f"{verb} {len(removed)} file(s):")
    for path in removed:
        click.echo(f"  {path}")


@main.command("explain-cache")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(dir_okay=False),
    help=(
        "Run-config file supplying cache locations and credentials "
        "(same discovery as 'run' when omitted)."
    ),
)
@click.option(
    "--output-dir",
    default=None,
    help=(
        "User cache root to probe. Defaults to the run config's output_dir, "
        f"else ./{DEFAULT_OUTPUT_ROOT}."
    ),
)
@click.option(
    "--survey-cache-dir",
    "survey_cache_dir",
    default=None,
    metavar="DIR_OR_URL",
    help="Survey cache root to probe (defaults to the run config's value).",
)
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Supply a pipeline-level input value (repeatable).",
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the report as JSON instead of text.",
)
def explain_cache_cmd(
    recipe: str,
    config_path: str | None,
    output_dir: str | None,
    survey_cache_dir: str | None,
    inputs: tuple[str, ...],
    as_json: bool,
) -> None:
    """Explain, per step, why RECIPE would hit or miss the cache tiers.

    For each miss, the nearest stored entry with the same step id is diffed
    against the recipe's recomputed fingerprint, reporting exactly which
    field diverged (a param value, an input checksum, the cache epoch, an
    upstream change, ...).
    """
    import json as _json

    parsed_inputs: dict[str, str] = {}
    for item in inputs:
        if "=" not in item:
            _fail(f"--input value must be in NAME=VALUE format, got: {item!r}")
        name, _, value = item.partition("=")
        parsed_inputs[name.strip()] = value.strip()

    try:
        run_config = config.load_run_config(config_path, recipe_path=recipe)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    if run_config.source is not None and not as_json:
        click.echo(f"Using run config: {run_config.source}")
    output_dir = output_dir or run_config.output_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    survey_cache_dir = survey_cache_dir or run_config.survey_cache_dir
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    try:
        explanation = api.explain_cache(
            recipe,
            inputs=merged_inputs or None,
            output_dir=output_dir,
            survey_cache_dir=survey_cache_dir,
            storage_options=run_config.storage_options,
        )
    except Exception as exc:
        _handle_recipe_errors(exc)
        return

    if as_json:
        click.echo(_json.dumps(explanation.to_dict(), indent=2, default=str))
    else:
        click.echo(explanation.format_text())
