# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Generate the op reference and CLI pages while mkdocs builds the site.

Run by the mkdocs-gen-files plugin. Reads the committed docs/data/ops.json, so
the build needs no scientific packages.
"""

from __future__ import annotations

import json
import posixpath
from pathlib import Path

import mkdocs_gen_files

from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.docs.cli_reference import build_cli_reference

REPO = "https://github.com/BLayman-NOAA/AA-SI_recipe_manager"
SPEC_ROOT = "src/aa_recipe_manager"

docs_dir = Path(mkdocs_gen_files.config["docs_dir"])
payload = json.loads((docs_dir / "data" / "ops.json").read_text(encoding="utf-8"))


def cell(text) -> str:
    """Make a value safe for one Markdown table cell."""
    if text is None or text == "":
        return ""
    return " ".join(str(text).split()).replace("|", "\\|")


def value(text) -> str:
    """A default or param value as inline code."""
    if text is None:
        return ""
    rendered = json.dumps(text) if not isinstance(text, str) else text
    return f"`{cell(rendered)}`"


def page_link(page: str) -> str:
    """Link from an op page to another page of the site."""
    return posixpath.relpath(page, "ops")


def op_page(op: dict) -> str:
    lines = [f"# {op['op']}", ""]
    badges = []
    if op["category"]:
        badges.append(f"Category: **{op['category']}**")
    if op["sink"]:
        badges.append("Sink step: produces files or figures, no outputs")
    if op["experimental"]:
        badges.append("**Experimental**")
    if badges:
        lines += [" · ".join(badges), ""]
    if op["description"]:
        lines += [op["description"], ""]

    if op["inputs"]:
        lines += [
            "## Inputs",
            "",
            "| Name | Type | Required | Description |",
            "| --- | --- | --- | --- |",
        ]
        for port in op["inputs"]:
            required = "yes" if port["required"] else "no"
            lines.append(
                f"| `{port['name']}` | `{cell(port['type'])}` | {required} "
                f"| {cell(port['description'])} |"
            )
        lines.append("")

    if op["outputs"]:
        lines += [
            "## Outputs",
            "",
            "| Name | Type | Description |",
            "| --- | --- | --- |",
        ]
        for port in op["outputs"]:
            lines.append(
                f"| `{port['name']}` | `{cell(port['type'])}` "
                f"| {cell(port['description'])} |"
            )
        lines.append("")

    if op["params"]:
        lines += [
            "## Parameters",
            "",
            "| Name | Type | Units | Default | Description |",
            "| --- | --- | --- | --- | --- |",
        ]
        for param in op["params"]:
            type_ = f"`{cell(param['type'])}`" if param["type"] else ""
            default = value(param["default"])
            if param["required"] and param["default"] is None:
                default = "required"
            lines.append(
                f"| `{param['name']}` | {type_} | {cell(param['units'])} "
                f"| {default} | {cell(param['description'])} |"
            )
        lines.append("")

    lines += ["## Implementation", ""]
    if not op["implementations"]:
        lines += ["No implementation is registered for this op yet.", ""]
    for impl in op["implementations"]:
        lines += implementation(impl, len(op["implementations"]) > 1)

    if op["spec_file"]:
        url = f"{REPO}/blob/main/{SPEC_ROOT}/{op['spec_file']}"
        lines += ["## Spec", "", f"Defined in [`{op['spec_file']}`]({url}).", ""]

    used_by = [use for use in op.get("used_by", []) if use["page"]]
    if used_by:
        lines += ["## Used in", ""]
        for use in used_by:
            lines.append(
                f"- [{use['title']}]({page_link(use['page'])}), step `{use['step']}`"
            )
        lines.append("")
    return "\n".join(lines)


def implementation(impl: dict, several: bool) -> list[str]:
    lines = []
    if several:
        default = " (default)" if impl["default"] else ""
        lines += [f"### `{impl['key']}`{default}", ""]
    lines.append(f"Calls `{impl['callable_path']}`.")
    dependency = impl["dependency"]
    if dependency:
        where = f" from {dependency['url']}" if dependency.get("url") else ""
        lines.append(
            f"Requires `{dependency['name']} {dependency['version']}`"
            f" ({dependency['source']}{where})."
        )
    lines.append("")

    source = impl.get("source") or {}
    if source.get("github_url"):
        path = source.get("repo_relative_path") or source["github_url"]
        lines += [
            f"Source: [{path}, line {source['line']}]({source['github_url']})",
            "",
        ]
    if source.get("signature"):
        lines += ["```python", source["signature"], "```", ""]
    if source.get("doc"):
        doc = [f"    {line}" if line else "" for line in source["doc"].splitlines()]
        lines += ['??? note "Docstring"', "", "    ```text", *doc, "    ```", ""]

    renamed = [m for m in impl["param_map"] if m["spec"] != m["callable"]]
    if renamed:
        lines += ["| Recipe param | Passed as |", "| --- | --- |"]
        lines += [f"| `{m['spec']}` | `{m['callable']}` |" for m in renamed]
        lines.append("")
    return lines


def ops_index(ops: list[dict]) -> str:
    lines = [
        "# Op reference",
        "",
        f"Every built-in step a recipe can use: {len(ops)} ops. Each page lists the "
        "op's inputs, outputs and parameters as declared in its spec, the function "
        "it calls, and the recipes on this site that use it.",
        "",
    ]
    by_category: dict[str, list[dict]] = {}
    for op in ops:
        by_category.setdefault(op["category"] or "uncategorized", []).append(op)
    for category in sorted(by_category):
        lines += [
            f"## {category.capitalize()}",
            "",
            "| Op | Description |",
            "| --- | --- |",
        ]
        for op in by_category[category]:
            summary = (op["description"] or "").split(". ")[0].rstrip(".")
            flag = " (experimental)" if op["experimental"] else ""
            lines.append(f"| [`{op['op']}`]({op['op']}.md){flag} | {cell(summary)} |")
        lines.append("")
    return "\n".join(lines)


ops = payload["ops"]
with mkdocs_gen_files.open("ops/index.md", "w") as f:
    f.write(ops_index(ops))

summary = ["* [Overview](index.md)"]
for op in ops:
    path = f"ops/{op['op']}.md"
    with mkdocs_gen_files.open(path, "w") as f:
        f.write(op_page(op))
    if op["spec_file"]:
        mkdocs_gen_files.set_edit_path(path, f"../{SPEC_ROOT}/{op['spec_file']}")
    summary.append(f"* [{op['op']}]({op['op']}.md)")

with mkdocs_gen_files.open("ops/SUMMARY.md", "w") as f:
    f.write("\n".join(summary) + "\n")

with mkdocs_gen_files.open("reference/cli.md", "w") as f:
    # The client server is not documented on the site yet.
    f.write(build_cli_reference(main, exclude={"serve"}))

# The recipe JSON Schema comes from the installed package at build time, so
# it always matches the version the site documents.
with mkdocs_gen_files.open("data/recipe.schema.json", "w") as f:
    f.write(json.dumps(api.export_schema(), indent=2) + "\n")
