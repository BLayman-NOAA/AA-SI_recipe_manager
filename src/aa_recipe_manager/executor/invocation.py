# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Invocation-mapping and output-extraction primitives.

This module implements the Phase 0 design decisions that govern how the
executor (Stage 6) and the code generator (Stage 3) translate a resolved
``DAGNode`` into a concrete callable invocation. Both paths share the same
semantics so behavior stays consistent.

Decisions implemented here:

* Decision 1 — runtime context shape (``dict[step_id][output_name]``).
* Decision 2 — dynamic ``import_callable`` and a single ``build_kwargs`` path
  that applies ``param_map`` to both input ports and params.
* Decision 3 — fan-in list collection when an input resolves to multiple
  ``${step.output}`` references in declaration order.
* Decision 4 — ``output_map`` extraction rules: ``__return__``, ``[N]``,
  ``['key']``, ``.attr``, bare identifiers, and the implicit single-output
  rule. Optional outputs whose extraction fails fall back to the spec
  default when the port is not ``required``.
"""

from __future__ import annotations

import importlib
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import DAGNode


_EDGE_REF = re.compile(r"^\$\{(\w+)\.(\w+)\}$")
_INPUT_REF = re.compile(r"\$\{inputs\.(\w+)\}")


# ---------------------------------------------------------------------------
# Runtime context
# ---------------------------------------------------------------------------


class RuntimeContext:
    """Two-level mapping ``{step_id: {output_name: value}}`` of step outputs.

    Stores the outputs of each completed step so downstream steps can resolve
    ``${step.output}`` references. Sink and no-output steps still get an
    (empty) entry so they appear as "executed".
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}

    def record(self, step_id: str, outputs: dict[str, Any]) -> None:
        self._data[step_id] = dict(outputs)

    def has_step(self, step_id: str) -> bool:
        return step_id in self._data

    def get(self, step_id: str, output_name: str) -> Any:
        if step_id not in self._data:
            raise KeyError(
                f"step {step_id!r} has not produced outputs yet"
            )
        outputs = self._data[step_id]
        if output_name not in outputs:
            raise KeyError(
                f"step {step_id!r} did not produce output {output_name!r}"
            )
        return outputs[output_name]

    def step_outputs(self, step_id: str) -> dict[str, Any]:
        return dict(self._data.get(step_id, {}))

    def as_dict(self) -> dict[str, dict[str, Any]]:
        return {sid: dict(outs) for sid, outs in self._data.items()}


# ---------------------------------------------------------------------------
# Callable import
# ---------------------------------------------------------------------------


def import_callable(callable_path: str) -> Any:
    """Resolve a dotted ``module.attr[.attr...]`` path to a Python callable.

    Walks the module/attribute chain so nested attributes such as
    ``pkg.module.Class.method`` are supported. Raises ``ImportError`` when
    the module cannot be imported and ``AttributeError`` when an attribute
    is missing.
    """
    if not callable_path or "." not in callable_path:
        raise ImportError(
            f"callable_path must be a dotted path, got {callable_path!r}"
        )
    module_name, _, remainder = callable_path.partition(".")
    obj: Any = importlib.import_module(module_name)
    parts = remainder.split(".")
    consumed = module_name
    for part in parts:
        if hasattr(obj, part):
            obj = getattr(obj, part)
            consumed = f"{consumed}.{part}"
            continue
        candidate = f"{consumed}.{part}"
        try:
            obj = importlib.import_module(candidate)
        except ImportError as exc:
            raise AttributeError(
                f"cannot resolve {part!r} on {consumed!r} while importing "
                f"{callable_path!r}"
            ) from exc
        consumed = candidate
    if not callable(obj):
        raise TypeError(
            f"object at {callable_path!r} is not callable"
        )
    return obj


# ---------------------------------------------------------------------------
# Value resolution
# ---------------------------------------------------------------------------


_SENTINEL_MISSING = object()


def _resolve_single_ref(
    raw: Any,
    runtime: RuntimeContext,
    pipeline_inputs: dict[str, Any],
) -> Any:
    """Resolve a single value (not a fan-in list).

    Returns ``_SENTINEL_MISSING`` if a ``${inputs.x}`` reference cannot be
    satisfied; callers decide whether to skip the argument (optional) or
    raise.
    """
    if not isinstance(raw, str):
        return raw
    m_edge = _EDGE_REF.match(raw)
    if m_edge:
        step_id, output_name = m_edge.group(1), m_edge.group(2)
        if step_id == "inputs":
            value = pipeline_inputs.get(output_name, _SENTINEL_MISSING)
            return value
        return runtime.get(step_id, output_name)
    m_input = _INPUT_REF.fullmatch(raw)
    if m_input:
        name = m_input.group(1)
        return pipeline_inputs.get(name, _SENTINEL_MISSING)
    if "${inputs." in raw:
        def _sub(match: re.Match) -> str:
            name = match.group(1)
            if name not in pipeline_inputs:
                return match.group(0)
            return str(pipeline_inputs[name])

        return _INPUT_REF.sub(_sub, raw)
    return raw


def _resolve_value(
    raw: Any,
    runtime: RuntimeContext,
    pipeline_inputs: dict[str, Any],
) -> Any:
    """Resolve an input/param value, expanding fan-in lists element-wise."""
    if isinstance(raw, list):
        return [
            _resolve_single_ref(item, runtime, pipeline_inputs)
            for item in raw
        ]
    return _resolve_single_ref(raw, runtime, pipeline_inputs)


# ---------------------------------------------------------------------------
# Kwarg construction
# ---------------------------------------------------------------------------


def build_kwargs(
    node: DAGNode,
    runtime: RuntimeContext,
    pipeline_inputs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Merge resolved inputs and params into the callable's keyword arguments.

    The implementation's ``param_map`` is applied to both input port names
    and param names so the recipe-facing names can differ from the
    callable's actual argument names. Pipeline-level ``${inputs.x}``
    references that have no value in ``pipeline_inputs`` are skipped so the
    callable's own default is preserved.
    """
    if node.implementation is None:
        raise ValueError(
            f"node for step {node.step.id!r} has no implementation; "
            "cannot build kwargs"
        )
    pipeline_inputs = pipeline_inputs or {}
    param_map = node.implementation.param_map or {}
    kwargs: dict[str, Any] = {}

    for port_name, raw_value in node.step.inputs.items():
        callable_arg = param_map.get(port_name, port_name)
        value = _resolve_value(raw_value, runtime, pipeline_inputs)
        if value is _SENTINEL_MISSING:
            port_decl = node.spec.inputs.get(port_name)
            if port_decl is not None and not port_decl.required:
                continue
            raise KeyError(
                f"step {node.step.id!r} input {port_name!r} references an "
                f"unknown pipeline input"
            )
        kwargs[callable_arg] = value

    # resolved_params takes priority; step.params fills in any unreplaced keys.
    merged_params = {
        **{k: v for k, v in node.step.params.items() if k not in node.resolved_params},
        **node.resolved_params,
    }
    for param_name, raw_value in merged_params.items():
        callable_arg = param_map.get(param_name, param_name)
        value = _resolve_value(raw_value, runtime, pipeline_inputs)
        if value is _SENTINEL_MISSING:
            param_decl = node.spec.params.get(param_name)
            if param_decl is not None and not param_decl.required:
                continue
            raise KeyError(
                f"step {node.step.id!r} param {param_name!r} references an "
                f"unknown pipeline input"
            )
        kwargs[callable_arg] = value

    return kwargs


# ---------------------------------------------------------------------------
# Output extraction
# ---------------------------------------------------------------------------


_INDEX_RE = re.compile(r"^\[(-?\d+)\]")
_KEY_RE = re.compile(r"^\[(['\"])(.+?)\1\]")
_ATTR_RE = re.compile(r"^\.(\w+)")
_IDENT_RE = re.compile(r"^(\w+)$")


def _apply_extraction(value: Any, rule: str) -> Any:
    """Apply a single ``output_map`` rule chain to ``value``.

    Supports chained suffixes such as ``[0]['key']`` and ``.attr.sub``.
    """
    if rule == "__return__":
        return value
    if _IDENT_RE.match(rule):
        if not isinstance(value, dict):
            raise TypeError(
                f"expected dict for bare-key extraction rule {rule!r}, "
                f"got {type(value).__name__}"
            )
        return value[rule]
    current = value
    remaining = rule
    while remaining:
        m_idx = _INDEX_RE.match(remaining)
        if m_idx:
            current = current[int(m_idx.group(1))]
            remaining = remaining[m_idx.end():]
            continue
        m_key = _KEY_RE.match(remaining)
        if m_key:
            current = current[m_key.group(2)]
            remaining = remaining[m_key.end():]
            continue
        m_attr = _ATTR_RE.match(remaining)
        if m_attr:
            current = getattr(current, m_attr.group(1))
            remaining = remaining[m_attr.end():]
            continue
        raise ValueError(f"unrecognized output_map rule fragment: {remaining!r}")
    return current


def extract_outputs(node: DAGNode, return_value: Any) -> dict[str, Any]:
    """Map a callable's return value onto the spec's declared output ports.

    Implements the implicit single-output rule (when the spec has exactly one
    output and no ``output_map`` entry, the entire return value is used) and
    the optional-output fallback (extraction failures for non-required ports
    fall back to the port's default or ``None``).
    """
    if node.implementation is None:
        raise ValueError(
            f"node for step {node.step.id!r} has no implementation"
        )
    spec_outputs = node.spec.outputs
    if not spec_outputs:
        return {}

    output_map = node.implementation.output_map or {}
    extracted: dict[str, Any] = {}
    output_names = list(spec_outputs.keys())
    single_output = len(output_names) == 1

    for out_name in output_names:
        port_decl = spec_outputs[out_name]
        if out_name in output_map:
            rule = output_map[out_name]
        elif single_output:
            rule = "__return__"
        else:
            rule = out_name
        try:
            extracted[out_name] = _apply_extraction(return_value, rule)
        except (KeyError, IndexError, AttributeError, TypeError):
            if port_decl.required:
                raise
            extracted[out_name] = port_decl.default
    return extracted
