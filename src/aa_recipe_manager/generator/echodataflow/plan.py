# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Lower a recipe DAG into the tasks of an echodataflow flow.

A task unit is either one ordinary step or one mapped chain: the consecutive
steps sharing a ``map_over`` source, which run in order inside one Prefect task
per item. The plan records, for each unit, what it reads from earlier units,
which recipe inputs it needs, what it hands back to the flow, and which of its
outputs are written to a zarr store and handed on by path.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.generator.core import build_variable_name_map
from aa_recipe_manager.parallel import group_mapped_chains

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import DAGNode, PipelineDAG

_INPUT_REF = re.compile(r"\$\{inputs\.(\w+)\}")
_STEP_REF = re.compile(r"\$\{(\w+)\.(\w+)\}")

#: Output types written to a zarr store when handed off. Other values pass to
#: the next task as they are.
_HEAVY_TYPES = ("Dataset", "DataArray", "EchoData")


class UnsupportedRecipeError(ValueError):
    """The recipe uses a construct the echodataflow backend cannot compile."""


@dataclass
class TaskUnit:
    """One Prefect task of the generated flow.

    Attributes:
        name: Task function suffix, the first member's step id.
        member_ids: Step ids run by the task, in order.
        source_ref: The ``map_over`` reference of a chain, else None.
        args: Flow variables the task reads from earlier units, in order.
        flow_params: Recipe inputs the task references, in order.
        exports: Variables the task returns to the flow, in order.
        handoffs: Variables written to a zarr store, mapped to their step and
            output names.
        needs_output_dir: Whether the task takes ``output_dir``.
        read_later: Variables read by a later member of the same task.
    """

    name: str
    member_ids: list[str]
    source_ref: str | None = None
    args: list[str] = field(default_factory=list)
    flow_params: list[str] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    handoffs: dict[str, tuple[str, str]] = field(default_factory=dict)
    needs_output_dir: bool = False
    read_later: set[str] = field(default_factory=set)

    @property
    def is_chain(self) -> bool:
        return self.source_ref is not None


@dataclass
class FlowPlan:
    """Everything the renderer needs, derived once from the DAG.

    Attributes:
        units: Task units in execution order.
        var_names: Variable name for each (step id, output name).
        handoff_vars: Every variable handed between tasks as a zarr path.
    """

    units: list[TaskUnit]
    var_names: dict[tuple[str, str], str]
    handoff_vars: set[str]


#: Params, by (op, param), that the recipe manager fills from its execution
#: context when a recipe leaves them unset. The flow fills them from
#: ``output_dir`` instead; render.py holds the expressions.
CONTEXT_BOUND_PARAMS: frozenset[tuple[str, str]] = frozenset({
    ("initial_setup", "calibration_outputs"),
})

#: Params bound for any op that declares them: the plotting ops, which save
#: their figures under the recipe manager's outputs folder and show nothing
#: when the executor runs them, and do the reverse when called on their own.
FIGURE_PARAMS: tuple[str, ...] = ("save_image", "show", "save_dir")


def iter_step_refs(value: Any) -> list[tuple[str, str]]:
    """Return every ``${step.output}`` reference inside a value, in order.

    Only a string that is a whole reference counts, at any depth of lists and
    dicts, as for the DAG's edges: the executor passes a reference embedded in
    longer text through as literal text.
    """
    refs: list[tuple[str, str]] = []
    if isinstance(value, str):
        match = _STEP_REF.fullmatch(value)
        if match and match.group(1) != "inputs":
            refs.append((match.group(1), match.group(2)))
    elif isinstance(value, list):
        for item in value:
            refs.extend(iter_step_refs(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.extend(iter_step_refs(item))
    return refs


def iter_input_refs(value: Any) -> list[str]:
    """Return every ``${inputs.x}`` name inside a value, in order."""
    names: list[str] = []
    if isinstance(value, str):
        names.extend(_INPUT_REF.findall(value))
    elif isinstance(value, list):
        for item in value:
            names.extend(iter_input_refs(item))
    elif isinstance(value, dict):
        for item in value.values():
            names.extend(iter_input_refs(item))
    return names


def step_refs(node: DAGNode) -> list[tuple[str, str]]:
    """The step outputs a node reads, including the one it collects."""
    step = node.step
    refs = iter_step_refs(list(step.inputs.values())) + iter_step_refs(
        list(step.params.values())
    )
    if step.collect is not None:
        refs.extend(iter_step_refs(step.collect))
    return refs


def is_heavy(node: DAGNode, output_name: str) -> bool:
    """Whether an output is a dataset or EchoData, written when handed off."""
    port = node.spec.outputs.get(output_name)
    type_name = getattr(port, "type", None) or ""
    return any(heavy in type_name for heavy in _HEAVY_TYPES)


def context_bound_params(node: DAGNode) -> list[str]:
    """Params of a node the flow fills in because the recipe leaves them unset."""
    bound = {param for (op, param) in CONTEXT_BOUND_PARAMS if op == node.step.op}
    bound.update(param for param in FIGURE_PARAMS if param in node.spec.params)
    return sorted(param for param in bound if param not in node.step.params)


def plan_flow(dag: PipelineDAG) -> FlowPlan:
    """Lower a DAG into task units.

    Args:
        dag: The recipe's DAG, built with raw step params intact.

    Returns:
        The flow plan.

    Raises:
        UnsupportedRecipeError: If the recipe uses a sweep or a step with no
            implementation.
    """
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        if node.step.sweep is not None:
            raise UnsupportedRecipeError(
                f"step {step_id!r} uses a sweep, which the echodataflow backend "
                "does not compile yet"
            )
        if node.implementation is None:
            raise UnsupportedRecipeError(
                f"step {step_id!r} (op {node.step.op!r}) has no implementation"
            )

    # Recipe inputs are flow parameters, so a step output whose variable would
    # share an input's name is qualified with its step id instead.
    var_names = {
        key: f"{key[0]}_{key[1]}" if var in dag.recipe.inputs else var
        for key, var in build_variable_name_map(dag).items()
    }
    chains = {chain.member_ids[0]: chain for chain in group_mapped_chains(dag)}
    in_chain = {mid for chain in chains.values() for mid in chain.member_ids}

    units: list[TaskUnit] = []
    for step_id in dag.topological_order:
        if step_id in chains:
            chain = chains[step_id]
            units.append(
                TaskUnit(
                    name=step_id,
                    member_ids=list(chain.member_ids),
                    source_ref=chain.source_ref,
                )
            )
        elif step_id not in in_chain:
            units.append(TaskUnit(name=step_id, member_ids=[step_id]))

    # A map_over source is read by the flow body to fan the chain out, so it
    # counts as a reader outside the unit that produces it.
    readers: dict[tuple[str, str], set[str]] = {}
    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        refs = step_refs(node)
        if node.step.map_over is not None:
            refs += iter_step_refs(node.step.map_over)
        for ref in refs:
            readers.setdefault(ref, set()).add(step_id)

    handoff_vars: set[str] = set()
    for unit in units:
        members = set(unit.member_ids)
        for mid in unit.member_ids:
            node = dag.nodes[mid]
            for out in node.spec.outputs:
                key = (mid, out)
                var = var_names[key]
                read_outside = bool(readers.get(key, set()) - members)
                heavy = is_heavy(node, out)
                checkpointed = node.step.checkpoint == "always"
                if heavy and (checkpointed or (unit.is_chain and read_outside)):
                    unit.handoffs[var] = key
                    handoff_vars.add(var)
                if read_outside:
                    unit.exports.append(var)
        # Chain tasks always take output_dir: they set the scratch folder in
        # their own process, which a Dask worker does not inherit.
        unit.needs_output_dir = (
            unit.is_chain
            or bool(unit.handoffs)
            or any(context_bound_params(dag.nodes[mid]) for mid in unit.member_ids)
        )

    for unit in units:
        members = set(unit.member_ids)
        args: list[str] = []
        params: list[str] = []
        for mid in unit.member_ids:
            node = dag.nodes[mid]
            for ref in step_refs(node):
                var = var_names.get(ref, f"{ref[0]}_{ref[1]}")
                if ref[0] in members:
                    unit.read_later.add(var)
                elif var not in args:
                    args.append(var)
            names = iter_input_refs(list(node.step.inputs.values())) + iter_input_refs(
                list(node.step.params.values())
            )
            for name in names:
                if name not in params:
                    params.append(name)
        unit.args = args
        unit.flow_params = params

    return FlowPlan(units=units, var_names=var_names, handoff_vars=handoff_vars)
