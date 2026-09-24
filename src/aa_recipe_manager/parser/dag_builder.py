# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Build and topologically validate the PipelineDAG from a parsed Recipe."""

from __future__ import annotations

import heapq
import warnings
from pathlib import Path
from typing import Any
import re

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
from aa_recipe_manager.parallel import group_mapped_chains
from aa_recipe_manager.registry.registry import Registry
from aa_recipe_manager.resolver.params import (
    contains_item_ref,
    extract_edge_refs,
    parse_ref,
    resolve_input_refs,
)
from aa_recipe_manager.storage import is_remote_url

# Synthetic edge target-input sentinels for the map_over / collect directives.
# These live on dedicated Step fields (not inputs/params), so extract_edge_refs
# never sees them; we materialize them as ordering edges so the topological sort
# runs the segment producer before a mapped step and a mapped step before its
# collector, and so dangling-reference validation covers them.
_MAP_OVER_TARGET = "__map_over__"
_COLLECT_TARGET = "__collect__"
_SYNTHETIC_TARGETS = frozenset({_MAP_OVER_TARGET, _COLLECT_TARGET})


_INPUT_REF = re.compile(r"\$\{inputs\.(\w+)\}")


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

        # map_over / collect sources are dependency edges too.
        for source_ref, target in (
            (step.map_over, _MAP_OVER_TARGET),
            (step.collect, _COLLECT_TARGET),
        ):
            parsed = parse_ref(source_ref)
            if parsed is not None:
                src_step, src_output = parsed
                edges.append(
                    DAGEdge(
                        source_step_id=src_step,
                        source_output=src_output,
                        target_step_id=step.id,
                        target_input=target,
                    )
                )

    valid_step_ids = {s.id for s in recipe.steps}
    _validate_pipeline_input_refs(recipe, nodes, errors)
    _validate_edges(edges, nodes, valid_step_ids, errors, warn_msgs)
    _validate_required_inputs(nodes, errors)
    _validate_map_collect_sweep(nodes, errors, warn_msgs)
    _validate_disposable_outputs(nodes, errors)

    if errors:
        raise RecipeValidationError(errors, warn_msgs)

    for msg in warn_msgs:
        warnings.warn(msg, stacklevel=2)

    topo_order = _topological_sort(nodes, edges, errors)
    if errors:
        raise RecipeValidationError(errors)

    dag = PipelineDAG(
        recipe=recipe,
        nodes=nodes,
        edges=edges,
        topological_order=topo_order,
    )
    _validate_mapped_chain_refs(dag, edges, errors)
    _validate_disposal_survives_fan_in(dag, errors)
    if errors:
        raise RecipeValidationError(errors)

    return dag


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
        impl = _resolve_custom_implementation(step)
        return spec, impl

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


def _resolve_custom_implementation(step: Step) -> Implementation:
    """Build an Implementation from a step's custom_spec declaration."""
    cs = step.custom_spec
    assert cs is not None
    return Implementation(
        op="custom",
        key="custom",
        callable_path=cs.callable_path,
        dependency=cs.dependency,
        param_map=cs.param_map or {},
        output_map=cs.output_map or {},
        default=True,
    )


def _validate_params(
    step: Step,
    spec: Spec,
    resolved_params: dict[str, Any],
    errors: list[str],
    warn_msgs: list[str],
) -> None:
    """Check required params are present and emit type-mismatch warnings."""
    swept = set(step.sweep.param_lists) if step.sweep is not None else set()
    for param_name, param_decl in spec.params.items():
        # Swept params are supplied per-instance by the sweep block, not the
        # step's static params, so they are neither missing nor mismatched here.
        if param_name in swept:
            continue
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

        # An unresolved ${...} reference is still a placeholder string here; its
        # real value (or its absence, which lets the callable default apply)
        # is only known once pipeline inputs are supplied at execution time.
        if isinstance(value, str) and "${" in value:
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

    # fsspec URLs (gs://, s3://, memory://, ...) are not local paths: skip the
    # existence / mkdir checks entirely. Validation runs before execution and
    # may have no credentials, and Path(value) would mangle the URL and create
    # a bogus local 'gs:/bucket' directory. The storage layer verifies the URL
    # when the step actually reads or writes it.
    if is_remote_url(value):
        return

    must_exist = True
    create_if_missing = False
    if param_decl.constraints is not None:
        must_exist = param_decl.constraints.get("must_exist", True)
        create_if_missing = bool(param_decl.constraints.get("create_if_missing", False))

    path_obj = Path(value)

    # `create_if_missing` is a user-friendly convenience: if the directory does
    # not yet exist, create it instead of failing validation. This lets a
    # downstream step accept a folder that an upstream step will populate, and
    # spares users from having to pre-create staging directories by hand.
    if create_if_missing and not path_obj.exists():
        try:
            path_obj.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            errors.append(
                f"Step '{step.id}': could not create path param '{param_name}' "
                f"at {value}: {exc}"
            )
            return

    if must_exist and not path_obj.exists():
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
    # A port may name several accepted types as "A | B"; the edge is compatible
    # when the two sides share one. Applied to both sides so an op that really
    # handles either (merge_datasets concatenates Datasets or DataArrays) can
    # declare that on its output as well as its input.
    src_types = {t.strip() for t in src_port.type.split("|")}
    target_types = {t.strip() for t in tgt_port.type.split("|")}
    # If the target port has many=True it accepts a single element or a collected
    # list of that element type. Compare element types, not the wrapper.
    if tgt_port.many:
        if not (src_types & target_types):
            warn_msgs.append(
                f"Type mismatch on edge '{edge.source_step_id}.{edge.source_output}' -> "
                f"'{edge.target_step_id}.{edge.target_input}': "
                f"element type '{src_port.type}' is not compatible with '{tgt_port.type}'."
            )
        return
    if not (src_types & target_types):
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
        collect_port = _collect_bind_port(node)
        for input_name, port_decl in node.spec.inputs.items():
            if not port_decl.required or input_name in node.step.inputs:
                continue
            # A collector auto-binds the accumulated list to the input port
            # named after the collected output, so that port counts as wired.
            if input_name == collect_port:
                continue
            errors.append(
                f"Step '{node.step.id}': required input '{input_name}' is not wired."
            )


def _collect_bind_port(node: DAGNode) -> str | None:
    """Return the input-port name a ``collect`` directive auto-binds to, if any.

    ``collect: ${S.out}`` binds the accumulated list to the collector's input
    port named ``out`` when the recipe does not wire it explicitly.
    """
    if not node.is_collector or node.collect_source is None:
        return None
    parsed = parse_ref(node.collect_source)
    if parsed is None:
        return None
    _, output_name = parsed
    return output_name if output_name in node.spec.inputs else None


def _validate_mapped_chain_refs(
    dag: PipelineDAG,
    edges: list[DAGEdge],
    errors: list[str],
) -> None:
    """Reject a reference between mapped steps that did not land in one chain.

    Two steps mapped over the same source are meant to run as one chain, where
    each member sees this element's value of the ones before it. Only
    consecutive members are grouped, so a step ordered between them splits the
    chain, and the reference then resolves to the whole fanned-out list. That
    is silently the wrong value rather than an error, so refuse to run instead.
    """
    chain_of: dict[str, str] = {}
    for chain in group_mapped_chains(dag):
        for member_id in chain.member_ids:
            chain_of[member_id] = chain.member_ids[0]

    reported: set[tuple[str, str]] = set()
    for edge in edges:
        src, tgt = edge.source_step_id, edge.target_step_id
        source = dag.nodes.get(src)
        target = dag.nodes.get(tgt)
        if source is None or target is None:
            continue
        if not (source.is_mapped and target.is_mapped):
            continue
        if source.map_source != target.map_source:
            continue
        if chain_of.get(src) == chain_of.get(tgt) or (src, tgt) in reported:
            continue
        reported.add((src, tgt))
        errors.append(
            f"Step '{tgt}' reads '{src}', which maps over the same source, but "
            f"the two are not in one mapped chain, so '{src}' resolves to the "
            f"whole fanned-out list rather than this element's value. Declare "
            f"the steps that map over {source.map_source} consecutively, or "
            f"fan '{src}' in with a collect step and read that."
        )


# Output types whose value is a small, self-contained object. Anything else
# (Dataset, DataArray, EchoData, object) may be a lazy graph that still reads
# from the files an earlier chain member disposes.
_LIGHT_OUTPUT_TYPES = {"int", "float", "str", "bool", "path", "list", "dict"}


def _disposing_ports(node: DAGNode) -> list[str]:
    """Output ports of ``node`` that disposal deletes after each instance."""
    ports = [
        name
        for name, port in node.spec.outputs.items()
        if getattr(port, "disposable", False)
    ]
    ports += [name for name in node.step.dispose_outputs or [] if name not in ports]
    return ports


def _validate_disposal_survives_fan_in(
    dag: PipelineDAG,
    errors: list[str],
) -> None:
    """Reject a mapped chain whose disposed scratch could reach a reader outside it.

    Once a chain member disposes its outputs, each later member's dataset may
    be a lazy graph rooted in the deleted files. Inside the chain that is safe:
    the next member reads the value before the instance finishes. Outside the
    chain it is not: a collect step or any other reader receives the graph
    after disposal, and zarr answers a missing chunk with its fill value, so
    the fan-in quietly becomes all NaN. The one path that survives is a
    checkpoint, which the fan-in reloads from the store instead. So every
    member from the disposing step onward whose non-trivial output is read
    outside the chain must set ``checkpoint: always``. ``save`` is not enough,
    because it yields to a run's ``--checkpoint-mode none``.

    Small outputs (a path, a count, a params dict) are values, not graphs, and
    are exempt. Whether a disposed file behind a path is still wanted is what
    the ``disposable`` flag on that port already declares.
    """
    outside_readers: dict[tuple[str, str], set[str]] = {}
    for edge in dag.edges:
        key = (edge.source_step_id, edge.source_output)
        outside_readers.setdefault(key, set()).add(edge.target_step_id)
    for output in (dag.recipe.outputs or {}).values():
        key = (output.step_id, output.output_name)
        outside_readers.setdefault(key, set()).add("the recipe outputs block")

    for chain in group_mapped_chains(dag):
        inside = set(chain.member_ids)
        disposer: str | None = None
        for member_id in chain.member_ids:
            node = dag.nodes[member_id]
            if disposer is None:
                if _disposing_ports(node):
                    disposer = member_id
                else:
                    continue
            exposed: dict[str, set[str]] = {}
            for name, port in node.spec.outputs.items():
                if port.type in _LIGHT_OUTPUT_TYPES:
                    continue
                readers = outside_readers.get((member_id, name), set()) - inside
                if readers:
                    exposed[name] = readers
            if not exposed or node.step.checkpoint == "always":
                continue
            ports = ", ".join(sorted(exposed))
            readers = ", ".join(sorted(set().union(*exposed.values())))
            if member_id == disposer:
                fix = (
                    f"read a later member that is checkpointed instead, or drop "
                    f"the disposal on '{disposer}'"
                )
            else:
                fix = f"set 'checkpoint: always' on '{member_id}'"
            errors.append(
                f"Step '{member_id}' (output(s) {ports}) is read outside its "
                f"mapped chain by {readers}, but '{disposer}' earlier in the "
                f"chain disposes its outputs, so the value would arrive as a "
                f"lazy graph over files that no longer exist and read back as "
                f"fill values (NaN) with no error. Fix: {fix}."
            )


def _validate_disposable_outputs(
    nodes: dict[str, DAGNode],
    errors: list[str],
) -> None:
    """Reject a disposable output on a step whose result is checkpointed.

    Covers both ways a port becomes disposable: the spec's ``disposable`` flag
    and the step's ``dispose_outputs`` opt-in.

    Disposal deletes the files the port names. If the step were checkpointed,
    the checkpoint would record paths to files that no longer exist, and a
    later partial resume would load that checkpoint and hand a consumer a
    dangling path. Requiring ``checkpoint: never`` keeps the two features from
    contradicting each other, and is also the honest declaration: an output you
    are about to delete is not one worth caching.

    A ``dispose_outputs`` entry naming a port the op does not produce is an
    error rather than a no-op, so a typo fails the build instead of silently
    leaving the scratch it was meant to remove.
    """
    for step_id, node in nodes.items():
        requested = list(node.step.dispose_outputs or [])
        unknown = [name for name in requested if name not in node.spec.outputs]
        if unknown:
            errors.append(
                f"Step '{step_id}': dispose_outputs names port(s) "
                f"{', '.join(sorted(unknown))}, which op '{node.step.op}' does "
                f"not produce. Available outputs: "
                f"{', '.join(sorted(node.spec.outputs)) or '(none)'}."
            )
            continue
        ports = [
            name
            for name, port in node.spec.outputs.items()
            if getattr(port, "disposable", False)
        ]
        ports += [name for name in requested if name not in ports]
        if not ports:
            continue
        if node.step.checkpoint != "never":
            listed = ", ".join(sorted(ports))
            errors.append(
                f"Step '{node.step.id}': disposable output(s) {listed} "
                f"(op '{node.step.op}'), so the step must set "
                f"'checkpoint: never'. A checkpoint would record paths to "
                f"files disposal deletes, and a later resume would load them "
                f"as dangling paths."
            )


def _validate_map_collect_sweep(
    nodes: dict[str, DAGNode],
    errors: list[str],
    warn_msgs: list[str],
) -> None:
    """Validate map_over / collect / sweep semantics (FR-14.6, FR-18.3)."""
    for node in nodes.values():
        step = node.step

        if step.collect is not None:
            parsed = parse_ref(step.collect)
            if parsed is None:
                errors.append(
                    f"Step '{step.id}': collect must be a ${{step.output}} "
                    f"reference, got '{step.collect}'."
                )
            else:
                src_id, _ = parsed
                src = nodes.get(src_id)
                if src is not None and not (src.is_mapped or src.is_swept):
                    errors.append(
                        f"Step '{step.id}': collect references step '{src_id}', "
                        f"which is neither mapped (map_over) nor swept (sweep)."
                    )

        if step.map_over is not None:
            parsed = parse_ref(step.map_over)
            if parsed is None:
                errors.append(
                    f"Step '{step.id}': map_over must be a ${{step.output}} "
                    f"reference, got '{step.map_over}'."
                )
            else:
                src_id, src_out = parsed
                src = nodes.get(src_id)
                if src is not None:
                    port = src.spec.outputs.get(src_out)
                    if port is not None and port.type != "list" and not port.many:
                        warn_msgs.append(
                            f"Step '{step.id}': map_over source "
                            f"'{src_id}.{src_out}' has declared type '{port.type}', "
                            f"which is not a list; the step will run once unless the "
                            f"value is a list at runtime (single-item transparency)."
                        )

        if step.sweep is not None:
            _validate_sweep(step, node.spec, errors)

        # ${_item} is only meaningful inside a mapped step.
        if step.map_over is None:
            for input_name, raw_value in step.inputs.items():
                if contains_item_ref(raw_value):
                    errors.append(
                        f"Step '{step.id}': input '{input_name}' references "
                        f"${{_item}} but the step has no map_over."
                    )
            for param_name, raw_value in step.params.items():
                if contains_item_ref(raw_value):
                    errors.append(
                        f"Step '{step.id}': param '{param_name}' references "
                        f"${{_item}} but the step has no map_over."
                    )


def _validate_sweep(step: Step, spec: Spec, errors: list[str]) -> None:
    """Validate a step's sweep declaration (FR-18.3)."""
    sweep = step.sweep
    assert sweep is not None
    if not sweep.param_lists:
        errors.append(f"Step '{step.id}': sweep.param_lists is empty.")
        return

    for pname, values in sweep.param_lists.items():
        if pname not in spec.params:
            errors.append(
                f"Step '{step.id}': sweep param '{pname}' is not a declared param "
                f"of op '{step.op}'."
            )
        if pname in step.params:
            errors.append(
                f"Step '{step.id}': sweep param '{pname}' must not also appear in "
                f"the step's params block."
            )
        if not isinstance(values, list) or len(values) == 0:
            errors.append(
                f"Step '{step.id}': sweep param '{pname}' must be a non-empty list."
            )

    if sweep.mode == "zip":
        lengths = {len(v) for v in sweep.param_lists.values() if isinstance(v, list)}
        if len(lengths) > 1:
            errors.append(
                f"Step '{step.id}': sweep mode 'zip' requires all param lists to have "
                f"the same length; got lengths {sorted(lengths)}."
            )


def _validate_pipeline_input_refs(
    recipe: Recipe,
    nodes: dict[str, DAGNode],
    errors: list[str],
) -> None:
    """Reject ${inputs.x} references that do not match declared recipe inputs."""
    declared_inputs = set(recipe.inputs)
    for node in nodes.values():
        for input_name, raw_value in node.step.inputs.items():
            for missing_name in _iter_unknown_input_refs(raw_value, declared_inputs):
                errors.append(
                    f"Step '{node.step.id}': input '{input_name}' references "
                    f"undeclared pipeline input '{missing_name}'."
                )
        for param_name, raw_value in node.step.params.items():
            for missing_name in _iter_unknown_input_refs(raw_value, declared_inputs):
                errors.append(
                    f"Step '{node.step.id}': param '{param_name}' references "
                    f"undeclared pipeline input '{missing_name}'."
                )


def _iter_unknown_input_refs(value: Any, declared_inputs: set[str]) -> list[str]:
    """Return undeclared ${inputs.x} names found anywhere within a nested value."""
    missing: list[str] = []
    if isinstance(value, str):
        for name in _INPUT_REF.findall(value):
            if name not in declared_inputs and name not in missing:
                missing.append(name)
        return missing
    if isinstance(value, list):
        for item in value:
            for name in _iter_unknown_input_refs(item, declared_inputs):
                if name not in missing:
                    missing.append(name)
        return missing
    if isinstance(value, dict):
        for item in value.values():
            for name in _iter_unknown_input_refs(item, declared_inputs):
                if name not in missing:
                    missing.append(name)
    return missing


def _topological_sort(
    nodes: dict[str, DAGNode],
    edges: list[DAGEdge],
    errors: list[str],
) -> list[str]:
    """Kahn's algorithm topological sort. Appends a cycle error if a cycle is found.

    Ready steps are drained in recipe declaration order rather than in the
    order they became ready. Both are valid topological orders, but only the
    declaration order keeps a mapped chain contiguous when the recipe also
    declares independent non-mapped steps alongside it: a breadth-first queue
    hoists a mapped step as soon as its map source is done, which lands
    unrelated steps in the middle of the chain and splits it. group_mapped_chains
    only groups consecutive members, and a split chain's members stop sharing an
    element context, so a reference from one to another resolves to the whole
    fanned-out list instead of this element's value.
    """
    # De-duplicate while preserving edge declaration order: a plain ``set``
    # here made the ready-queue processing order (and therefore which mapped
    # chains stay contiguous in dag.topological_order, see parallel.py's
    # group_mapped_chains) depend on CPython's per-process string hash
    # randomization -- the same recipe could split a mapped chain on one run
    # and not the next.
    seen_deps: set[tuple[str, str]] = set()
    unique_deps: list[tuple[str, str]] = []
    for edge in edges:
        src, tgt = edge.source_step_id, edge.target_step_id
        if src in nodes and tgt in nodes and src != tgt and (src, tgt) not in seen_deps:
            seen_deps.add((src, tgt))
            unique_deps.append((src, tgt))

    in_degree: dict[str, int] = {node_id: 0 for node_id in nodes}
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in nodes}

    for src, tgt in unique_deps:
        adjacency[src].append(tgt)
        in_degree[tgt] += 1

    declared_at = {node_id: index for index, node_id in enumerate(nodes)}
    ready = [(declared_at[nid], nid) for nid, deg in in_degree.items() if deg == 0]
    heapq.heapify(ready)
    order: list[str] = []

    while ready:
        _, nid = heapq.heappop(ready)
        order.append(nid)
        for neighbor in adjacency[nid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                heapq.heappush(ready, (declared_at[neighbor], neighbor))

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

