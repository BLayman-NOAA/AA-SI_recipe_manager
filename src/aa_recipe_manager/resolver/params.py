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
    """Return DAG edge tuples from a step's input wiring.

    Each tuple is (source_step_id, source_output, target_step_id, target_input).
    List-valued inputs (e.g. combine_masks.masks) produce one tuple per element.
    """
    edges = []
    for input_name, value in step.inputs.items():
        items: list[Any] = value if isinstance(value, list) else [value]
        for item in items:
            if isinstance(item, str):
                m = _EDGE_REF.match(item)
                if m:
                    edges.append((m.group(1), m.group(2), step.id, input_name))
    return edges


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

            def _replace(m: re.Match, _iv: dict = input_values) -> str:
                sub = _iv.get(m.group(1))
                return str(sub) if sub is not None else m.group(0)

            resolved[key] = _INPUT_REF.sub(_replace, value)
        else:
            resolved[key] = value
    return resolved

