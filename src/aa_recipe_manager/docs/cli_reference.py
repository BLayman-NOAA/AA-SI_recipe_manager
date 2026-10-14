# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Render the ``aa-recipe`` command line help as one Markdown page."""

from __future__ import annotations

import click

#: Example invocations shown under each command, keyed by command path.
EXAMPLES: dict[str, list[str]] = {
    "generate": [
        "aa-recipe generate quickstart.yaml -o quickstart.ipynb",
        "aa-recipe generate quickstart.yaml --format script -o quickstart.py",
    ],
    "dry-run": [
        "aa-recipe dry-run quickstart.yaml",
        "aa-recipe dry-run quickstart.yaml --visualize",
    ],
    "deps": [
        "aa-recipe deps quickstart.yaml --format requirements -o requirements.txt",
    ],
    "schema": ["aa-recipe schema -o recipe.schema.json"],
    "graph": [
        "aa-recipe graph quickstart.yaml",
        "aa-recipe graph quickstart.yaml --json",
    ],
    "ops": [
        "aa-recipe ops",
        "aa-recipe ops --category calibration --json",
    ],
    "serve": [
        "aa-recipe serve --recipe-root recipes",
        "aa-recipe serve --recipe-root recipes --recipe-root shared --port 8800",
    ],
    "docs": ["aa-recipe docs --no-source-links -o op_reference.html --open"],
    "site-data": [
        "aa-recipe site-data",
        "aa-recipe site-data --check",
    ],
    "env create": [
        "aa-recipe env create quickstart.yaml --path .venv-quickstart",
        "aa-recipe env create outputs/provenance/provenance.yaml --path .venv-replay",
    ],
    "run": [
        "aa-recipe run quickstart.yaml",
        "aa-recipe run quickstart.yaml --user-cache-dir ./outputs --input "
        "file_time_start=2016-07-25T21:00:00",
        "aa-recipe run survey.yaml --executor dask --dask-scheduler processes "
        "--dask-workers 4",
        "aa-recipe run dive_analysis.yaml --to dive_sv",
        "aa-recipe run dive_analysis.yaml --to dive_sv --only --force-step dive_sv",
        "aa-recipe run dive_analysis.yaml --to dive_sv --events run_events.jsonl",
        "aa-recipe run survey.yaml --cancel-file ./stop",
    ],
    "batch": [
        "aa-recipe batch per_file.yaml --input-dir gs://bucket/raw --input-name "
        "raw_file --pattern '*.raw'",
    ],
    "doctor": ["aa-recipe doctor quickstart.yaml"],
    "clean": [
        "aa-recipe clean quickstart.yaml --stale --dry-run",
        "aa-recipe clean quickstart.yaml --all",
    ],
    "explain-cache": ["aa-recipe explain-cache quickstart.yaml"],
    "preview": [
        "aa-recipe preview dive_analysis.yaml --to dive_sv",
        "aa-recipe preview quickstart.yaml --json",
    ],
}

_WIDTH = 100


def build_cli_reference(group: click.Group, prog: str = "aa-recipe") -> str:
    """Build the CLI reference page from a click group's own help text.

    Args:
        group: The top-level command group.
        prog: Program name shown in usage lines.

    Returns:
        Markdown with a summary table and one section per command.
    """
    root = click.Context(
        group, info_name=prog, terminal_width=_WIDTH, max_content_width=_WIDTH
    )
    commands = list(_walk(group, root, []))

    lines = [
        "# CLI reference",
        "",
        "Every command runs as `aa-recipe <command>`. This page is generated "
        "from the commands' own `--help` text.",
        "",
        "| Command | Summary |",
        "| --- | --- |",
    ]
    for path, command, _ in commands:
        anchor = path.replace(" ", "-")
        summary = command.get_short_help_str(limit=_WIDTH).replace("|", "\\|")
        lines.append(f"| [`{path}`](#{anchor}) | {summary} |")

    for path, command, ctx in commands:
        anchor = path.replace(" ", "-")
        lines += [
            "",
            f"## {path} {{#{anchor}}}",
            "",
            "```text",
            command.get_help(ctx).rstrip(),
            "```",
        ]
        examples = EXAMPLES.get(path)
        if examples:
            lines += ["", "Examples:", "", "```bash", *examples, "```"]
    return "\n".join(lines) + "\n"


def _walk(group: click.Group, parent: click.Context, prefix: list[str]):
    """Yield ``(path, command, context)`` for every leaf command in order."""
    for name, command in group.commands.items():
        if command.hidden:
            continue
        ctx = click.Context(
            command,
            info_name=name,
            parent=parent,
            terminal_width=_WIDTH,
            max_content_width=_WIDTH,
        )
        path = [*prefix, name]
        if isinstance(command, click.Group):
            yield from _walk(command, ctx, path)
        else:
            yield " ".join(path), command, ctx
