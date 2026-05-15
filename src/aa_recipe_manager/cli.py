"""CLI entry point for aa-recipe-manager."""

import json
import logging
import sys

import click

from aa_recipe_manager import api
from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
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


@env_group.command("create")
@click.argument("recipe", type=click.Path(exists=True, dir_okay=False))
@click.option("--path", "env_path", default=None, help="Path for the virtual environment.")
@click.option("--python", "python_exe", default=None, help="Python executable to base the environment on.")
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
    help="Supply a pipeline-level input value for path resolution (repeatable).",
)
def env_create_cmd(
    recipe: str,
    env_path: str | None,
    python_exe: str | None,
    local_pkgs: tuple[str, ...],
    inputs: tuple[str, ...],
) -> None:
    """Create a virtual environment with dependencies for RECIPE."""
    from pathlib import Path as _Path

    parsed_inputs: dict[str, str] = {}
    for item in inputs:
        if "=" not in item:
            _fail(f"--input value must be in NAME=VALUE format, got: {item!r}")
        name, _, value = item.partition("=")
        parsed_inputs[name.strip()] = value.strip()

    parsed_local: dict[str, str] = {}
    for item in local_pkgs:
        if "=" not in item:
            _fail(f"--local-pkg value must be in NAME=PATH format, got: {item!r}")
        name, _, path = item.partition("=")
        parsed_local[name.strip()] = path.strip()

    if env_path is None:
        env_path = f"./{_Path(recipe).stem}_env"

    try:
        result = api.create_env(
            recipe,
            env_path,
            python=python_exe,
            inputs=parsed_inputs or None,
            local_overrides=parsed_local or None,
        )
        click.echo(f"Environment created: {result.env_path}")
        if result.installed:
            click.echo("Installed:")
            for pkg in result.installed:
                click.echo(f"  {pkg}")
        if result.skipped_local:
            click.echo("Local packages without an install path (install manually):")
            for name in result.skipped_local:
                click.echo(f"  pip install -e /path/to/{name}")
        if result.warnings:
            for w in result.warnings:
                click.echo(f"Warning: {w}", err=True)
    except Exception as exc:
        _handle_recipe_errors(exc)
