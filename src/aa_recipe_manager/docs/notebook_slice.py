# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Render generated notebooks, or the part of one a sub-recipe owns, as Markdown.

A sub-recipe usually cannot be generated on its own because it reads outputs
of steps declared in its sibling includes. The site instead generates the full
recipe's notebook once and keeps the cells that belong to one include's steps.
"""

from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import nbformat

from aa_recipe_manager.resolver.params import input_ref_names

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import Recipe
    from aa_recipe_manager.registry.registry import Registry

_STEP_HEADING = re.compile(r"^#{2,3} Step: `([^`]+)`", re.MULTILINE)
_HEADING = re.compile(r"^(#{1,5}) ", re.MULTILINE)
_PLACEHOLDER = "memory://aa-recipe-docs/{}"


@dataclass
class NotebookSlice:
    """The cells of a generated notebook that belong to a set of steps."""

    cells: list[Any] = field(default_factory=list)
    shared_with: list[str] = field(default_factory=list)
    """Steps outside the set whose code shares a cell with steps in it."""


def generate_parent_notebook(
    recipe: Recipe, registry: Registry
) -> nbformat.NotebookNode:
    """Generate a recipe's notebook without tracker or provenance cells.

    Validation requires local ``path`` params to exist, and creates some of
    them, neither of which suits a documentation build. Every local path
    reaching a ``path`` param is validated as a placeholder URL instead, and
    its usual text is put back into the generated cells.

    Args:
        recipe: The full recipe to generate.
        registry: Registry for the recipe's specs.

    Returns:
        The notebook.
    """
    from aa_recipe_manager import api

    recipe, inputs, restore = _placeholder_paths(recipe, registry)
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "recipe.ipynb"
        api.generate(
            recipe,
            output=output,
            output_format="notebook",
            overwrite=True,
            include_provenance=False,
            include_tracker=False,
            inputs=inputs,
        )
        nb = nbformat.read(output, as_version=4)

    restore_placeholders(nb, restore)
    return nb


def restore_placeholders(nb: nbformat.NotebookNode, restore: dict[str, str]) -> None:
    """Put the original text back in place of each placeholder, in every cell.

    Longer placeholders go first, so one that is a prefix of another
    (``literal-1`` and ``literal-10``) cannot be replaced inside it.
    """
    ordered = sorted(restore.items(), key=lambda item: len(item[0]), reverse=True)
    for cell in nb.cells:
        for placeholder, original in ordered:
            cell.source = cell.source.replace(repr(placeholder), repr(original))
            cell.source = cell.source.replace(placeholder, original)


def _placeholder_paths(
    recipe: Recipe, registry: Registry
) -> tuple[Recipe, dict[str, str], dict[str, str]]:
    """Swap the local paths validation would check for placeholder URLs.

    Returns:
        The recipe with literal paths replaced, input values for the inputs
        that feed path params, and each placeholder mapped to the text the
        notebook normally shows in its place.
    """
    from aa_recipe_manager.graph import step_spec
    from aa_recipe_manager.storage import is_remote_url

    inputs: dict[str, str] = {}
    restore: dict[str, str] = {}
    steps = []
    for step in recipe.steps:
        spec = step_spec(step, registry)
        params = dict(step.params)
        for name, decl in spec.params.items():
            value = params.get(name)
            if decl.type != "path" or not isinstance(value, str):
                continue
            refs = input_ref_names(value)
            for input_name in refs:
                placeholder = _PLACEHOLDER.format(input_name)
                declared = recipe.inputs.get(input_name)
                default = declared.default if declared else None
                inputs[input_name] = placeholder
                restore[placeholder] = (
                    default if isinstance(default, str) else f"${{inputs.{input_name}}}"
                )
            if not refs and "${" not in value and not is_remote_url(value):
                placeholder = _PLACEHOLDER.format(f"literal-{len(restore)}")
                restore[placeholder] = value
                params[name] = placeholder
        steps.append(step.model_copy(update={"params": params}))
    return recipe.model_copy(update={"steps": steps}), inputs, restore


def slice_steps(nb: nbformat.NotebookNode, step_ids: list[str]) -> NotebookSlice:
    """Keep the cells that document or run any of ``step_ids``.

    Each step's markdown cell opens with ``Step: `<id>```. A code cell belongs
    to the step markdown cells directly above it, which for a fan-out loop is
    every member of the chain. A loop that also runs steps outside the set is
    kept whole, and those steps are reported in ``shared_with``.

    Args:
        nb: A notebook from :func:`generate_parent_notebook`.
        step_ids: Steps to keep.

    Returns:
        The kept cells, in notebook order.
    """
    wanted = set(step_ids)
    result = NotebookSlice()
    pending: list[str] = []
    for cell in nb.cells:
        if cell.cell_type == "markdown":
            match = _STEP_HEADING.match(cell.source)
            if match is None:
                continue
            pending.append(match.group(1))
            if match.group(1) in wanted:
                result.cells.append(cell)
            continue
        owners, pending = pending, []
        if not wanted.intersection(owners):
            continue
        result.cells.append(cell)
        for owner in owners:
            if owner not in wanted and owner not in result.shared_with:
                result.shared_with.append(owner)
    return result


def cells_to_markdown(cells: list[Any], *, heading_shift: int = 0) -> str:
    """Render notebook cells as Markdown.

    Args:
        cells: Notebook cells.
        heading_shift: Levels to demote every markdown heading by.

    Returns:
        Markdown text with code cells as fenced Python blocks.
    """
    blocks: list[str] = []
    for cell in cells:
        source = cell.source.strip("\n")
        if not source:
            continue
        if cell.cell_type == "markdown":
            if heading_shift:
                source = _HEADING.sub(
                    lambda m: "#" * min(len(m.group(1)) + heading_shift, 6) + " ",
                    source,
                )
            blocks.append(source)
        elif cell.cell_type == "code":
            fence = "```"
            while fence in source:
                fence += "`"
            blocks.append(f"{fence}python\n{source}\n{fence}")
    return "\n\n".join(blocks) + "\n"


def notebook_to_markdown(nb: nbformat.NotebookNode, *, heading_shift: int = 0) -> str:
    """Render a whole notebook as Markdown."""
    return cells_to_markdown(nb.cells, heading_shift=heading_shift)
