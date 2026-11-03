"""CLI entry point for aa-recipe-manager."""

from __future__ import annotations

import contextlib
import functools
import json
import logging
import os
import sys
import time
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import click

from aa_recipe_manager import api, config
from aa_recipe_manager.events import CancelFileWatcher, CancelToken, JsonlEventSink
from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyConflictError,
    DependencyVersionError,
    ImplementationNotFoundError,
    PipelineExecutionError,
    RecipeParseError,
    RecipeValidationError,
    SpecNotFoundError,
    UnknownStepError,
    UpstreamNotCachedError,
)
from aa_recipe_manager.executor.checkpoint import DEFAULT_OUTPUT_ROOT
from aa_recipe_manager.executor.engine.tasks import (
    TASK_INSTANCE_ATTR,
    TASK_ITEM_ATTR,
    TASK_STEP_ATTR,
)


#: Log level asked for on the command line. Tracked explicitly because
#: ``logging.getLogger().isEnabledFor(DEBUG)`` cannot answer the question:
#: importing echopype calls ``logging.disable(logging.WARNING)``, a global mute
#: that makes the check return False even when the root level is DEBUG.
_REQUESTED_LOG_LEVEL = "INFO"


def _fail(message: str) -> None:
    """Print an error message to stderr and exit with code 1."""
    click.echo(f"Error: {message}", err=True)
    sys.exit(1)


def _exception_text(exc: BaseException) -> str:
    """Return ``str(exc)``, falling back to a placeholder when that fails.

    Some exception types cannot rebuild their own message after a round trip
    through a worker process (``numpy.exceptions.AxisError`` loses the
    attributes its ``__str__`` reads), and formatting them would otherwise
    mask the error being reported.
    """
    try:
        return str(exc)
    except Exception:
        return f"<unprintable {type(exc).__name__}>"


def _echo_cause_chain(exc: BaseException) -> None:
    """Print the exceptions that led to ``exc``, innermost last.

    An error raised while handling another (a cleanup failure during a failed
    run, say) replaces it as the reported error. Printing the chain means the
    original is always visible even when something masks it.
    """
    # Anything already reported on its own line is walked through but not
    # repeated, so a deeper cause is still reached.
    quiet = {id(exc), id(getattr(exc, "original", None))}
    visited = {id(exc)}
    cursor: BaseException | None = exc
    while cursor is not None:
        nxt = cursor.__cause__ or cursor.__context__
        if nxt is None or id(nxt) in visited:
            break
        visited.add(id(nxt))
        if id(nxt) not in quiet:
            click.echo(
                f"  caused by: {type(nxt).__name__}: {_exception_text(nxt)}",
                err=True,
            )
        cursor = nxt


def _echo_traceback(exc: BaseException) -> None:
    """Print a full traceback when ``--log-level DEBUG`` was requested."""
    if _REQUESTED_LOG_LEVEL != "DEBUG":
        click.echo(
            "  (re-run with `aa-recipe --log-level DEBUG run ...` for a full "
            "traceback)",
            err=True,
        )
        return
    text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    click.echo(text, err=True)


def _failure_place(exc: BaseException) -> str:
    """Say which instance of a mapped step an error came from, if known."""
    instance = getattr(exc, TASK_INSTANCE_ATTR, None)
    if instance is None:
        return ""
    item = getattr(exc, TASK_ITEM_ATTR, None)
    return f" (instance {instance}" + (f", {item})" if item is not None else ")")


def _handle_recipe_errors(exc: Exception) -> None:
    """Format and display known recipe errors, then exit 1."""
    failed_step = getattr(exc, TASK_STEP_ATTR, None)
    if failed_step is not None and not isinstance(exc, PipelineExecutionError):
        click.echo(f"Step {failed_step!r} failed{_failure_place(exc)}:", err=True)
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
    elif isinstance(exc, DependencyConflictError):
        click.echo("Dependency conflict:", err=True)
        for c in exc.conflicts:
            click.echo(f"  - {c}", err=True)
        click.echo(
            "  A Python environment holds one build per package, so this recipe "
            "has no valid environment. Split the conflicting steps into separate "
            "recipes, or reconcile the specs.",
            err=True,
        )
    elif isinstance(exc, DependencyVersionError):
        click.echo(f"Dependency version error: {exc}", err=True)
    elif isinstance(exc, (UpstreamNotCachedError, UnknownStepError)):
        click.echo(f"Error: {exc}", err=True)
    elif isinstance(exc, PipelineExecutionError):
        click.echo(
            f"Pipeline execution failed at step {exc.step_id!r}"
            f"{_failure_place(exc)}:",
            err=True,
        )
        click.echo(f"  {exc}", err=True)
        if exc.callable_path:
            click.echo(f"  callable: {exc.callable_path}", err=True)
        if exc.original is not None:
            click.echo(
                f"  original: {type(exc.original).__name__}: "
                f"{_exception_text(exc.original)}",
                err=True,
            )
        _echo_cause_chain(exc)
        _echo_traceback(exc)
    elif isinstance(exc, FileExistsError):
        click.echo(f"File already exists: {exc}", err=True)
    elif isinstance(exc, FileNotFoundError):
        click.echo(f"File not found: {exc}", err=True)
    else:
        click.echo(f"Error: {type(exc).__name__}: {exc}", err=True)
        _echo_cause_chain(exc)
        _echo_traceback(exc)
    sys.exit(1)


@click.group()
@click.version_option(package_name="aa-recipe-manager")
@click.option(
    "--log-level",
    default="WARNING",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"]),
    show_default=True,
    help=(
        "Root log level. Step progress is echoed independently of this, so "
        "INFO only adds per-step detail from the op libraries."
    ),
)
def main(log_level: str) -> None:
    """aa-recipe-manager: define, share, generate, and execute scientific workflow recipes."""
    global _REQUESTED_LOG_LEVEL
    _REQUESTED_LOG_LEVEL = log_level
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
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Run config file whose inputs apply, found as for run when omitted.",
)
def dry_run_cmd(
    recipe: str,
    visualize: bool,
    check_versions: bool,
    inputs: tuple[str, ...],
    config_path: str | None,
) -> None:
    """Validate RECIPE without executing or generating any artifacts.

    Inputs come from the run config, as for run, with --input values on top.
    """
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
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    report = api.dry_run(
        recipe,
        inputs=merged_inputs or None,
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


def _echo_columns(rows: list[tuple[str, ...]]) -> None:
    """Print rows with every column but the last padded to a common width."""
    if not rows:
        return
    widths = [max(len(row[i]) for row in rows) for i in range(len(rows[0]) - 1)]
    for row in rows:
        cells = [cell.ljust(width) for cell, width in zip(row, widths)]
        click.echo("  ".join([*cells, row[-1]]).rstrip())


@main.command("graph")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Print the graph as JSON."
)
def graph_cmd(recipe: str, as_json: bool) -> None:
    """Show the flow diagram graph of RECIPE: each step's op and include."""
    try:
        graph = api.recipe_graph(recipe)
    except Exception as exc:
        _handle_recipe_errors(exc)
    if as_json:
        click.echo(json.dumps(graph, indent=2))
        return
    steps = [node for node in graph["nodes"] if node["kind"] == "step"]
    _echo_columns([(n["id"], n["op"], n["include"] or "-") for n in steps])


@main.command("ops")
@click.option(
    "--json", "as_json", is_flag=True, default=False, help="Print the catalog as JSON."
)
@click.option("--category", default=None, help="Only list ops in this category.")
def ops_cmd(as_json: bool, category: str | None) -> None:
    """List the ops in the registry with their category and description."""
    catalog = api.op_catalog()
    if category is not None:
        if category not in catalog["categories"]:
            _fail(
                f"unknown category {category!r}. Categories: "
                + ", ".join(catalog["categories"])
            )
        ops = [op for op in catalog["ops"] if op["category"] == category]
        implementations = [impl for op in ops for impl in op["implementations"]]
        catalog["ops"] = ops
        catalog["categories"] = [category]
        catalog["counts"] = {
            "ops": len(ops),
            "implementations": len(implementations),
            "sources_resolved": sum(
                1 for impl in implementations if impl["source"]["resolved"]
            ),
        }
    if as_json:
        click.echo(json.dumps(catalog, indent=2))
        return
    _echo_columns(
        [
            (
                op["op"],
                op["category"] or "-",
                op["description"].splitlines()[0] if op["description"] else "",
            )
            for op in catalog["ops"]
        ]
    )


@main.command("serve")
@click.option("--host", default=None, help="Interface to bind.  [default: 127.0.0.1]")
@click.option("--port", type=int, default=None, help="Port to bind.  [default: 8765]")
@click.option(
    "--recipe-root",
    "recipe_roots",
    multiple=True,
    type=click.Path(exists=True, file_okay=False),
    help="Folder whose recipes the client can open (repeatable). "
    "Defaults to the current folder.",
)
@click.option(
    "--data-root",
    "data_roots",
    multiple=True,
    type=click.Path(exists=True, file_okay=False),
    help="Folder the client may browse for data (repeatable). Defaults to home.",
)
@click.option(
    "--state-dir",
    default=None,
    help="Folder for sessions and server.yaml.  [default: ~/.aa-recipe/client]",
)
@click.option(
    "--config",
    "run_config",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    help="Run config file to use for every recipe, as --config does for run.",
)
@click.option("--docs-url", default=None, help="Base URL of the documentation site.")
@click.option(
    "--dev",
    is_flag=True,
    default=False,
    help="Accept the Vite dev server origin and reload on code changes.",
)
def serve_cmd(
    host: str | None,
    port: int | None,
    recipe_roots: tuple[str, ...],
    data_roots: tuple[str, ...],
    state_dir: str | None,
    run_config: str | None,
    docs_url: str | None,
    dev: bool,
) -> None:
    """Serve the recipe manager client in the browser on this machine.

    Settings in <state-dir>/server.yaml apply unless a flag is given.
    """
    try:
        import uvicorn
    except ImportError:
        _fail('the client server needs: pip install "aa-recipe-manager[server]"')
    from aa_recipe_manager.server.config import ServerConfig

    try:
        settings = ServerConfig.load(
            state_dir,
            host=host,
            port=port,
            recipe_roots=list(recipe_roots),
            data_roots=list(data_roots),
            run_config=run_config,
            docs_url=docs_url,
            dev=dev,
        )
    except ValueError as exc:
        _fail(str(exc))
    os.environ.update(settings.to_env())
    click.echo(f"Recipe manager client: http://{settings.host}:{settings.port}/")
    for root in settings.recipe_roots:
        click.echo(f"  recipe root: {Path(root).as_posix()}")
    click.echo(f"  state: {Path(settings.state_dir).as_posix()}")
    uvicorn.run(
        "aa_recipe_manager.server.app:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.dev,
        reload_dirs=[str(Path(__file__).parent)] if settings.dev else None,
    )


@main.command("docs")
@click.option(
    "--output",
    "-o",
    default="op_reference.html",
    show_default=True,
    help="Path for the generated HTML file. Use '-' to write to stdout.",
)
@click.option(
    "--source-links/--no-source-links",
    default=True,
    show_default=True,
    help=(
        "Import each implementation's package to link its source on GitHub "
        "and show its signature and docstring. This takes about a minute; "
        "disable it for a fast page or in an environment without the "
        "scientific packages installed."
    ),
)
@click.option(
    "--spec-file",
    "spec_files",
    multiple=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Extra spec YAML file to document alongside the built-in registry.",
)
@click.option(
    "--open",
    "open_browser",
    is_flag=True,
    default=False,
    help="Open the generated page in the default browser.",
)
def docs_cmd(
    output: str,
    source_links: bool,
    spec_files: tuple[str, ...],
    open_browser: bool,
) -> None:
    """Generate a browsable HTML reference for the built-in op registry."""
    import webbrowser
    from pathlib import Path as _Path

    to_stdout = output == "-"
    try:
        result = api.export_op_docs(
            None if to_stdout else output,
            resolve_sources=source_links,
            registry_files=spec_files,
        )
    except Exception as exc:
        _handle_recipe_errors(exc)
        return

    if to_stdout:
        click.echo(result.html)
        return

    counts = result.counts
    click.echo(f"Docs written to: {result.output}")
    summary = f"{counts['ops']} ops, {counts['implementations']} implementations"
    if source_links:
        summary += f", {counts['sources_resolved']} source links resolved"
    click.echo(summary)
    if source_links and result.unresolved:
        click.echo("No source link for: " + ", ".join(result.unresolved), err=True)
    if source_links and result.stale_links:
        click.echo(
            f"{len(result.stale_links)} link(s) point at files with uncommitted "
            "changes; commit and push for exact line numbers.",
            err=True,
        )

    if open_browser:
        webbrowser.open(_Path(result.output).resolve().as_uri())


@main.command("site-data")
@click.option(
    "--manifest",
    default="docs/site_recipes.yaml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False),
    help="Manifest listing the recipes the documentation site shows.",
)
@click.option(
    "--out",
    "out_dir",
    default="docs/data",
    show_default=True,
    help="Folder for the generated data files.",
)
@click.option(
    "--source-links/--no-source-links",
    default=True,
    show_default=True,
    help="Link each op implementation to its source on GitHub. Needs the "
    "scientific packages installed.",
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Compare the committed data with a fresh build instead of writing, "
    "ignoring source links. Exits 1 when anything is out of date.",
)
def site_data_cmd(manifest: str, out_dir: str, source_links: bool, check: bool) -> None:
    """Generate the documentation site's op, recipe graph and tab data."""
    try:
        if check:
            problems = api.check_site_data(manifest, out_dir)
        else:
            result = api.export_site_data(
                manifest, out_dir, resolve_sources=source_links
            )
    except Exception as exc:
        _handle_recipe_errors(exc)
        return

    if check:
        if problems:
            for line in problems:
                click.echo(line, err=True)
            _fail("site data is out of date; run 'aa-recipe site-data' and commit.")
        click.echo("Site data is current.")
        return

    counts = result.counts
    click.echo(f"Wrote {len(result.written)} files to {out_dir}")
    for path in result.removed:
        click.echo(f"Removed {path}, no longer in the manifest")
    summary = f"{counts['ops']} ops, {counts['recipes']} recipes"
    if source_links:
        summary += f", {counts['sources_resolved']} source links resolved"
    click.echo(summary)
    if result.unresolved:
        click.echo("No source link for: " + ", ".join(result.unresolved), err=True)
    if result.stale_links:
        click.echo(
            f"{len(result.stale_links)} link(s) point at files with uncommitted "
            "changes; commit and push for exact line numbers.",
            err=True,
        )


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



def _format_duration(seconds: float) -> str:
    """Render a duration as seconds, m/s, or h/m/s — whichever reads fastest.

    Long GCS runs are the common case, and "1h 04m 12s" is easier to compare
    across runs at a glance than "3852.31s".
    """
    if seconds < 60:
        return f"{seconds:.2f}s"
    minutes, secs = divmod(int(round(seconds)), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s ({seconds:.1f}s)"
    return f"{minutes}m {secs:02d}s ({seconds:.1f}s)"


def _format_step_time(elapsed: float, instance_seconds: Sequence[float] = ()) -> str:
    """Render a step's time, adding the spread when it fanned out.

    A mapped step's total is the sum over its instances, which alone cannot
    tell "every file was slow" from "one file dominated" -- and under a
    concurrent executor the sum exceeds wall clock, so quoting it by itself
    invites the wrong conclusion. The per-instance spread makes both legible.
    """
    if len(instance_seconds) < 2:
        return f"{elapsed:.2f}s"
    count = len(instance_seconds)
    mean = sum(instance_seconds) / count
    return (
        f"{count} instances: sum {sum(instance_seconds):.2f}s, "
        f"avg {mean:.2f}s, min {min(instance_seconds):.2f}s, "
        f"max {max(instance_seconds):.2f}s"
    )


def _resolve_executor_options(
    executor: str,
    dask_scheduler: str | None,
    dask_workers: int | None,
    run_config: config.RunConfig,
) -> tuple[str, dict[str, object]]:
    """Resolve the backend name and its constructor options.

    Precedence for both the backend and its Dask sizing is explicit CLI flag,
    then run config, then default. Only a flag the caller actually typed is
    rejected against a non-Dask backend: a config carrying dask_scheduler /
    dask_workers is a standing default for the runs that do use Dask, and must
    not fail every other run.
    """
    if executor == "sequential" and run_config.executor:
        executor = run_config.executor
    if executor != "dask":
        if dask_scheduler is not None or dask_workers is not None:
            _fail("--dask-scheduler / --dask-workers require --executor dask.")
        return executor, {}

    options: dict[str, object] = {}
    scheduler = dask_scheduler or run_config.dask_scheduler
    workers = dask_workers if dask_workers is not None else run_config.dask_workers
    if scheduler is not None:
        options["scheduler"] = scheduler
    if workers is not None:
        options["n_workers"] = workers
    return executor, options


def _echo_now(message: str = "", err: bool = False) -> None:
    """Echo and flush immediately.

    Under Git Bash / MinTTY (and any redirect to a file or pipe) stdout is not a
    tty, so Python block-buffers it: progress lines surface in ~8 KB bursts and
    an interrupted run loses the tail entirely — including the line naming the
    step it died on. Flushing per line keeps the console honest about where a
    long run actually is.
    """
    click.echo(message, err=err)
    try:
        (sys.stderr if err else sys.stdout).flush()
    except Exception:  # a closed stream must not kill the run
        pass


class _CLIProgress:
    """Progress reporter for the ``run`` subcommand.

    Start and finish are separate, fully-labeled lines. A concurrent executor
    interleaves steps (and library code prints between them), so a step's
    outcome cannot be appended to its start line — every line names the step it
    belongs to, and the elapsed time is never ambiguous.

    Args:
        err: Print to stderr, which keeps stdout free for an event stream.
    """

    def __init__(self, err: bool = False) -> None:
        self._err = err

    def on_step_start(self, step_id: str, index: int, total: int) -> None:
        _echo_now(f"[{index}/{total}] {step_id} ... start", err=self._err)

    def on_step_end(
        self,
        step_id: str,
        index: int,
        total: int,
        *,
        skipped: bool = False,
        elapsed: float = 0.0,
        error: BaseException | None = None,
        instance_seconds: Sequence[float] = (),
    ) -> None:
        if error is not None:
            status = "FAILED"
        elif skipped:
            status = "cached"
        else:
            status = "ok"
        _echo_now(
            f"[{index}/{total}] {step_id} ... {status} "
            f"({_format_step_time(elapsed, instance_seconds)})",
            err=self._err,
        )


@main.command("run")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--executor",
    default="sequential",
    type=click.Choice(["sequential", "dask", "prefect"]),
    show_default=True,
    help=(
        "Executor backend. 'sequential' (default) runs in-process; 'dask' "
        "distributes across a Dask cluster (requires the 'dask' extra); "
        "'prefect' orchestrates via Prefect (requires the 'prefect' extra)."
    ),
)
@click.option(
    "--dask-scheduler",
    default=None,
    help=(
        "Dask scheduler for --executor dask: 'threads' (default local thread "
        "cluster), 'processes' (local worker processes), or a scheduler "
        "address (tcp://host:port) for an external cluster."
    ),
)
@click.option(
    "--dask-workers",
    type=int,
    default=None,
    help="Worker count for a local Dask cluster (--executor dask).",
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
    "--user-cache-dir",
    default=None,
    help=(
        "Directory for serialized step outputs and checkpoints. May be a local "
        "path or a gs:// URL (requires the 'gcs' extra; credentials via "
        "Application Default Credentials). Falls back to the config file's "
        f"user_cache_dir, then './{DEFAULT_OUTPUT_ROOT}'."
    ),
)
@click.option(
    "--outputs-dir",
    default=None,
    help=(
        "Directory for user-facing outputs (images, logs, provenance). "
        "Defaults to a sibling of --user-cache-dir named 'outputs'. May be a local "
        "path or a gs:// URL."
    ),
)
@click.option(
    "--temp-dir",
    default=None,
    help=(
        "Run-scoped scratch directory (exe_temp) for per-step intermediate "
        "stores. Defaults to a sibling of --user-cache-dir named 'exe_temp' under "
        "the same scheme. May be a local path or a gs:// URL; remote scratch "
        "requires zarr intermediates (NetCDF cannot be written to a bucket)."
    ),
)
@click.option(
    "--keep-temp",
    is_flag=True,
    default=False,
    help=(
        "Do not delete the run-scoped scratch directory (exe_temp) at the end "
        "of the run. Diagnostic: leaves per-step intermediate stores in place "
        "so a slow step can be profiled against its real inputs."
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
        "[user, survey] in order; the user tier is --user-cache-dir. Usually set "
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
        "cache dir and credentials with write access to it; bucket IAM is "
        "the enforcement). Deliberately not a config key."
    ),
)
@click.option(
    "--to",
    "targets",
    multiple=True,
    metavar="STEP_ID",
    help=(
        "Run to this step: plan only it and its ancestors, and checkpoint it "
        "(repeatable). A cached target loads; add --force-step to recompute."
    ),
)
@click.option(
    "--only",
    is_flag=True,
    default=False,
    help=(
        "Run the --to steps alone, loading their parents from the cache. "
        "Fails, naming them, when a parent is not cached."
    ),
)
@click.option(
    "--force-step",
    "force_steps",
    multiple=True,
    metavar="STEP_ID",
    help="Recompute this step even when it is cached (repeatable).",
)
@click.option(
    "--no-pin-targets",
    is_flag=True,
    default=False,
    help="Do not checkpoint the --to steps beyond what the checkpoint mode saves.",
)
@click.option(
    "--events",
    "events_path",
    default=None,
    metavar="PATH",
    help=(
        "Append the run's events to PATH as JSON lines, or write them to "
        "stdout with '-' (progress and the summary then go to stderr)."
    ),
)
@click.option(
    "--cancel-file",
    default=None,
    metavar="PATH",
    help=(
        "Stop the run cleanly when PATH appears (checked once a second): "
        "running steps finish, nothing new starts, and the exit code is 2."
    ),
)
def run_cmd(
    recipe: str,
    executor: str,
    dask_scheduler: str | None,
    dask_workers: int | None,
    config_path: str | None,
    user_cache_dir: str | None,
    outputs_dir: str | None,
    temp_dir: str | None,
    keep_temp: bool,
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
    targets: tuple[str, ...],
    only: bool,
    force_steps: tuple[str, ...],
    no_pin_targets: bool,
    events_path: str | None,
    cancel_file: str | None,
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
    if only and not targets:
        _fail("--only needs at least one --to STEP_ID.")

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
    # With events on stdout every human line goes to stderr, so stdout stays
    # pure JSON lines.
    to_stderr = events_path == "-"
    say = functools.partial(_echo_now, err=to_stderr)
    if run_config.source is not None:
        say(f"Using run config: {run_config.source}")

    user_cache_dir = (
        user_cache_dir or run_config.user_cache_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    )
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

    executor, executor_options = _resolve_executor_options(
        executor, dask_scheduler, dask_workers, run_config
    )

    event_sink: JsonlEventSink | None = None
    if events_path is not None:
        event_sink = JsonlEventSink(sys.stdout if to_stderr else events_path)
    cancel_token: CancelToken | None = None
    watcher: CancelFileWatcher | None = None
    if cancel_file is not None:
        cancel_token = CancelToken()
        watcher = CancelFileWatcher(cancel_file, cancel_token, interval=1.0)
        watcher.start()
    # An op or the executor may print to the terminal (console_print writes
    # to the stream the run's log capture wrapped). With events on stdout
    # that stream must be stderr, or the JSON lines are corrupted.
    stdout_guard = (
        contextlib.redirect_stdout(sys.stderr)
        if to_stderr
        else contextlib.nullcontext()
    )
    run_start = time.perf_counter()
    try:
        with stdout_guard:
            result = api.execute(
                recipe,
                inputs=merged_inputs or None,
                executor=executor,
                executor_options=executor_options or None,
                user_cache_dir=user_cache_dir,
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
                progress=_CLIProgress(err=to_stderr),
                keep_temp=keep_temp,
                targets=list(targets) or None,
                only=only,
                force_steps=list(force_steps),
                pin_targets=not no_pin_targets,
                events=event_sink,
                cancel=cancel_token,
            )
    except Exception as exc:
        # Wall-clock, not a step sum: it's the answer to "did dask actually
        # help" even when the run dies partway through.
        click.echo(
            f"Total time before failure: {_format_duration(time.perf_counter() - run_start)}",
            err=True,
        )
        _handle_recipe_errors(exc)
        return
    finally:
        if watcher is not None:
            watcher.stop()
        if event_sink is not None:
            event_sink.close()

    say()
    say(
        f"Executed {len(result.executed_steps)} step(s), "
        f"skipped {len(result.skipped_steps)} (cache hits)."
    )
    say(f"Total time: {_format_duration(result.elapsed_seconds)}")
    if targets:
        how = "run only" if only else "run to here"
        say(f"Targets: {', '.join(targets)} ({how})")
        say(f"Outside the target: {len(result.untargeted_steps)} step(s)")
    for entry in result.logs:
        if entry.startswith("warning: target"):
            say("W" + entry[1:])
    # What actually ran the steps — the answer to "why was there no speedup?"
    for entry in result.logs:
        if entry.startswith("executor: "):
            say(entry.splitlines()[0].capitalize())
            break
    if result.user_cache_dir is not None:
        say(f"Cache in: {result.user_cache_dir}")
    if survey_cache_dir is not None:
        hit_count = sum(
            1
            for record in result.step_dispositions.values()
            if record.disposition == "hit-survey-cache"
        )
        say(f"Survey cache: {survey_cache_dir} ({hit_count} hit(s))")
    if result.outputs_dir is not None:
        say(f"Outputs in: {result.outputs_dir}")
    if result.manifest_file is not None:
        say(f"Manifest: {result.manifest_file}")
    if result.log_file is not None:
        say(f"Logs in: {result.log_file}")
    if log_output in ("console", "both") and result.console_log:
        say()
        say("--- step logs ---")
        say(result.console_log)
    if result.status == "cancelled":
        finished = len(result.executed_steps) + len(result.skipped_steps)
        say(f"Run cancelled: {finished} step(s) finished before the stop.")
        sys.exit(2)


@main.command("batch")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--input-dir",
    required=True,
    help="Folder of input files (local path or gs:// URL); one run per file.",
)
@click.option(
    "--input-name",
    required=True,
    help="Recipe input each matched file is bound to (e.g. raw_input_folder).",
)
@click.option(
    "--pattern", default="*.raw", show_default=True,
    help="Glob selecting files under --input-dir.",
)
@click.option(
    "--user-cache-dir", default=None,
    help=(
        "Shared checkpoint cache for the whole batch (work common to several "
        f"inputs is computed once). Defaults to ./{DEFAULT_OUTPUT_ROOT}."
    ),
)
@click.option(
    "--outputs-dir", default=None,
    help="Root for per-input outputs; each run writes under <outputs-dir>/<label>/.",
)
@click.option(
    "--config", "config_path", default=None,
    type=click.Path(exists=True, dir_okay=False),
    help="Run-config file (same discovery as 'run' when omitted).",
)
@click.option(
    "--executor", default="sequential",
    type=click.Choice(["sequential", "dask", "prefect"]), show_default=True,
    help="Executor backend for each run.",
)
@click.option("--dask-scheduler", default=None, help="Dask scheduler (see 'run').")
@click.option("--dask-workers", type=int, default=None, help="Dask worker count.")
def batch_cmd(
    recipe: str,
    input_dir: str,
    input_name: str,
    pattern: str,
    user_cache_dir: str | None,
    outputs_dir: str | None,
    config_path: str | None,
    executor: str,
    dask_scheduler: str | None,
    dask_workers: int | None,
) -> None:
    """Run RECIPE once per input file under --input-dir (UC-6)."""
    import os as _os
    _os.environ.setdefault("MPLBACKEND", "Agg")

    from aa_recipe_manager.executor import input_sets_from_folder

    try:
        run_config = config.load_run_config(config_path, recipe_path=recipe)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    user_cache_dir = (
        user_cache_dir or run_config.user_cache_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    )
    outputs_dir = outputs_dir or run_config.outputs_dir

    executor, executor_options = _resolve_executor_options(
        executor, dask_scheduler, dask_workers, run_config
    )

    run_start = time.perf_counter()
    try:
        input_sets = input_sets_from_folder(
            input_dir, input_name, pattern=pattern,
            storage_options=run_config.storage_options,
        )
        click.echo(f"Batch: {len(input_sets)} input set(s) from {input_dir}")
        batch = api.execute_batch(
            recipe,
            input_sets,
            executor=executor,
            executor_options=executor_options or None,
            user_cache_dir=user_cache_dir,
            outputs_dir=outputs_dir,
            storage_options=run_config.storage_options,
            progress=_CLIProgress(),
        )
    except Exception as exc:
        click.echo(
            f"Total time before failure: {_format_duration(time.perf_counter() - run_start)}",
            err=True,
        )
        _handle_recipe_errors(exc)
        return

    click.echo("")
    click.echo(f"Completed {len(batch)} run(s).")
    for label, result in zip(batch.labels, batch.results):
        click.echo(
            f"  [{label}] {len(result.executed_steps)} computed, "
            f"{len(result.skipped_steps)} cached, "
            f"{_format_duration(result.elapsed_seconds)}"
        )
    click.echo(f"Total time: {_format_duration(batch.elapsed_seconds)}")
    if batch.manifest_file is not None:
        click.echo(f"Batch manifest: {batch.manifest_file}")


@main.command("doctor")
@click.argument(
    "recipe", required=False, type=click.Path(exists=True, dir_okay=False)
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(dir_okay=False),
    help=(
        "Run-config file to report on (same discovery as 'run' when omitted; "
        "pass a RECIPE to pick up its per-recipe config)."
    ),
)
def doctor_command(recipe: str | None, config_path: str | None) -> None:
    """Report installed versions, machine resources, and path writability.

    Run this from the directory a recipe is run from, and send the output along
    when reporting a failure: it identifies the exact build of every AA-SI
    package, the memory that decides whether echopype swaps large arrays to
    disk, and whether the configured directories accept the nested writes a
    zarr store performs.
    """
    from aa_recipe_manager import diagnostics  # noqa: PLC0415

    try:
        run_config = config.load_run_config(config_path, recipe_path=recipe)
    except (FileNotFoundError, ValueError) as exc:
        # A broken config must not stop the rest of the report; it is often
        # the thing being diagnosed.
        click.echo(diagnostics.build_report(f"failed to load: {exc}", {}))
        return

    paths = {
        "temp_dir": run_config.temp_dir,
        "outputs_dir": run_config.outputs_dir,
        "user_cache_dir": run_config.user_cache_dir,
        "survey_cache_dir": run_config.survey_cache_dir,
    }
    source = str(run_config.source) if run_config.source else None
    click.echo(diagnostics.build_report(source, paths))


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
    "--user-cache-dir",
    default=None,
    help=(
        "Directory containing checkpoint files. Defaults to the run config's "
        f"user_cache_dir, else ./{DEFAULT_OUTPUT_ROOT}. Cleans the user cache "
        "tier only; the shared survey tier is curated/manual."
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
    user_cache_dir: str | None,
    inputs: tuple[str, ...],
    clean_all: bool,
    stale: bool,
    dry_run: bool,
) -> None:
    """Remove checkpoint files for RECIPE under --user-cache-dir."""
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
    user_cache_dir = (
        user_cache_dir or run_config.user_cache_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    )
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    try:
        removed = api.clean(
            recipe,
            user_cache_dir,
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


@main.command("preview")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--to",
    "targets",
    multiple=True,
    metavar="STEP_ID",
    help="Preview a run to this step (repeatable).",
)
@click.option("--only", is_flag=True, default=False, help="Preview a run of --to only.")
@click.option(
    "--force-step",
    "force_steps",
    multiple=True,
    metavar="STEP_ID",
    help="Treat this step as forced to recompute (repeatable).",
)
@click.option(
    "--force", is_flag=True, default=False, help="Treat every step as forced."
)
@click.option(
    "--regenerate",
    type=click.Choice(["auto", "off", "sinks", "all"]),
    default="auto",
    show_default=True,
    help="Artifact regeneration mode, as for 'run'.",
)
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
    "--user-cache-dir",
    default=None,
    help=(
        "User cache root to probe. Defaults to the run config's user_cache_dir, "
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
    "--checkpoint",
    "checkpoint_steps",
    multiple=True,
    metavar="STEP_ID",
    help="Force-checkpoint a step regardless of mode (repeatable).",
)
@click.option(
    "--checkpoint-mode",
    default=None,
    type=click.Choice(["eager", "explicit", "terminal", "none"]),
    help="Checkpoint policy override, as for 'run'.",
)
@click.option(
    "--no-pin-targets",
    is_flag=True,
    default=False,
    help="Do not count the --to steps as checkpointed.",
)
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Supply a pipeline-level input value (repeatable).",
)
@click.option(
    "--base-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help=(
        "Resolve the recipe's relative paths against PATH instead of the "
        "working directory, as a run started there would."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the preview as JSON instead of text.",
)
def preview_cmd(
    recipe: str,
    targets: tuple[str, ...],
    only: bool,
    force_steps: tuple[str, ...],
    force: bool,
    regenerate: str,
    config_path: str | None,
    user_cache_dir: str | None,
    survey_cache_dir: str | None,
    checkpoint_steps: tuple[str, ...],
    checkpoint_mode: str | None,
    no_pin_targets: bool,
    inputs: tuple[str, ...],
    base_dir: str | None,
    as_json: bool,
) -> None:
    """Show, per step, what running RECIPE would do, without running it.

    Each row gives the step's hash prefix, the cache tiers holding it (U for
    user, S for survey), the planned disposition (compute, load-user,
    load-survey, marker, skipped, pruned, untargeted, unknown), whether its
    output is or will be saved, and for a mapped step the instances held.
    Nothing is written.
    """
    if only and not targets:
        _fail("--only needs at least one --to STEP_ID.")
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
    user_cache_dir = (
        user_cache_dir or run_config.user_cache_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    )
    survey_cache_dir = survey_cache_dir or run_config.survey_cache_dir
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    try:
        preview = api.plan_preview(
            recipe,
            inputs=merged_inputs or None,
            targets=list(targets) or None,
            only=only,
            force_steps=list(force_steps),
            force=force,
            regenerate=regenerate,
            user_cache_dir=user_cache_dir,
            survey_cache_dir=survey_cache_dir,
            outputs_dir=run_config.outputs_dir,
            checkpoint_mode=checkpoint_mode,
            checkpoint_steps=list(checkpoint_steps) or None,
            pin_targets=not no_pin_targets,
            storage_options=run_config.storage_options,
            base_dir=base_dir,
        )
    except Exception as exc:
        _handle_recipe_errors(exc)
        return

    if as_json:
        click.echo(json.dumps(preview.to_dict(), indent=2, default=str))
    else:
        click.echo(preview.format_text())


def _read_text_source(source: str, option: str) -> str:
    """Read a file, or standard input for ``-``."""
    if source == "-":
        return click.get_text_stream("stdin").read()
    try:
        return Path(source).read_text(encoding="utf-8-sig")
    except OSError as exc:
        _fail(f"{option}: cannot read {source!r}: {exc}")


def _edit_operations(
    workspace: Any,
    ops_file: str | None,
    sets: tuple[str, ...],
    unsets: tuple[str, ...],
    checkpoints: tuple[str, ...],
    overrides: tuple[str, ...],
    unset_overrides: tuple[str, ...],
    input_defaults: tuple[str, ...],
    replace_texts: tuple[str, ...],
) -> list[Any]:
    """Build the operations the options ask for, in the order they apply.

    A whole number for a ``float`` param, override or input default becomes
    a float, as the client writes it.
    """
    from aa_recipe_manager.editing import cli_args, typed_operations

    try:
        pairs = [
            cli_args.split_assignment(item, "--replace-text", "DOCUMENT=FILE")
            for item in replace_texts
        ]
        if [ops_file, *(source for _, source in pairs)].count("-") > 1:
            _fail("only one of --ops and --replace-text can read standard input.")
        ops = []
        if ops_file is not None:
            text = _read_text_source(ops_file, "--ops")
            ops = cli_args.operations_from_json(text, "--ops")
        texts = [
            (document, _read_text_source(source, "--replace-text"))
            for document, source in pairs
        ]
        ops += cli_args.edit_operations(
            workspace,
            sets=sets,
            unsets=unsets,
            checkpoints=checkpoints,
            overrides=overrides,
            unset_overrides=unset_overrides,
            input_defaults=input_defaults,
            replace_texts=texts,
        )
        return typed_operations(workspace, ops)
    except ValueError as exc:
        _fail(str(exc))


def _describe_op(op: Any) -> str:
    """A short name for an operation in an error message."""
    names = ("step", "include", "document")
    target = next((getattr(op, n) for n in names if hasattr(op, n)), "")
    if getattr(op, "name", None) is not None:
        dotted = ".".join(str(part) for part in (op.name, *getattr(op, "path", ())))
        target += ("." if hasattr(op, "step") else ":") + dotted
    return f"{op.kind} {target}".strip()


def _edit_diff(label: str, before: str, after: str) -> str:
    """A unified diff of one document's text."""
    import difflib

    def lines(text: str) -> list[str]:
        found = text.splitlines(keepends=True)
        if found and not found[-1].endswith("\n"):
            found[-1] += "\n"
        return found

    return "".join(
        difflib.unified_diff(lines(before), lines(after), f"a/{label}", f"b/{label}")
    )


def _echo_validation(report: Any) -> None:
    """Print a validation report's verdict, errors and warnings."""
    if report.is_valid and report.warnings:
        click.echo(f"Valid, with {len(report.warnings)} warning(s).")
    elif report.is_valid:
        click.echo("Valid.")
    else:
        click.echo("Validation errors:")
        for error in report.errors:
            click.echo(f"  - {error}")
    if report.warnings:
        click.echo("Warnings:")
        for warning in report.warnings:
            click.echo(f"  - {warning}")


def _echo_save_as(result: Any, steps: int, keep_paths: bool) -> None:
    """Print what a Save As into another folder rewrote and what it rehashed."""
    for label, rewrites in result.rewrites.items():
        for rewrite in rewrites:
            click.echo(
                f"rebased {label} {rewrite.where}: {rewrite.old} -> {rewrite.new}"
            )
    if result.config is not None and result.dry_run:
        click.echo(f"would copy run config to {result.config}")
    elif result.config is not None:
        click.echo(f"run config copied to {result.config}")
    else:
        click.echo("no run config copied")
    for site in result.absolute:
        click.echo(
            f"Warning: {site} has no relative path from the new folder (another "
            "drive); it was written as an absolute path, so the copy is not "
            "portable.",
            err=True,
        )
    if result.rehashed is None:
        reason = (
            "; with --keep-paths this usually means folders the recipe reads by "
            "plain path are missing beside the copy"
            if keep_paths
            else ""
        )
        click.echo(
            "Step hashes could not be computed, so which steps start a new cache "
            f"is unknown{reason}."
        )
    elif result.rehashed:
        click.echo(
            f"{len(result.rehashed)} of {steps} steps hash differently from the "
            "original; the copy starts a new cache for them: "
            + ", ".join(result.rehashed)
        )
    else:
        click.echo(
            f"All {steps} steps hash as in the original; the copy shares its caches."
        )


@main.command("edit")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--set",
    "sets",
    multiple=True,
    metavar="STEP.PARAM=VALUE",
    help=(
        "Set a step param (repeatable). PARAM may be dotted for a nested value: "
        "filters.CRUISE_NAME, companion_extensions.0 (a number indexes a list). "
        'An empty VALUE (s.k=) is null and "" is the empty string. A key '
        "that holds a dot or is a number needs --ops."
    ),
)
@click.option(
    "--unset",
    "unsets",
    multiple=True,
    metavar="STEP.PARAM",
    help="Remove a step param or a nested entry (repeatable).",
)
@click.option(
    "--checkpoint",
    "checkpoints",
    multiple=True,
    metavar="STEP=always|never|save|none",
    help="Set a step's checkpoint key; none removes it (repeatable).",
)
@click.option(
    "--override",
    "overrides",
    multiple=True,
    metavar="INCLUDE:NAME=VALUE",
    help=(
        "Set an include's input override (repeatable). INCLUDE is the file's "
        "path from the root recipe's folder, as 'aa-recipe graph' lists it."
    ),
)
@click.option(
    "--unset-override",
    "unset_overrides",
    multiple=True,
    metavar="INCLUDE:NAME",
    help="Remove an include's input override (repeatable).",
)
@click.option(
    "--input-default",
    "input_defaults",
    multiple=True,
    metavar="[DOCUMENT:]NAME=VALUE",
    help=(
        "Set the default of a recipe input (repeatable), in the file that "
        "declares it; DOCUMENT: is needed when more than one file does."
    ),
)
@click.option(
    "--replace-text",
    "replace_texts",
    multiple=True,
    metavar="DOCUMENT=FILE",
    help=(
        "Replace a file's whole text with FILE's, or standard input for - (repeatable)."
    ),
)
@click.option(
    "--ops",
    "ops_file",
    default=None,
    metavar="FILE",
    help=(
        "Apply a JSON list of operations in the wire format first; - reads "
        "standard input."
    ),
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Path(dir_okay=False),
    help=(
        "Save As: write the edited root recipe to this file. Beside the "
        "original, the recipe's <stem>.config.yaml is copied to the new name's "
        "and edited sub-recipes are saved in place. Into another folder, "
        "the sub-recipes are copied with the recipe's layout, relative data "
        "paths are rewritten to reach the same files, the run config is copied "
        "beside it, and the originals are not changed. An existing file needs "
        "--overwrite; a run config is never replaced."
    ),
)
@click.option(
    "--keep-paths",
    is_flag=True,
    default=False,
    help=(
        "With --output into another folder, rewrite only the paths no step "
        "hash contains, so the copy keeps every hash and shares the caches; "
        "paths read as written must then exist beside the copy."
    ),
)
@click.option(
    "--no-config",
    is_flag=True,
    default=False,
    help="With --output, do not copy the run config.",
)
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help=(
        "With --output, replace an existing file there: the root, a sub-recipe "
        "or a run config copy. Saving in place, write files that changed on "
        "disk since they were read."
    ),
)
@click.option(
    "--copy-to",
    default=None,
    metavar="DIR",
    type=click.Path(file_okay=False),
    help=(
        "Edit a working copy of the recipe's files under DIR, with the same "
        "layout; the originals are not changed. Its history is kept in "
        "DIR/.aa-recipe, so a later --copy-to DIR continues the copy: files "
        "changed on disk since are reloaded, unless the copy edited them too."
    ),
)
@click.option(
    "--undo",
    "undo_count",
    type=click.IntRange(min=1),
    is_flag=False,
    flag_value=1,
    default=None,
    metavar="[N]",
    help="With --copy-to, undo the copy's newest N edits (default 1) first.",
)
@click.option(
    "--redo",
    "redo_count",
    type=click.IntRange(min=1),
    is_flag=False,
    flag_value=1,
    default=None,
    metavar="[N]",
    help="With --copy-to, redo the newest N undone edits (default 1), after --undo.",
)
@click.option(
    "--reload",
    "reload_conflicts",
    is_flag=True,
    default=False,
    help=(
        "With --copy-to, also reload the files the copy edited that changed on "
        "disk; the copy's edits to them are replaced, as an edit to undo."
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help=(
        "Print a unified diff per changed file and write nothing; with "
        "--output, also what Save As would write and rewrite."
    ),
)
@click.option(
    "--check",
    is_flag=True,
    default=False,
    help="Exit 1 and write nothing when the edited recipe has validation errors.",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(dir_okay=False),
    help="Run config file whose inputs apply, found as for run when omitted.",
)
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help=(
        "Supply a pipeline-level input value (repeatable), over the run "
        "config's, for validation, checkpoint checks and the hash comparison "
        "of --output."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the files, edits, validation and writes as JSON instead of text.",
)
def edit_cmd(
    recipe: str,
    sets: tuple[str, ...],
    unsets: tuple[str, ...],
    checkpoints: tuple[str, ...],
    overrides: tuple[str, ...],
    unset_overrides: tuple[str, ...],
    input_defaults: tuple[str, ...],
    replace_texts: tuple[str, ...],
    ops_file: str | None,
    output: str | None,
    keep_paths: bool,
    no_config: bool,
    overwrite: bool,
    copy_to: str | None,
    undo_count: int | None,
    redo_count: int | None,
    reload_conflicts: bool,
    dry_run: bool,
    check: bool,
    config_path: str | None,
    inputs: tuple[str, ...],
    as_json: bool,
) -> None:
    """Edit RECIPE and the sub-recipes it includes, keeping comments and layout.

    Each edit lands in the file that declares its target and changes only the
    lines it has to; a file nothing changed is written back byte for byte.
    VALUEs are read as YAML: 5, 5.0, true, [1, 2] and {a: 1} are what they
    look like, a bare word is a string, ${inputs.x} is kept as written, an
    empty VALUE is null and "" is the empty string. Keys that hold a dot or
    are numbers cannot be named in the dotted forms; use --ops for them.
    Edits apply in this order: --ops, --set, --unset, --checkpoint,
    --override, --unset-override, --input-default, --replace-text.

    Without --output or --copy-to the changed files are saved in place. The
    edited recipe is validated as dry-run would; validation errors are shown
    but do not stop the save unless --check is given. An edit that leaves
    the recipe unreadable, or a checkpoint change that breaks the DAG, is
    refused and nothing is written.
    """
    from aa_recipe_manager import editing

    if dry_run and copy_to:
        _fail("--dry-run writes nothing; it cannot be combined with --copy-to.")
    if output and copy_to:
        _fail(
            "--output and --copy-to cannot be combined; --output alone into "
            "another folder copies the recipe with the edits."
        )
    for flag, given in (("--keep-paths", keep_paths), ("--no-config", no_config)):
        if given and not output:
            _fail(f"{flag} needs --output.")
    if overwrite and copy_to:
        _fail("--overwrite does nothing with --copy-to, which writes only the copy.")
    for flag, given in (
        ("--undo", undo_count),
        ("--redo", redo_count),
        ("--reload", reload_conflicts),
    ):
        if given and not copy_to:
            _fail(f"{flag} needs a working copy with a history (--copy-to DIR).")
    from aa_recipe_manager.editing.cli_args import split_assignment

    try:
        parsed_inputs = dict(
            split_assignment(item, "--input value", "NAME=VALUE") for item in inputs
        )
    except ValueError as exc:
        _fail(str(exc))
    try:
        run_config = config.load_run_config(config_path, recipe_path=recipe)
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    if run_config.source is not None and not as_json:
        click.echo(f"Using run config: {run_config.source}")
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    journal = editing.Journal(Path(copy_to) / ".aa-recipe") if copy_to else None
    continuing = journal is not None and journal.exists()
    try:
        if continuing:
            workspace = editing.RecipeWorkspace.reopen(
                Path(copy_to) / Path(recipe).name, recipe, journal=journal
            )
        else:
            workspace = editing.RecipeWorkspace.open(recipe)
    except editing.WorkspaceError as exc:
        _fail(str(exc))
    except RecipeParseError as exc:
        _handle_recipe_errors(exc)

    disk: dict[str, Any] = {"reloaded": [], "conflicts": []}
    if continuing:
        for warning in workspace.warnings:
            click.echo(f"Warning: {warning}", err=True)
        synced = workspace.sync_disk()
        disk["reloaded"] = [entry.documents[0] for entry in synced.reloaded]
        conflicts = synced.conflicts
        if reload_conflicts and conflicts:
            try:
                workspace.reload([conflict.label for conflict in conflicts])
            except editing.EditRejected as exc:
                _fail("Reload refused: " + "; ".join(exc.problems))
            disk["reloaded"] += [conflict.label for conflict in conflicts]
            conflicts = []
        disk["conflicts"] = [
            {"label": c.label, "error": c.error, "disk_sha256": c.disk_digest}
            for c in conflicts
        ]
        if not as_json:
            for label in disk["reloaded"]:
                click.echo(f"reloaded {label} from disk")
            for conflict in conflicts:
                reason = f": {conflict.error}" if conflict.error else ""
                click.echo(f"changed on disk, kept: {conflict.label}{reason}")
        for name, done, step, count in (
            ("undo", "undid", workspace.undo, undo_count),
            ("redo", "redid", workspace.redo, redo_count),
        ):
            for _ in range(count or 0):
                entry = step()
                if entry is None:
                    if not as_json:
                        click.echo(f"Nothing more to {name}.")
                    break
                if not as_json:
                    click.echo(f"{done} {_describe_op(entry.op)}")
        workspace.flush()
    elif (undo_count or redo_count) and not as_json:
        click.echo("Nothing to undo or redo: the copy has no history yet.")

    ops = _edit_operations(
        workspace,
        ops_file,
        sets,
        unsets,
        checkpoints,
        overrides,
        unset_overrides,
        input_defaults,
        replace_texts,
    )
    applied = 0

    def take_back() -> None:
        """Undo this run's edits in a continued copy, keeping the copy in step."""
        if continuing:
            for _ in range(applied):
                workspace.undo()
            workspace.flush()

    for op in ops:
        try:
            if workspace.apply(op, inputs=merged_inputs or None) is not None:
                applied += 1
        except editing.EditRejected as exc:
            take_back()
            _fail(f"{_describe_op(op)} refused: " + "; ".join(exc.problems))
        except (editing.EditError, ValueError) as exc:
            take_back()
            _fail(f"{_describe_op(op)}: {exc}")

    report = workspace.validate(merged_inputs or None)
    refused = check and not report.is_valid
    if refused:
        take_back()
    dirty = workspace.dirty()
    history = [entry.to_dict() for entry in workspace.history.undo_stack]
    elsewhere = bool(output) and Path(output).resolve().parent != workspace.base_dir
    diffs = {
        label: _edit_diff(
            label,
            workspace.document(label).saved_text or "",
            workspace.document(label).text,
        )
        for label in dirty
    }
    if not as_json:
        if dry_run:
            for diff in diffs.values():
                click.echo(diff, nl=False)
            if not dirty:
                click.echo("No changes.")
        elif not refused:
            for label in dirty:
                click.echo(f"edited {label}")
        _echo_validation(report)

    written: list[Path] = []
    result = None
    if not refused and (output or not dry_run):
        in_place = [d for d in dirty if d != workspace.label(workspace.root)]
        if output and not elsewhere and in_place and not as_json:
            click.echo(
                "Warning: Save As beside the original saves the edited sub-recipes "
                f"in place ({', '.join(in_place)}), which changes the original "
                "recipe and every other recipe that includes them; use --override "
                "on the include, or --output into another folder, to leave them "
                "as they are.",
                err=True,
            )
        try:
            if output:
                result = workspace.save_as(
                    output,
                    paths="fingerprinted" if keep_paths else "all",
                    copy_config=not no_config,
                    config=run_config.source,
                    inputs=merged_inputs,
                    overwrite=overwrite,
                    dry_run=dry_run,
                )
                if elsewhere and not as_json:
                    steps = len(workspace.recipe.steps)
                    _echo_save_as(result, steps, keep_paths)
                if not dry_run:
                    written = result.written
            elif continuing:
                written = workspace.flush()
            elif copy_to:
                written = workspace.start_copy(copy_to, journal=journal)
            else:
                written = workspace.save(overwrite=overwrite)
        except editing.TargetExists as exc:
            listed = ", ".join(str(path) for path in exc.paths)
            _fail(
                f"Save As would replace existing files: {listed}. Choose another "
                "name or folder, or pass --overwrite to replace them."
            )
        except editing.ChangedOnDisk as exc:
            listed = ", ".join(str(path) for path in exc.paths)
            _fail(
                f"Changed on disk since they were read: {listed}. Nothing was "
                "written; pass --overwrite to replace them."
            )
        except (editing.EditError, RecipeParseError) as exc:
            _fail(str(exc))
        if not as_json and dry_run:
            for path in result.written:
                click.echo(f"would write {path}")
            for path in [] if overwrite else result.existing:
                click.echo(f"Warning: {path} exists; it needs --overwrite.", err=True)
        elif not as_json:
            for path in dict.fromkeys(written):
                click.echo(f"wrote {path}")
            if not written and not dirty:
                click.echo("No changes.")

    if refused and not as_json:
        if continuing:
            click.echo(
                "Edits taken back in the copy: --check and the recipe has errors.",
                err=True,
            )
        else:
            click.echo("Nothing written: --check and the recipe has errors.", err=True)
    if as_json:
        payload: dict[str, Any] = {
            "files": [
                {
                    "label": label,
                    "path": str(workspace.document(label).path),
                    "origin": str(workspace.document(label).origin),
                    "dirty": workspace.document(label).dirty,
                }
                for label in workspace.files()
            ],
            "history": history,
            "validation": {
                "valid": report.is_valid,
                "errors": list(report.errors),
                "warnings": list(report.warnings),
            },
            "written": [str(path) for path in dict.fromkeys(written)],
        }
        if dry_run:
            payload["diffs"] = diffs
        if continuing:
            payload["disk"] = disk
        if result is not None:
            payload["save_as"] = result.to_dict()
        click.echo(json.dumps(payload, indent=2, default=str))
    if refused:
        sys.exit(1)


@main.group("config")
def config_group() -> None:
    """Create and edit a recipe's run config file, keeping its comments."""


@config_group.command("init")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option(
    "--overwrite",
    is_flag=True,
    default=False,
    help="Replace an existing <stem>.config.yaml.",
)
def config_init_cmd(recipe: str, overwrite: bool) -> None:
    """Write RECIPE's own <stem>.config.yaml beside it, from the template.

    Every run setting is listed as null with a comment, and each input the
    recipe requires with a null value to fill in. A run of the recipe finds
    the file by the <recipe>.config.yaml rule.
    """
    from aa_recipe_manager.editing import TargetExists, run_config
    from aa_recipe_manager.parser.yaml_reader import load_recipe

    try:
        target = run_config.create(load_recipe(recipe), recipe, overwrite=overwrite)
    except TargetExists as exc:
        _fail(f"{exc} Pass --overwrite to replace it.")
    except RecipeParseError as exc:
        _handle_recipe_errors(exc)
    click.echo(f"wrote {target}")


@config_group.command("set")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.argument("settings", nargs=-1, metavar="[NAME=VALUE]...")
@click.option(
    "--input",
    "inputs",
    multiple=True,
    metavar="NAME=VALUE",
    help="Set a value in the file's inputs: (repeatable).",
)
@click.option(
    "--unset-input",
    "unset_inputs",
    multiple=True,
    metavar="NAME",
    help="Remove a value from the file's inputs: (repeatable).",
)
@click.option(
    "--config",
    "config_path",
    default=None,
    type=click.Path(dir_okay=False),
    help="The run config file to edit, found as for run when omitted.",
)
@click.option(
    "--only-this-recipe",
    is_flag=True,
    default=False,
    help=(
        "Write the changes to a copy of the config file as RECIPE's own "
        "<stem>.config.yaml, leaving the file other recipes share alone."
    ),
)
def config_set_cmd(
    recipe: str,
    settings: tuple[str, ...],
    inputs: tuple[str, ...],
    unset_inputs: tuple[str, ...],
    config_path: str | None,
    only_this_recipe: bool,
) -> None:
    """Set run settings and input values in the run config RECIPE uses.

    Each NAME=VALUE is a run setting: user_cache_dir, survey_cache_dir,
    outputs_dir, temp_dir, executor, dask_scheduler, dask_workers or
    storage_options. VALUEs are read as YAML, as for edit; an empty VALUE
    writes null. A whole number for a float input is written as a float, as
    the client writes it. Only the lines that change are rewritten, so the
    file keeps its comments and layout.

    With --only-this-recipe the file in use is copied, comments and all, to
    RECIPE's own <stem>.config.yaml with the changes made there. A run finds
    that file first, so RECIPE then takes every setting from it and no longer
    follows the shared file.
    """
    from aa_recipe_manager.editing import (
        RecipeDocument,
        TargetExists,
        run_config,
        typed_value,
    )
    from aa_recipe_manager.editing.cli_args import (
        finite_value,
        split_assignment,
        yaml_value,
    )
    from aa_recipe_manager.parser.yaml_reader import load_recipe

    if not (settings or inputs or unset_inputs):
        _fail("Nothing to set: give NAME=VALUE, --input or --unset-input.")
    try:
        declared = load_recipe(recipe).inputs if inputs else {}
    except RecipeParseError as exc:
        _handle_recipe_errors(exc)
    try:
        path = config.discover_config_path(config_path, recipe_path=recipe)
        values = {}
        for item in settings:
            name, text = split_assignment(item, "A setting", "NAME=VALUE")
            values[name] = yaml_value(text, name)
        given = {}
        for item in inputs:
            name, text = split_assignment(item, "--input", "NAME=VALUE")
            value = finite_value(text, "--input")
            if name in declared:
                given[name] = typed_value(value, declared[name].type)
            else:
                given[name] = value
                click.echo(
                    f"Warning: {Path(recipe).name} declares no input '{name}'; "
                    "it is written, but not used by this recipe.",
                    err=True,
                )
    except (FileNotFoundError, ValueError) as exc:
        _fail(str(exc))
    if path is None:
        _fail(
            f"{Path(recipe).name} uses no run config file; create one with "
            "'aa-recipe config init', or name one with --config."
        )
    try:
        document = RecipeDocument.load(path)
        if only_this_recipe:
            document = run_config.copy_to(
                document, run_config.config_candidate(recipe)
            )
        written = (run_config.contents(document) or {}).get("inputs", {})
        new_inputs = None
        if given or unset_inputs:
            kept = {k: v for k, v in written.items() if k not in unset_inputs}
            new_inputs = {**kept, **given}
        run_config.apply(document, new_inputs, values)
    except TargetExists as exc:
        _fail(f"{exc} Edit it without --only-this-recipe.")
    except (RecipeParseError, ValueError) as exc:
        _fail(f"{path}: {exc}")
    if not only_this_recipe and not document.dirty:
        click.echo(f"No changes to {path}.")
        return
    click.echo(f"wrote {document.write()}")


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
    "--user-cache-dir",
    default=None,
    help=(
        "User cache root to probe. Defaults to the run config's user_cache_dir, "
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
    user_cache_dir: str | None,
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
    user_cache_dir = (
        user_cache_dir or run_config.user_cache_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    )
    survey_cache_dir = survey_cache_dir or run_config.survey_cache_dir
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    try:
        explanation = api.explain_cache(
            recipe,
            inputs=merged_inputs or None,
            user_cache_dir=user_cache_dir,
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


@main.command("artifacts")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.argument("step_id")
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
    "--user-cache-dir",
    default=None,
    help=(
        "User cache root to probe. Defaults to the run config's user_cache_dir, "
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
    "--base-dir",
    default=None,
    type=click.Path(exists=True, file_okay=False),
    help=(
        "Resolve the recipe's relative paths against PATH instead of the "
        "working directory, as a run started there would."
    ),
)
@click.option(
    "--json",
    "as_json",
    is_flag=True,
    default=False,
    help="Emit the answer as JSON instead of text.",
)
def artifacts_cmd(
    recipe: str,
    step_id: str,
    config_path: str | None,
    user_cache_dir: str | None,
    survey_cache_dir: str | None,
    inputs: tuple[str, ...],
    base_dir: str | None,
    as_json: bool,
) -> None:
    """List where STEP_ID's checkpointed outputs are, without running RECIPE.

    Looks up the entry the recipe's current parameters hash to in each cache
    tier; for a mapped or swept step, each instance's entry. Each line gives
    the output, its format, the tier it was found in, the instance and its
    item for a mapped step, and the location. Finding nothing is not an
    error. Nothing is written.
    """
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
    user_cache_dir = (
        user_cache_dir or run_config.user_cache_dir or f"./{DEFAULT_OUTPUT_ROOT}"
    )
    survey_cache_dir = survey_cache_dir or run_config.survey_cache_dir
    merged_inputs = {**run_config.inputs, **parsed_inputs}

    try:
        found = api.step_artifacts(
            recipe,
            step_id,
            inputs=merged_inputs or None,
            user_cache_dir=user_cache_dir,
            survey_cache_dir=survey_cache_dir,
            storage_options=run_config.storage_options,
            base_dir=base_dir,
        )
    except Exception as exc:
        _handle_recipe_errors(exc)
        return

    if as_json:
        click.echo(json.dumps(found.to_dict(), indent=2, default=str))
    else:
        click.echo(found.format_text())
