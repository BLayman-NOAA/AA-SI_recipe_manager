# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Work out how a documented recipe connects to the recipes around it.

A sub-recipe's diagram shows only its own steps. The full recipe that includes
it knows the rest: which step in which level feeds each input, what each input
is set to, and which later steps read what this level leaves behind. The site
can draw those around the diagram. The same knowledge draws a full recipe as
one box per included file.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ruamel.yaml import YAML

from aa_recipe_manager.docs.payload import clean_text, json_safe
from aa_recipe_manager.docs.recipe_graph import GRAPH_SCHEMA, strings_in
from aa_recipe_manager.resolver.params import (
    input_ref_names,
    parse_input_ref,
    parse_ref,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from aa_recipe_manager.model.types import Recipe, Step


@dataclass
class Home:
    """Where a step of a full recipe is documented."""

    slug: str | None
    title: str
    page: str | None
    """Site URL of the page that shows it, relative to the site root."""
    level: int | None = None
    doc: str | None = None
    """The same page as a docs-relative Markdown path, for links in pages."""


def page_url(page: str | None) -> str | None:
    """The site URL of a docs page, relative to the site root."""
    if page is None:
        return None
    path = page[:-3] if page.endswith(".md") else page
    if path == "index":
        return ""
    if path.endswith("/index"):
        return path[: -len("index")]
    return path + "/"


def include_overrides(parent_path: Path, child_path: Path) -> dict[str, Any]:
    """The ``input_overrides`` a parent recipe passes to one included file.

    Args:
        parent_path: The parent recipe file.
        child_path: The included file.

    Returns:
        The overrides as written, or an empty dict when the parent does not
        include that file directly.
    """
    with open(parent_path, encoding="utf-8") as f:
        raw = YAML(typ="safe").load(f) or {}
    target = child_path.resolve()
    for item in raw.get("steps") or []:
        if not isinstance(item, dict) or "include" not in item:
            continue
        if (parent_path.parent / item["include"]).resolve() == target:
            return dict(item.get("input_overrides") or {})
    return {}


def declared_inputs(recipe_path: Path) -> list[str]:
    """Names of the inputs a recipe file declares itself, before includes."""
    with open(recipe_path, encoding="utf-8") as f:
        raw = YAML(typ="safe").load(f) or {}
    return list((raw.get("inputs") or {}).keys())


def recipe_context(
    graph: dict[str, Any],
    full: Recipe,
    homes: dict[str, Home],
    overrides: dict[str, Any],
) -> dict[str, Any] | None:
    """The steps around a recipe's diagram and the values its inputs receive.

    Args:
        graph: The recipe's flow-diagram graph.
        full: The full recipe the steps run in, includes flattened.
        homes: Where each step of ``full`` is documented.
        overrides: What the full recipe sets this recipe's inputs to.

    Returns:
        ``upstream`` steps feeding the diagram's entry points,
        ``downstream`` steps reading its exit points, and ``values`` for
        inputs set to a literal or a parent input. None when there is
        nothing to show.
    """
    mine = {node["id"] for node in graph["nodes"] if node["kind"] == "step"}
    steps = {step.id: step for step in full.steps}

    def describe(step_id: str) -> dict[str, Any]:
        home = homes.get(step_id) or Home(None, "the full recipe", None)
        return {
            "step": step_id,
            "op": steps[step_id].op,
            "recipe": home.slug,
            "title": home.title,
            "page": home.page,
        }

    upstream: list[dict[str, Any]] = []
    values: dict[str, str] = {}
    for node in graph["nodes"]:
        if node["kind"] == "input":
            name = node["id"][len("in:") :]
            if name not in overrides:
                continue
            value = overrides[name]
            ref = parse_ref(value)
            parent_input = parse_input_ref(value)
            if ref is not None and ref[0] in steps and ref[0] not in mine:
                upstream.append(
                    {"target": node["id"], "output": ref[1], **describe(ref[0])}
                )
            elif parent_input is not None:
                values[node["id"]] = f"(from parent input {parent_input})"
            else:
                values[node["id"]] = f"= {_show(value)}"
        elif node["kind"] == "external":
            step_id, _, output = node["label"].partition(".")
            if step_id in steps and step_id not in mine:
                upstream.append(
                    {
                        "target": node["id"],
                        "output": output or None,
                        **describe(step_id),
                    }
                )

    downstream: list[dict[str, Any]] = []
    for node in graph["nodes"]:
        if node["kind"] != "output":
            continue
        source = node["from"]
        ref = "${" + f"{source['step']}.{source['output']}" + "}"
        for step in full.steps:
            if step.id in mine:
                continue
            slot = next((s for s, text in _slots(step) if text == ref), None)
            if slot is not None:
                downstream.append(
                    {"source": node["id"], "input": slot, **describe(step.id)}
                )

    if not (upstream or downstream or values):
        return None
    return {"upstream": upstream, "downstream": downstream, "values": values}


def overview_graph(
    full: Recipe,
    homes: dict[str, Home],
    *,
    slug: str,
    title: str,
    parent_slug: str,
    parent_inputs: list[str],
) -> dict[str, Any]:
    """A full recipe drawn as one box per included file.

    Steps the parent declares itself, such as a plot between two levels, get
    a box each. Edges carry the names of the outputs passed between boxes.

    Args:
        full: The full recipe, includes flattened.
        homes: Where each step is documented.
        slug: Slug for the overview's data file.
        title: Title of the full recipe.
        parent_slug: Manifest slug of the full recipe, which marks the steps
            it declares itself.
        parent_inputs: Inputs the full recipe's own file declares.

    Returns:
        A graph in the same shape as :func:`build_recipe_graph` returns, with
        ``level`` nodes in place of steps.
    """
    steps = {step.id: step for step in full.steps}

    def key(step_id: str) -> str:
        home = homes.get(step_id)
        if home is None or home.slug == parent_slug:
            return f"step:{step_id}"
        return f"lvl:{home.slug or home.title}"

    boxes: dict[str, dict[str, Any]] = {}
    for step in full.steps:
        box_id = key(step.id)
        if box_id in boxes:
            boxes[box_id]["steps"] += 1
            continue
        home = homes.get(step.id)
        if box_id.startswith("step:"):
            boxes[box_id] = {
                "id": box_id,
                "kind": "level",
                "label": step.id,
                "op": step.op,
                "inline": True,
                "level": None,
                "page": None,
                "doc": None,
                "steps": 1,
            }
        else:
            boxes[box_id] = {
                "id": box_id,
                "kind": "level",
                "label": home.title,
                "op": None,
                "inline": False,
                "level": home.level,
                "page": home.page,
                "doc": home.doc,
                "steps": 1,
            }

    passed: dict[tuple[str, str], list[str]] = {}
    for step in full.steps:
        target = key(step.id)
        refs = [parse_ref(text) for _, text in _slots(step)]
        refs += [(dep, "") for dep in step.depends_on or []]
        for ref in refs:
            if ref is None or ref[0] not in steps:
                continue
            source = key(ref[0])
            if source == target:
                continue
            names = passed.setdefault((source, target), [])
            if ref[1] and ref[1] not in names:
                names.append(ref[1])

    edges = [
        {
            "source": source,
            "target": target,
            "kind": "data",
            "source_output": None,
            "target_input": None,
            "label": _list_label(names),
            "type": None,
            "outputs": names,
        }
        for (source, target), names in passed.items()
    ]

    inputs: list[dict[str, Any]] = []
    for name in parent_inputs:
        consumers = []
        for step in full.steps:
            texts = [text for _, text in _slots(step)]
            if any(name in input_ref_names(text) for text in texts):
                if key(step.id) not in consumers:
                    consumers.append(key(step.id))
        if not consumers:
            continue
        decl = full.inputs.get(name)
        inputs.append(
            {
                "id": f"in:{name}",
                "kind": "input",
                "label": f"inputs.{name}",
                "type": decl.type if decl else None,
                "declared": decl is not None,
                "required": decl.required if decl else None,
                "default": json_safe(decl.default) if decl else None,
                "description": clean_text(decl.description) if decl else None,
            }
        )
        for consumer in consumers:
            edges.append(
                {
                    "source": f"in:{name}",
                    "target": consumer,
                    "kind": "param",
                    "source_output": name,
                    "target_input": None,
                    "label": name,
                    "type": decl.type if decl else None,
                }
            )

    return {
        "schema": GRAPH_SCHEMA,
        "slug": slug,
        "title": title,
        "overview": True,
        "recipe": {"name": full.name, "description": clean_text(full.description)},
        "nodes": [*inputs, *boxes.values()],
        "edges": edges,
        "groups": [],
    }


def _slots(step: Step) -> Iterator[tuple[str, str]]:
    """Every string a step reads, with the input or param it sits in."""
    for name, value in step.inputs.items():
        for text in strings_in(value):
            yield name, text
    for name, value in step.params.items():
        for text in strings_in(value):
            yield name, text
    if step.map_over:
        yield "map_over", step.map_over
    if step.collect:
        yield "collect", step.collect


def _list_label(names: list[str]) -> str:
    if len(names) <= 3:
        return ", ".join(names)
    return f"{', '.join(names[:3])} +{len(names) - 3} more"


def _show(value: Any) -> str:
    value = json_safe(value)
    return value if isinstance(value, str) else json.dumps(value)
