# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Turn a recipe into the node and edge document flow diagrams draw.

The documentation site and the recipe manager client both draw this document.
Unlike :func:`aa_recipe_manager.parser.dag_builder.build_dag`, this accepts a
sub-recipe on its own. References to steps the file does not declare become
entry nodes at the top of the diagram instead of validation errors, and
outputs nothing in the file consumes become exit nodes at the bottom, so a
level's sub-recipe shows the data flowing into it and out of it.

Each include entry lists the input declarations of its own file, and each
input node names the file that declares it, so a reader can tell the root
file's inputs from those a sub-recipe brought in.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.exceptions import SpecNotFoundError
from aa_recipe_manager.model.types import Spec
from aa_recipe_manager.parser.dag_builder import resolve_custom_spec
from aa_recipe_manager.plain_values import clean_text, json_safe
from aa_recipe_manager.resolver.params import (
    contains_item_ref,
    input_ref_names,
    parse_input_ref,
    parse_ref,
)

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import (
        IncludeBlock,
        InputDeclaration,
        Recipe,
        Step,
    )
    from aa_recipe_manager.registry.registry import Registry

#: Bumped when the graph document changes shape in a way the page must follow.
GRAPH_SCHEMA = 2


@dataclass
class _Ref:
    """Where one ``${...}`` reference in a step points."""

    node: str
    name: str


def build_recipe_graph(
    recipe: Recipe,
    registry: Registry,
    *,
    slug: str,
    title: str,
    source: str,
    parent: str | None = None,
    level: int | None = None,
    cruise: str | None = None,
    ops_base: str = "ops/",
    hashes: dict[str, str] | None = None,
    allow_unknown_ops: bool = False,
) -> dict[str, Any]:
    """Build the flow-diagram document for one recipe or sub-recipe.

    Args:
        recipe: A loaded recipe. Includes, if any, are already flattened.
        registry: Registry used to look up each step's spec.
        slug: Identifier the site uses for this recipe's data files.
        title: Human readable title.
        source: Path of the recipe file as the site serves it.
        parent: Slug of the full recipe this one is included by, if any.
        level: Processing level the recipe documents, if any.
        cruise: Survey the example comes from, if any.
        ops_base: URL prefix for op reference pages, relative to the site root.
        hashes: Step hashes by step id, shown on step nodes when given.
        allow_unknown_ops: Draw a step whose op has no spec as a step with no
            ports, instead of raising.

    Returns:
        A dict containing only JSON-serializable values. Each entry of
        ``includes`` carries ``inputs``, the declarations of its own file,
        and each input node carries ``declared_in``, the root document or the
        ``source`` of the include whose file declares it.

    Raises:
        SpecNotFoundError: A step names an op the registry does not have, and
            ``allow_unknown_ops`` is false.
    """
    builder = _GraphBuilder(
        recipe, registry, ops_base, source, hashes, allow_unknown_ops
    )
    return builder.build(
        {
            "name": recipe.name,
            "description": clean_text(recipe.description),
            "version": recipe.version,
            "source": source,
            "parent": parent,
            "level": level,
            "cruise": cruise,
        },
        slug=slug,
        title=title,
    )


def step_spec(step: Step, registry: Registry) -> Spec:
    """The spec a step runs against, including inline custom specs.

    Follows the executor's rule: a step is custom when its ``op`` is
    ``custom``, and a ``custom_spec`` on any other op is ignored.
    """
    if step.op != "custom":
        return registry.get_spec(step.op)
    if step.custom_spec is None:
        raise SpecNotFoundError(
            f"Step '{step.id}': op is 'custom' but no custom_spec provided."
        )
    return resolve_custom_spec(step, registry)


def _declared_inputs(inputs: dict[str, Any]) -> list[dict[str, Any]]:
    """Input declarations of one file as the graph lists them, in file order.

    Tolerant of a declaration the model would reject: an input the parent
    overrides is never validated, so the graph must not fail on it.
    """
    entries = []
    for name, decl in inputs.items():
        if not isinstance(decl, dict):
            decl = {}
            required = True
        else:
            required = decl.get("required", "default" not in decl)
        entries.append(
            {
                "name": name,
                "type": decl.get("type"),
                "required": required,
                "default": json_safe(decl.get("default")),
                "description": clean_text(decl.get("description")),
            }
        )
    return entries


def _required(decl: InputDeclaration) -> bool:
    """Whether an input must be set; an explicit ``default: null`` is optional."""
    if "required" not in decl.model_fields_set and "default" in decl.model_fields_set:
        return False
    return decl.required


def _custom_spec(step: Step):
    """The custom spec the executor would use, or None."""
    return step.custom_spec if step.op == "custom" else None


class _GraphBuilder:
    """Accumulates nodes and edges for one recipe."""

    def __init__(
        self,
        recipe: Recipe,
        registry: Registry,
        ops_base: str,
        source: str,
        hashes: dict[str, str] | None,
        allow_unknown_ops: bool = False,
    ) -> None:
        self.recipe = recipe
        self.registry = registry
        self.ops_base = ops_base
        self.root_document = Path(source).name
        self.hashes = hashes
        self.allow_unknown_ops = allow_unknown_ops
        self.specs = {step.id: self._spec(step) for step in recipe.steps}
        self.step_ids = {step.id for step in recipe.steps}
        self.entries: dict[str, dict[str, Any]] = {}
        self.edges: list[dict[str, Any]] = []
        self.edge_keys: set[tuple[Any, ...]] = set()
        self.consumed: set[tuple[str, str]] = set()
        self.groups: list[dict[str, Any]] = []
        self.group_of: dict[str, dict[str, Any]] = {}
        self.blocks: dict[int, IncludeBlock] = {}
        self.includes = self._include_tree()
        self.include_of = self._innermost_includes()
        self.input_origins = self._input_origins()

    def _spec(self, step: Step) -> Spec:
        """The step's spec, or an empty stand in for an op that cannot be found."""
        try:
            return step_spec(step, self.registry)
        except SpecNotFoundError:
            if not self.allow_unknown_ops:
                raise
            return Spec(op=step.op, description=f"Unknown op '{step.op}'.")

    def build(
        self, recipe_info: dict[str, Any], *, slug: str, title: str
    ) -> dict[str, Any]:
        self._group_mapped_steps()
        # Every declared input gets a node, read or not, in declaration order.
        for name in self.recipe.inputs:
            self._input_entry(name, None, None)
        steps = []
        for step in self.recipe.steps:
            steps.append(self._step_node(step))
            self._wire(step)
        exits = self._exit_nodes()
        self._place_in_groups(exits)
        for group in self.groups:
            group.pop("edge", None)
        self._consumed_by_includes()
        return {
            "schema": GRAPH_SCHEMA,
            "slug": slug,
            "title": title,
            "recipe": recipe_info,
            "nodes": [*self.entries.values(), *steps, *exits],
            "edges": self.edges,
            "groups": self.groups,
            "includes": self.includes,
        }

    def _include_tree(self) -> list[dict[str, Any]]:
        """One entry per include block with steps in this recipe.

        A block's parent is the smallest other block containing its steps.
        When two blocks hold the same steps, as when a sub-recipe only
        includes another, the later one is the parent, because the loader
        lists children before their parents.
        """
        position = {step.id: index for index, step in enumerate(self.recipe.steps)}
        includes = []
        for block in self.recipe.include_blocks:
            steps = [step_id for step_id in block.step_ids if step_id in position]
            if not steps:
                continue
            source = block.path or block.source
            includes.append(
                {
                    "id": f"inc:{source}",
                    "source": source,
                    "name": block.name,
                    "title": block.name or Path(block.source).stem,
                    "description": clean_text(block.description),
                    "parent": None,
                    "position": min(position[step_id] for step_id in steps),
                    "steps": steps,
                    "overrides": json_safe(block.overrides),
                    "map_over": block.map_over,
                    "bindings": block.bindings,
                    "inputs": _declared_inputs(block.inputs),
                    "consumed": [],
                }
            )
            self.blocks[id(includes[-1])] = block
        for index, include in enumerate(includes):
            members = set(include["steps"])
            parents = [
                (len(other["steps"]), order, other["id"])
                for order, other in enumerate(includes)
                if members < set(other["steps"])
                or (members == set(other["steps"]) and order > index)
            ]
            if parents:
                include["parent"] = min(parents)[2]
        return includes

    def _input_origins(self) -> dict[str, str]:
        """The include source declaring each input a sub-recipe brought in.

        Follows each top level include's promoted names down to the file
        that declares them. Inputs not listed are the root document's.
        """

        def trace(include: dict[str, Any], name: str) -> str:
            if name in self.blocks[id(include)].inputs:
                return include["source"]
            for child in self.includes:
                if child["parent"] != include["id"]:
                    continue
                if name in self.blocks[id(child)].promoted:
                    return trace(child, name)
            return include["source"]

        origins: dict[str, str] = {}
        for include in self.includes:
            if include["parent"] is not None:
                continue
            for name in self.blocks[id(include)].promoted:
                origins.setdefault(name, trace(include, name))
        return origins

    def _innermost_includes(self) -> dict[str, dict[str, Any]]:
        """The smallest include containing each step that is in one."""
        include_of: dict[str, dict[str, Any]] = {}
        for include in sorted(self.includes, key=lambda i: len(i["steps"])):
            for step_id in include["steps"]:
                include_of.setdefault(step_id, include)
        return include_of

    def _consumed_by_includes(self) -> None:
        """List each include's outputs that steps outside it read."""
        for include in self.includes:
            members = set(include["steps"])
            readers: dict[tuple[str, str], set[str]] = {}
            for edge in self.edges:
                if edge["kind"] in ("item", "depends", "exit"):
                    continue
                if edge["source"] not in members or edge["target"] in members:
                    continue
                key = (edge["source"], edge["source_output"])
                readers.setdefault(key, set()).add(edge["target"])
            include["consumed"] = [
                {"step": step_id, "output": output, "by": sorted(by)}
                for (step_id, output), by in readers.items()
            ]

    def _place_in_groups(self, exits: list[dict[str, Any]]) -> None:
        """Put entry and exit points that belong to one mapped group inside it.

        An input read only by steps of one group, other than the list the
        group maps over, is drawn inside the group, and so is an output of a
        mapped step. Drawn outside, the layout pushes them to the far side of
        the group.
        """
        targets: dict[str, set[str | None]] = {}
        map_sources: set[str] = set()
        for edge in self.edges:
            if edge["source"] not in self.entries:
                continue
            group = self.group_of.get(edge["target"])
            group_id = group["id"] if group else None
            targets.setdefault(edge["source"], set()).add(group_id)
            if edge["kind"] == "map_over":
                map_sources.add(edge["source"])
        for node_id, node in self.entries.items():
            groups = targets.get(node_id, set())
            if len(groups) == 1 and None not in groups and node_id not in map_sources:
                node["group"] = next(iter(groups))
        for node in exits:
            group = self.group_of.get(node["from"]["step"])
            if group is not None:
                node["group"] = group["id"]

    def _group_mapped_steps(self) -> None:
        """Consecutive steps mapped over the same source form one group."""
        current: dict[str, Any] | None = None
        for step in self.recipe.steps:
            if step.map_over is None:
                current = None
                continue
            if current is not None and current["map_over"] == step.map_over:
                current["members"].append(step.id)
            else:
                source = self._resolve(step.map_over, step)
                current = {
                    "id": f"group:{len(self.groups)}",
                    "kind": "map",
                    "map_over": step.map_over,
                    "label": f"Runs once per item of {_bare(step.map_over)}",
                    "source": source.node if source else None,
                    "members": [step.id],
                }
                self.groups.append(current)
            self.group_of[step.id] = current

    def _step_node(self, step: Step) -> dict[str, Any]:
        spec = self.specs[step.id]
        custom = _custom_spec(step)
        if custom is not None:
            op_url = f"{self.ops_base}{custom.extends}/" if custom.extends else None
        else:
            op_url = f"{self.ops_base}{step.op}/"

        inputs = [
            {
                "name": name,
                "type": port.type,
                "required": port.required,
                "description": clean_text(port.description),
                "value": json_safe(step.inputs.get(name)),
                "many": port.many,
                "semantic": None,
            }
            for name, port in spec.inputs.items()
        ]
        inputs += [
            {
                "name": name,
                "type": None,
                "required": False,
                "description": None,
                "value": json_safe(value),
                "many": False,
                "semantic": None,
            }
            for name, value in step.inputs.items()
            if name not in spec.inputs
        ]

        params = [
            {
                "name": name,
                "type": decl.type,
                "units": decl.units,
                "description": clean_text(decl.description),
                "default": json_safe(decl.default),
                "set": name in step.params,
                "value": json_safe(step.params.get(name)),
            }
            for name, decl in spec.params.items()
        ]
        params += [
            {
                "name": name,
                "type": None,
                "units": None,
                "description": None,
                "default": None,
                "set": True,
                "value": json_safe(value),
            }
            for name, value in step.params.items()
            if name not in spec.params
        ]

        disposed = set(step.dispose_outputs)
        outputs = [
            {
                "name": name,
                "type": port.type,
                "description": clean_text(port.description),
                "disposed": name in disposed or port.disposable,
                "many": port.many,
                "semantic": None,
            }
            for name, port in spec.outputs.items()
        ]

        group = self.group_of.get(step.id)
        include = self.include_of.get(step.id)
        return {
            "id": step.id,
            "kind": "step",
            "op": step.op,
            "op_url": op_url,
            "custom": custom is not None,
            "extends": custom.extends if custom else None,
            "callable_path": custom.callable_path if custom else None,
            "category": spec.category,
            "implementation": (
                None if custom is not None else self._implementation(step.op)
            ),
            "include": include["id"] if include else None,
            "document": include["source"] if include else self.root_document,
            "hash": self.hashes.get(step.id) if self.hashes else None,
            "editable": False,
            "description": clean_text(step.description) or clean_text(spec.description),
            "sink": spec.sink,
            "mapped": step.map_over is not None,
            "group": group["id"] if group else None,
            "collector": step.collect is not None,
            "sweep": json_safe(
                {"mode": step.sweep.mode, "params": step.sweep.param_lists}
                if step.sweep
                else None
            ),
            "checkpoint": step.checkpoint,
            "regenerate": step.regenerate,
            "inputs": inputs,
            "params": params,
            "outputs": outputs,
        }

    def _implementation(self, op: str) -> dict[str, Any] | None:
        """The implementation a registered op runs by default, if it has one."""
        impls = [
            self.registry.get_implementation(op, key, check_versions=False)
            for key in self.registry.list_implementations(op)
        ]
        if not impls:
            return None
        impl = next((i for i in impls if i.default), impls[0])
        return {
            "key": impl.key,
            "callable_path": impl.callable_path,
            "dependency": impl.dependency.name if impl.dependency else None,
        }

    def _wire(self, step: Step) -> None:
        """Add the edges arriving at one step."""
        collected = step.collect
        if collected is not None:
            ref = self._resolve(collected, step)
            if ref is not None:
                target_input = next(
                    (name for name, value in step.inputs.items() if value == collected),
                    None,
                )
                self._edge(ref, step, target_input, "collect")

        group = self.group_of.get(step.id)
        if group is not None and group["members"][0] == step.id:
            ref = self._resolve(step.map_over, step)
            if ref is not None:
                self._edge(ref, step, None, "map_over", group=group["id"])
                group["edge"] = self.edges[-1]
                group["edge"]["label"] = f"{ref.name} -> _item"

        for name, value in step.inputs.items():
            if collected is not None and value == collected:
                continue
            for text in strings_in(value):
                self._wire_value(text, step, name, "data")
        for name, value in step.params.items():
            for text in strings_in(value):
                self._wire_value(text, step, name, "param")

        for dep in step.depends_on or []:
            if dep in self.step_ids:
                ref = _Ref(dep, "")
            else:
                ref = self._entry(f"ext:{dep}", "external", dep, None)
            self._edge(ref, step, None, "depends")

    def _wire_value(self, text: str, step: Step, slot: str, kind: str) -> None:
        if contains_item_ref(text):
            group = self.group_of.get(step.id)
            if group is None or group["source"] is None:
                return
            map_edge = group.get("edge")
            if map_edge is not None and map_edge["target"] == step.id:
                # The group's first step reads the item: one edge says it all,
                # list -> _item -> input.
                slots = map_edge.setdefault("item_inputs", [])
                if slot not in slots:
                    slots.append(slot)
                map_edge["label"] = (
                    f"{map_edge['source_output']} -> _item -> {', '.join(slots)}"
                )
                map_edge["target_input"] = slots[0]
                return
            self._edge(_Ref(group["source"], "_item"), step, slot, "item")
            return
        ref = self._resolve(text, step, slot)
        if ref is not None:
            self._edge(ref, step, slot, kind)
            return
        for name in input_ref_names(text):
            self._edge(self._input_entry(name, step, slot), step, slot, kind)

    def _resolve(
        self, text: str | None, step: Step, slot: str | None = None
    ) -> _Ref | None:
        """Resolve a whole-string reference to the node it names."""
        if text is None:
            return None
        parsed = parse_ref(text)
        if parsed is not None:
            src, out = parsed
            if src in self.step_ids:
                return _Ref(src, out)
            ref = self._entry(
                f"ext:{src}.{out}",
                "external",
                f"{src}.{out}",
                self._slot_type(step, slot),
            )
            return _Ref(ref.node, out)
        name = parse_input_ref(text)
        if name is not None:
            return self._input_entry(name, step, slot)
        return None

    def _input_entry(self, name: str, step: Step | None, slot: str | None) -> _Ref:
        decl = self.recipe.inputs.get(name)
        node_id = f"in:{name}"
        if node_id not in self.entries:
            self.entries[node_id] = {
                "id": node_id,
                "kind": "input",
                "label": f"inputs.{name}",
                "type": decl.type if decl else self._slot_type(step, slot),
                "declared": decl is not None,
                "required": _required(decl) if decl else None,
                "default": json_safe(decl.default) if decl else None,
                "description": clean_text(decl.description) if decl else None,
                "declared_in": (
                    self.input_origins.get(name, self.root_document) if decl else None
                ),
            }
        return _Ref(node_id, name)

    def _entry(self, node_id: str, kind: str, label: str, type_: str | None) -> _Ref:
        if node_id not in self.entries:
            self.entries[node_id] = {
                "id": node_id,
                "kind": kind,
                "label": label,
                "type": type_,
            }
        return _Ref(node_id, label.rsplit(".", 1)[-1])

    def _slot_type(self, step: Step, slot: str | None) -> str | None:
        """Declared type of a step's input or param, used for entry nodes."""
        if slot is None:
            return None
        spec = self.specs[step.id]
        if slot in spec.inputs:
            return spec.inputs[slot].type
        if slot in spec.params:
            return spec.params[slot].type
        return None

    def _edge(
        self,
        ref: _Ref,
        step: Step,
        target_input: str | None,
        kind: str,
        group: str | None = None,
    ) -> None:
        key = (ref.node, ref.name, step.id, target_input, kind)
        if key in self.edge_keys:
            return
        self.edge_keys.add(key)

        if kind == "item":
            type_ = self._slot_type(step, target_input)
        elif ref.node in self.step_ids:
            port = self.specs[ref.node].outputs.get(ref.name)
            type_ = port.type if port else None
            self.consumed.add((ref.node, ref.name))
        else:
            type_ = self.entries[ref.node]["type"]
            if type_ is None:
                type_ = self._slot_type(step, target_input)

        if kind == "depends":
            label = ""
        elif kind == "item":
            label = f"_item -> {target_input}"
        elif target_input and target_input != ref.name:
            label = f"{ref.name} -> {target_input}"
        else:
            label = ref.name

        edge = {
            "source": ref.node,
            "target": step.id,
            "kind": kind,
            "source_output": ref.name or None,
            "target_input": target_input,
            "label": label,
            "type": type_,
        }
        if group is not None:
            edge["group"] = group
        self.edges.append(edge)

    def _exit_nodes(self) -> list[dict[str, Any]]:
        """Declared recipe outputs, then every output nothing here consumes."""
        exits: list[dict[str, Any]] = []
        declared: set[tuple[str, str]] = set()
        for name, decl in (self.recipe.outputs or {}).items():
            if decl.step_id not in self.step_ids:
                continue
            declared.add((decl.step_id, decl.output_name))
            port = self.specs[decl.step_id].outputs.get(decl.output_name)
            exits.append(
                self._exit(
                    f"out:{name}",
                    name,
                    decl.step_id,
                    decl.output_name,
                    port.type if port else None,
                    True,
                )
            )

        for step in self.recipe.steps:
            spec = self.specs[step.id]
            if spec.sink:
                continue
            for name, port in spec.outputs.items():
                key = (step.id, name)
                if key in self.consumed or key in declared:
                    continue
                if port.disposable or name in step.dispose_outputs:
                    continue
                exits.append(
                    self._exit(
                        f"out:{step.id}.{name}", name, step.id, name, port.type, False
                    )
                )
        return exits

    def _exit(
        self,
        node_id: str,
        label: str,
        step_id: str,
        output: str,
        type_: str | None,
        declared: bool,
    ) -> dict[str, Any]:
        self.edges.append(
            {
                "source": step_id,
                "target": node_id,
                "kind": "exit",
                "source_output": output,
                "target_input": None,
                "label": output,
                "type": type_,
            }
        )
        return {
            "id": node_id,
            "kind": "output",
            "label": f"{step_id}.{output}" if not declared else label,
            "type": type_,
            "declared": declared,
            "from": {"step": step_id, "output": output},
        }


def strings_in(value: Any) -> list[str]:
    """Every string inside a nested input or param value."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in strings_in(item)]
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings_in(item)]
    return []


def _bare(text: str) -> str:
    """``${a.b}`` without its braces, for labels."""
    return text[2:-1] if text.startswith("${") and text.endswith("}") else text
