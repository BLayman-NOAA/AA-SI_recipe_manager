# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Utilities for extracting and resolving ${...} references in recipe steps."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import Step

# Matches a full string that is a single step-output reference: ${step_id.output_name}
_EDGE_REF = re.compile(r"^\$\{(\w+)\.(\w+)\}$")

# Matches ${inputs.name} anywhere within a string
_INPUT_REF = re.compile(r"\$\{inputs\.(\w+)\}")


def extract_edge_refs(step: Step) -> list[tuple[str, str, str, str]]:
    """Return DAG edge tuples from a step's input and param wiring.

    Each tuple is (source_step_id, source_output, target_step_id, target_input).
    List-valued inputs/params (e.g. combine_masks.masks) produce one tuple per
    element. Param references are dependency edges too because generated code
    must run the producer step before rendering/passing that param value.
    """
    edges = []
    for input_name, value in step.inputs.items():
        for src_step, src_output in _iter_edge_refs(value):
            edges.append((src_step, src_output, step.id, input_name))
    for param_name, value in step.params.items():
        for src_step, src_output in _iter_edge_refs(value):
            edges.append((src_step, src_output, step.id, param_name))
    return edges


def _iter_edge_refs(value: Any) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    if isinstance(value, str):
        match = _EDGE_REF.match(value)
        if match and match.group(1) != "inputs":
            refs.append((match.group(1), match.group(2)))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_iter_edge_refs(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.extend(_iter_edge_refs(item))
    return refs


def extract_input_refs(params: dict[str, Any]) -> dict[str, str]:
    """Return {param_key: input_name} for each top-level ${inputs.x} value in params."""
    result = {}
    for key, value in params.items():
        if isinstance(value, str):
            m = _INPUT_REF.fullmatch(value)
            if m:
                result[key] = m.group(1)
    return result


def resolve_input_refs(
    params: dict[str, Any],
    input_values: dict[str, Any],
) -> dict[str, Any]:
    """Substitute ${inputs.x} placeholders in params with values from input_values.

    Handles both full-string references and partial interpolation
    (e.g. "${inputs.folder}/subdir"). References with no corresponding value
    in input_values are left as-is.
    """
    if not input_values:
        return dict(params)

    resolved: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str) and "${inputs." in value:
            full_match = _INPUT_REF.fullmatch(value)
            if full_match:
                input_value = input_values.get(full_match.group(1))
                resolved[key] = value if input_value is None else input_value
                continue

            def _replace(m: re.Match, _iv: dict = input_values) -> str:
                sub = _iv.get(m.group(1))
                return str(sub) if sub is not None else m.group(0)

            resolved[key] = _INPUT_REF.sub(_replace, value)
        else:
            resolved[key] = value
    return resolved

