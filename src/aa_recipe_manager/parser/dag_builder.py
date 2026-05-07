# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Build and topologically validate the PipelineDAG from a parsed Recipe."""

from __future__ import annotations

import warnings
from collections import deque
from pathlib import Path
from typing import Any

from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
    RecipeValidationError,
    SpecNotFoundError,
)
from aa_recipe_manager.model.types import (
    DAGEdge,
    DAGNode,
    Implementation,
    PipelineDAG,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)
from aa_recipe_manager.registry.registry import Registry
from aa_recipe_manager.resolver.params import extract_edge_refs, resolve_input_refs


def build_dag(
    recipe: Recipe,
    registry: Registry,
    input_values: dict[str, Any] | None = None,
    check_versions: bool = True,
) -> PipelineDAG:
    """Build and validate a PipelineDAG from a Recipe and Registry.

    Resolves specs and implementations, extracts DAG edges, runs validation,
    and performs a topological sort. Raises RecipeValidationError if any hard
    errors are found; non-blocking issues are emitted as Python warnings.
    When check_versions is False, dependency installation checks are skipped.
    """
    errors: list[str] = []
    warn_msgs: list[str] = []

    input_defaults = {
        name: decl.default
        for name, decl in recipe.inputs.items()
        if decl.default is not None
    }
    if input_values:
        input_defaults.update(input_values)

    nodes: dict[str, DAGNode] = {}
    edges: list[DAGEdge] = []

    # Detect duplicate step IDs before processing any steps.
    seen_ids: set[str] = set()
    for step in recipe.steps:
        if step.id in seen_ids:
            errors.append(f"Duplicate step id '{step.id}' in recipe.")
        seen_ids.add(step.id)
    if errors:
        raise RecipeValidationError(errors)

    for step in recipe.steps:
        spec, impl = _resolve_step(step, registry, errors, check_versions=check_versions)
        if spec is None:
            continue

        resolved_params = resolve_input_refs(step.params, input_defaults)
        _validate_params(step, spec, resolved_params, errors, warn_msgs)

        nodes[step.id] = DAGNode(
            step=step,
            spec=spec,
            implementation=impl,
            resolved_params=resolved_params,
            is_mapped=step.map_over is not None,
            is_collector=step.collect is not None,
            is_swept=step.sweep is not None,
            map_source=step.map_over,
            collect_source=step.collect,
            sweep_declaration=step.sweep,
        )

        for src_step, src_output, tgt_step, tgt_input in extract_edge_refs(step):
            edges.append(
                DAGEdge(
                    source_step_id=src_step,
                    source_output=src_output,
                    target_step_id=tgt_step,
                    target_input=tgt_input,
                )
            )

        for dep_id in step.depends_on or []:
            edges.append(
                DAGEdge(
                    source_step_id=dep_id,
                    source_output="",
                    target_step_id=step.id,
                    target_input="",
                )
            )

    valid_step_ids = {s.id for s in recipe.steps}
    _validate_edges(edges, nodes, valid_step_ids, errors, warn_msgs)
    _validate_required_inputs(nodes, errors)

    if errors:
        raise RecipeValidationError(errors, warn_msgs)

    for msg in warn_msgs:
        warnings.warn(msg, stacklevel=2)

    topo_order = _topological_sort(nodes, edges, errors)
    if errors:
        raise RecipeValidationError(errors)

    return PipelineDAG(
        recipe=recipe,
        nodes=nodes,
        edges=edges,
        topological_order=topo_order,
    )


def _resolve_step(
    step: Step,
    registry: Registry,
    errors: list[str],
    check_versions: bool = True,
) -> tuple[Spec | None, Implementation | None]:
    """Return the (Spec, Implementation) for a step, recording errors in-place."""
    if step.op == "custom":
        if step.custom_spec is None:
            errors.append(
                f"Step '{step.id}': op is 'custom' but no custom_spec provided."
            )
            return None, None
        spec = _resolve_custom_spec(step, registry, errors)
        return spec, None

    try:
        spec = registry.get_spec(step.op)
    except SpecNotFoundError:
        errors.append(f"Step '{step.id}': unknown op '{step.op}'.")
        return None, None

    impl = _resolve_implementation(step, registry, errors, check_versions=check_versions)
    return spec, impl


def _resolve_implementation(
    step: Step,
    registry: Registry,
    errors: list[str],
    check_versions: bool = True,
) -> Implementation | None:
    key = step.implementation_override
    try:
        return registry.get_implementation(step.op, key, check_versions=check_versions)
    except ImplementationNotFoundError:
        if key is not None:
            errors.append(
                f"Step '{step.id}': implementation '{key}' not found for op '{step.op}'."
            )
        return None
    except AmbiguousImplementationError as exc:
        errors.append(f"Step '{step.id}': {exc}")
        return None
    except DependencyVersionError as exc:
        errors.append(f"Step '{step.id}': {exc}")
        return None


def _resolve_custom_spec(
    step: Step,
    registry: Registry,
    errors: list[str],
) -> Spec | None:
    """Build a Spec from a step's custom_spec declaration, merging extends if set."""
    cs = step.custom_spec
    assert cs is not None

    if cs.extends is None:
        return Spec(
            op="custom",
            description=cs.description,
            inputs=cs.inputs or {},
            outputs=cs.outputs or {},
            params=cs.params or {},
        )

    try:
        parent = registry.get_spec(cs.extends)
    except SpecNotFoundError:
        errors.append(
            f"Step '{step.id}': custom_spec extends unknown op '{cs.extends}'."
        )
        return None

    merged_inputs = dict(parent.inputs)
    if cs.inputs:
        merged_inputs.update(cs.inputs)

    merged_outputs = dict(parent.outputs)
    if cs.outputs:
        merged_outputs.update(cs.outputs)

    merged_params = dict(parent.params)
    if cs.params:
        merged_params.update(cs.params)

    return Spec(
        op=cs.extends,
        description=cs.description,
        inputs=merged_inputs,
        outputs=merged_outputs,
        params=merged_params,
    )


def _validate_params(
    step: Step,
    spec: Spec,
    resolved_params: dict[str, Any],
    errors: list[str],
    warn_msgs: list[str],
) -> None:
    """Check required params are present and emit type-mismatch warnings."""
    for param_name, param_decl in spec.params.items():
        value = resolved_params.get(param_name)
        if value is None and param_name not in resolved_params:
            value = param_decl.default

        if param_decl.required and param_decl.default is None and value is None:
            errors.append(
                f"Step '{step.id}': required param '{param_name}' is not provided."
            )
            continue

        if value is None or param_decl.type is None:
            continue

        if param_decl.type == "path":
            _validate_path_param(step, param_name, param_decl, value, errors)
            continue

        expected_py = _YAML_TYPE_MAP.get(param_decl.type) if param_decl.type else None
        if expected_py and not isinstance(value, expected_py):
            warn_msgs.append(
                f"Step '{step.id}': param '{param_name}' has type '{param_decl.type}' "
                f"but received {type(value).__name__}."
            )


def _validate_path_param(
    step: Step,
    param_name: str,
    param_decl: Any,
    value: Any,
    errors: list[str],
) -> None:
    """Check path existence for concrete path values unless the spec opts out."""
    if not isinstance(value, (str, Path)):
        errors.append(
            f"Step '{step.id}': param '{param_name}' has type 'path' "
            f"but received {type(value).__name__}."
        )
        return

    if isinstance(value, str) and "${" in value:
        return

    must_exist = True
    if param_decl.constraints is not None:
        must_exist = param_decl.constraints.get("must_exist", True)

    if must_exist and not Path(value).exists():
        errors.append(
            f"Step '{step.id}': path param '{param_name}' does not exist: {value}"
        )


def _validate_edges(
    edges: list[DAGEdge],
    nodes: dict[str, DAGNode],
    valid_step_ids: set[str],
    errors: list[str],
    warn_msgs: list[str],
) -> None:
    """Check for dangling references and type compatibility on data edges."""
    for edge in edges:
        if not edge.source_output:
            # Ordering-only edge from depends_on
            if edge.source_step_id not in valid_step_ids:
                errors.append(
                    f"Step '{edge.target_step_id}': depends_on references unknown "
                    f"step '{edge.source_step_id}'."
                )
            continue

        if edge.source_step_id not in valid_step_ids:
            errors.append(
                f"Step '{edge.target_step_id}': input '{edge.target_input}' references "
                f"unknown step '{edge.source_step_id}'."
            )
            continue

        src_node = nodes.get(edge.source_step_id)
        if src_node is None:
            continue

        if edge.source_output not in src_node.spec.outputs:
            errors.append(
                f"Step '{edge.target_step_id}': input '{edge.target_input}' references "
                f"output '{edge.source_output}' which is not declared on "
                f"step '{edge.source_step_id}'."
            )
            continue

        tgt_node = nodes.get(edge.target_step_id)
        if tgt_node is None:
            continue

        _check_port_type_compatibility(edge, src_node, tgt_node, warn_msgs)


def _check_port_type_compatibility(
    edge: DAGEdge,
    src_node: DAGNode,
    tgt_node: DAGNode,
    warn_msgs: list[str],
) -> None:
    """Emit a warning when source output type and target input type names differ."""
    src_port: PortDeclaration | None = src_node.spec.outputs.get(edge.source_output)
    tgt_port: PortDeclaration | None = tgt_node.spec.inputs.get(edge.target_input)

    if src_port is None or tgt_port is None:
        return
    if tgt_port.type == "list":
        return
    # If the target port has many=True it accepts a single element or a collected
    # list of that element type. Compare element types, not the wrapper.
    if tgt_port.many:
        if src_port.type != tgt_port.type:
            warn_msgs.append(
                f"Type mismatch on edge '{edge.source_step_id}.{edge.source_output}' -> "
                f"'{edge.target_step_id}.{edge.target_input}': "
                f"element type '{src_port.type}' is not compatible with '{tgt_port.type}'."
            )
        return
    target_types = {t.strip() for t in tgt_port.type.split("|")}
    if src_port.type not in target_types:
        warn_msgs.append(
            f"Type mismatch on edge '{edge.source_step_id}.{edge.source_output}' -> "
            f"'{edge.target_step_id}.{edge.target_input}': "
            f"'{src_port.type}' vs '{tgt_port.type}'."
        )


def _validate_required_inputs(
    nodes: dict[str, DAGNode],
    errors: list[str],
) -> None:
    """Check that all required spec inputs are wired in each step."""
    for node in nodes.values():
        for input_name, port_decl in node.spec.inputs.items():
            if port_decl.required and input_name not in node.step.inputs:
                errors.append(
                    f"Step '{node.step.id}': required input '{input_name}' is not wired."
                )


def _topological_sort(
    nodes: dict[str, DAGNode],
    edges: list[DAGEdge],
    errors: list[str],
) -> list[str]:
    """Kahn's algorithm topological sort. Appends a cycle error if a cycle is found."""
    unique_deps: set[tuple[str, str]] = set()
    for edge in edges:
        src, tgt = edge.source_step_id, edge.target_step_id
        if src in nodes and tgt in nodes and src != tgt:
            unique_deps.add((src, tgt))

    in_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}

    for src, tgt in unique_deps:
        adjacency[src].append(tgt)
        in_degree[tgt] += 1

    queue = deque(nid for nid, deg in in_degree.items() if deg == 0)
    order: list[str] = []

    while queue:
        nid = queue.popleft()
        order.append(nid)
        for neighbor in adjacency[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    if len(order) != len(nodes):
        cycle_nodes = [nid for nid, deg in in_degree.items() if deg > 0]
        errors.append(
            f"Cycle detected in pipeline DAG. Involved steps: {sorted(cycle_nodes)}"
        )
        return []

    return order


# Mapping from spec type strings to Python types for basic value checking.
_YAML_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "float": (float, int),
    "int": int,
    "str": str,
    "bool": bool,
    "list": list,
    "dict": dict,
}

