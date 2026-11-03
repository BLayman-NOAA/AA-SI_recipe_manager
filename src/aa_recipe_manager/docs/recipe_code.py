# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The site's generated code: each full recipe's notebook and every Python tab.

The Python tab shows a recipe as the function calls ``aa-recipe generate``
writes, with a short comment per step. A sub-recipe usually cannot be
generated on its own because it reads outputs of steps declared in its sibling
includes, so its code is cut from the full recipe's DAG.
"""

from __future__ import annotations

import re
import tempfile
import textwrap
from collections.abc import Iterable
from pathlib import Path
from typing import TYPE_CHECKING

import nbformat

from aa_recipe_manager.generator.backends.notebook import (
    _build_callable_aliases,
    _build_inputs_cell,
    _build_notebook_step_order,
    _build_step_body_lines,
    _build_variable_name_map_from_dag,
    _collect_imports,
    _indent_lines,
    mapped_chain_code,
)
from aa_recipe_manager.parallel import group_mapped_chains
from aa_recipe_manager.resolver.params import input_ref_names

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import DAGNode, PipelineDAG, Recipe
    from aa_recipe_manager.registry.registry import Registry

_PLACEHOLDER = "memory://aa-recipe-docs/{}"
_SENTENCE_END = re.compile(r"(?<!e\.g)(?<!i\.e)\.(?:\s|$)")
_COMMENT_WIDTH = 76


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


def placeholder_dag(
    recipe: Recipe, registry: Registry
) -> tuple[PipelineDAG, dict[str, str]]:
    """Build a recipe's DAG with local paths swapped for placeholders.

    Returns:
        The DAG, and each placeholder mapped to the text code shows in its
        place, for :func:`recipe_code`.
    """
    from aa_recipe_manager.parser.dag_builder import build_dag

    recipe, inputs, restore = _placeholder_paths(recipe, registry)
    dag = build_dag(recipe, registry, input_values=inputs, check_versions=False)
    return dag, restore


def recipe_code(
    dag: PipelineDAG,
    restore: dict[str, str],
    *,
    step_ids: Iterable[str] | None = None,
    labels: dict[str, str] | None = None,
) -> str:
    """A recipe's function calls, each step under a comment summarizing its op.

    For a full recipe the code opens with its imports and the inputs its calls
    use, and a comment names each labelled section where it starts. For a part of one,
    only those steps and their imports are shown; a fan-out loop that also
    runs other steps keeps its ``for`` line and shows each run of the others
    as ``...`` with their label.

    Args:
        dag: A DAG from :func:`placeholder_dag`.
        restore: The placeholders that came with it.
        step_ids: Steps to show, or None for the whole recipe.
        labels: The section each step belongs to, such as the title of the
            level documenting it.

    Returns:
        Python source.
    """
    shown = set(dag.nodes) if step_ids is None else set(step_ids)
    labels = labels or {}
    headings = step_ids is None
    var_name_map = _build_variable_name_map_from_dag(dag)
    aliases = _build_callable_aliases(dag)
    chains = group_mapped_chains(dag)
    chain_by_first = {chain.member_ids[0]: chain for chain in chains}
    followers = {mid for chain in chains for mid in chain.member_ids[1:]}

    blocks: list[list[str]] = []
    section = None

    def heading(step_id: str) -> list[str]:
        nonlocal section
        label = labels.get(step_id)
        if not headings or label is None or label == section:
            return []
        section = label
        return [f"# {label}", ""]

    for step_id in _build_notebook_step_order(dag):
        if step_id in followers:
            continue
        chain = chain_by_first.get(step_id)
        if chain is None:
            if step_id not in shown:
                continue
            node = dag.nodes[step_id]
            blocks.append(
                heading(step_id)
                + _step_comment(step_id, node)
                + _build_step_body_lines(step_id, node, var_name_map, aliases)
            )
            continue

        visible = [mid for mid in chain.member_ids if mid in shown]
        if not visible:
            continue
        members = [dag.nodes[mid] for mid in chain.member_ids]
        code = mapped_chain_code(chain, members, var_name_map, aliases)
        lines = heading(visible[0])
        lines += [line for mid in visible for line in code.accumulators[mid]]
        lines += code.header
        body: list[str] = []
        elided = None
        for mid in chain.member_ids:
            if mid not in shown:
                label = labels.get(mid, "other steps")
                if label != elided:
                    body += ([""] if body else []) + [f"...  # {label}"]
                    elided = label
                continue
            elided = None
            if body:
                body.append("")
            body += heading(mid) + _step_comment(mid, dag.nodes[mid]) + code.bodies[mid]
        blocks.append(lines + _indent_lines(body, prefix=code.indent))

    head = [_collect_imports(dag, aliases, shown)]
    if headings:
        # Params take input values as literals, so only inputs wired to a
        # port appear in the calls.
        calls = _restore_text(
            "\n".join(line for block in blocks for line in block), restore
        )
        inputs = [
            line
            for name, line in zip(
                dag.recipe.inputs, _build_inputs_cell(dag).source.splitlines()
            )
            if re.search(rf"(?<![\w.']){re.escape(name)}\b(?!\s*=[^=])", calls)
        ]
        if inputs:
            head.append(["# Inputs", *inputs])
    text = "\n\n".join("\n".join(block) for block in head + blocks if block)
    return _restore_text(text, restore) + "\n"


def _step_comment(step_id: str, node: DAGNode) -> list[str]:
    """The step id and the first sentence of its op's description, as comments."""
    summary = _first_sentence(node.spec.description or "")
    text = f"{step_id}: {summary}" if summary else step_id
    return [f"# {line}" for line in textwrap.wrap(text, _COMMENT_WIDTH)]


def _first_sentence(description: str) -> str:
    text = " ".join(description.split()).replace("``", "")
    match = _SENTENCE_END.search(text)
    return text[: match.start() + 1] if match else text


def restore_placeholders(nb: nbformat.NotebookNode, restore: dict[str, str]) -> None:
    """Put the original text back in place of each placeholder, in every cell."""
    for cell in nb.cells:
        cell.source = _restore_text(cell.source, restore)


def _restore_text(text: str, restore: dict[str, str]) -> str:
    """Put the original text back in place of each placeholder.

    Longer placeholders go first, so one that is a prefix of another
    (``literal-1`` and ``literal-10``) cannot be replaced inside it.
    """
    ordered = sorted(restore.items(), key=lambda item: len(item[0]), reverse=True)
    for placeholder, original in ordered:
        text = text.replace(repr(placeholder), repr(original))
        text = text.replace(placeholder, original)
    return text


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
