# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""PipelineTracker context manager and save_recipe().

Self-contained: imports only stdlib and ruamel.yaml. No imports from the
rest of aa_recipe_manager. The generated notebook embeds a PipelineTracker
instance to record actual execution order and parameter values, then
serialize back to a valid YAML recipe.
"""

from __future__ import annotations

import copy
import io
import warnings
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator


class PipelineTracker:
    """Records step execution in a generated notebook and serializes it back.

    Usage in generated notebook::

        tracker = PipelineTracker(recipe_dict)
        with tracker.step("compute_sv", op="compute_sv", params={"range_bin": "2m"}):
            ds_Sv = compute_Sv(echodata=echodata)
        tracker.save_recipe("pipeline_modified.yaml")
    """

    def __init__(self, recipe_dict: dict[str, Any]) -> None:
        self._original = _normalize_recipe_dict(recipe_dict)
        self._executed: list[tuple[str, str, dict[str, Any]]] = []

    @contextmanager
    def step(
        self,
        step_id: str,
        *,
        op: str,
        params: dict[str, Any] | None = None,
    ) -> Generator[None, None, None]:
        """Context manager that records a step execution.

        Only scalar-valued params (int, float, str, bool, None, or lists of
        scalars) should be passed. Data objects are not recorded.
        """
        _scalar_types = (int, float, str, bool, type(None))
        if params:
            for key, value in params.items():
                is_scalar = isinstance(value, _scalar_types) or (
                    isinstance(value, list)
                    and all(isinstance(i, _scalar_types) for i in value)
                )
                if not is_scalar:
                    warnings.warn(
                        f"PipelineTracker.step(): param '{key}' has a non-scalar "
                        f"value of type '{type(value).__name__}'. Non-scalar params "
                        "are not round-trippable and will be omitted from save_recipe().",
                        UserWarning,
                        stacklevel=2,
                    )
        self._executed.append((step_id, op, params or {}))
        yield

    def save_recipe(self, path: str | Path | None = None) -> str:
        """Serialize the recorded execution to a YAML recipe string.

        Builds a new recipe by replacing the original steps list with executed
        steps in execution order. Steps not executed are omitted. Any param
        values recorded in tracker.step() override the original step params.
        Returns the serialized YAML string and optionally writes it to path.
        """
        from ruamel.yaml import YAML

        recipe = copy.deepcopy(self._original)

        # Index original steps by id for lookup.
        original_steps = {s["id"]: s for s in recipe.get("steps", [])}

        new_steps: list[dict[str, Any]] = []
        for step_id, op, recorded_params in self._executed:
            if step_id in original_steps:
                step = copy.deepcopy(original_steps[step_id])
            else:
                step = {"id": step_id, "op": op}

            # Merge recorded params on top of original params.
            if recorded_params:
                step.setdefault("params", {})
                step["params"].update(recorded_params)

            new_steps.append(step)

        recipe["steps"] = new_steps

        yaml = YAML()
        yaml.default_flow_style = False
        yaml.width = 4096
        stream = io.StringIO()
        yaml.dump(recipe, stream)
        serialized = stream.getvalue()

        if path is not None:
            Path(path).write_text(serialized, encoding="utf-8")

        return serialized


def _normalize_recipe_dict(recipe_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a canonical YAML-shaped recipe dict for round-trip serialization.

    The tracker may be initialized either from the original YAML structure
    (with a top-level ``recipe`` block) or from a flattened Recipe model dump.
    Normalize both forms to the YAML layout expected by load_recipe().
    """
    normalized = copy.deepcopy(recipe_dict)
    if "recipe" in normalized:
        return normalized

    recipe_meta = {
        "name": normalized.pop("name", None),
        "version": normalized.pop("version", None),
        "description": normalized.pop("description", None),
        "author": normalized.pop("author", None),
        "schema_version": normalized.pop("schema_version", None),
    }

    normalized["recipe"] = recipe_meta
    return normalized
