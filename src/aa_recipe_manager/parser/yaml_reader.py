# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Parse YAML recipe files into Recipe objects."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from aa_recipe_manager.exceptions import RecipeParseError
from aa_recipe_manager.model.types import Recipe

_yaml = YAML()
_yaml.preserve_quotes = True


def load_recipe(path: str | Path) -> Recipe:
    """Parse a YAML recipe file and return a validated Recipe object.

    Raises RecipeParseError if the file cannot be read or fails validation.
    """
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            raw = _yaml.load(f)
    except FileNotFoundError:
        raise RecipeParseError(f"Recipe file not found: {path}")
    except Exception as exc:
        raise RecipeParseError(f"Failed to read '{path}': {exc}") from exc

    if not isinstance(raw, dict):
        raise RecipeParseError(f"Expected a YAML mapping at the top level of '{path}'.")

    resolved = _resolve_includes(raw, path.resolve(), (), {})
    merged = _flatten_recipe_yaml(resolved.raw)
    if resolved.include_blocks:
        merged["include_blocks"] = resolved.include_blocks
    try:
        return Recipe.model_validate(merged)
    except ValidationError as exc:
        raise RecipeParseError(f"Invalid recipe '{path}':\n{exc}") from exc


class _ResolvedRecipe:
    def __init__(
        self,
        raw: dict[str, Any],
        include_blocks: list[dict[str, Any]],
        step_sources: dict[str, Path],
    ) -> None:
        self.raw = raw
        self.include_blocks = include_blocks
        self.step_sources = step_sources


def _resolve_includes(
    raw: dict[str, Any],
    path: Path,
    stack: tuple[Path, ...],
    known_step_sources: dict[str, Path],
) -> _ResolvedRecipe:
    """Return a raw recipe dict with include entries replaced by steps."""
    if path in stack:
        chain = " -> ".join(p.name for p in (*stack, path))
        raise RecipeParseError(f"Circular recipe include detected: {chain}")

    steps = raw.get("steps") or []
    if not isinstance(steps, list):
        return _ResolvedRecipe(raw, [], {})

    merged_steps: list[Any] = []
    include_blocks: list[dict[str, Any]] = []
    local_step_sources: dict[str, Path] = {}
    visible_step_sources = dict(known_step_sources)

    for step in steps:
        if _is_include_entry(step):
            include_path = _resolve_include_path(step["include"], path)
            child_raw = _load_raw_recipe(include_path)
            overrides = step.get("input_overrides") or {}
            if not isinstance(overrides, dict):
                raise RecipeParseError(
                    f"include input_overrides must be a mapping in '{path}'."
                )
            child_resolved = _resolve_includes(
                child_raw,
                include_path,
                (*stack, path),
                visible_step_sources,
            )
            child_raw = _apply_input_overrides(child_resolved.raw, overrides)
            child_steps = child_raw.get("steps") or []
            child_ids = _collect_step_ids(child_steps)

            for child_id in child_ids:
                if child_id in visible_step_sources:
                    other = visible_step_sources[child_id]
                    raise RecipeParseError(
                        f"Step id collision for '{child_id}' between "
                        f"'{other}' and '{include_path}'."
                    )
                visible_step_sources[child_id] = include_path
                local_step_sources[child_id] = include_path

            merged_steps.extend(child_steps)
            include_blocks.extend(child_resolved.include_blocks)
            include_blocks.append(
                {"source": include_path.name, "step_ids": child_ids}
            )
            _merge_child_inputs(raw, child_raw, overrides)
            continue

        if isinstance(step, dict) and "id" in step:
            step_id = step["id"]
            if step_id in visible_step_sources:
                other = visible_step_sources[step_id]
                raise RecipeParseError(
                    f"Step id collision for '{step_id}' between '{other}' and '{path}'."
                )
            visible_step_sources[step_id] = path
            local_step_sources[step_id] = path
        merged_steps.append(step)

    resolved_raw = dict(raw)
    resolved_raw["steps"] = merged_steps
    return _ResolvedRecipe(resolved_raw, include_blocks, local_step_sources)


def _load_raw_recipe(path: Path) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as f:
            raw = _yaml.load(f)
    except FileNotFoundError:
        raise RecipeParseError(f"Included recipe file not found: {path}")
    except Exception as exc:
        raise RecipeParseError(
            f"Failed to read included recipe '{path}': {exc}"
        ) from exc

    if not isinstance(raw, dict):
        raise RecipeParseError(
            f"Expected a YAML mapping at the top level of '{path}'."
        )
    return raw


def _is_include_entry(step: Any) -> bool:
    return isinstance(step, dict) and "include" in step and "id" not in step


def _resolve_include_path(include_value: Any, parent_path: Path) -> Path:
    if not isinstance(include_value, str):
        raise RecipeParseError(
            f"include value must be a path string in '{parent_path}'."
        )
    include_path = Path(include_value)
    if not include_path.is_absolute():
        include_path = parent_path.parent / include_path
    return include_path.resolve()


def _apply_input_overrides(
    raw: dict[str, Any],
    overrides: dict[str, Any],
) -> dict[str, Any]:
    if not overrides:
        return raw
    updated = dict(raw)
    updated["steps"] = _replace_input_refs(raw.get("steps") or [], overrides)
    return updated


def _replace_input_refs(value: Any, overrides: dict[str, Any]) -> Any:
    if isinstance(value, str):
        for input_name, replacement in overrides.items():
            ref = f"${{inputs.{input_name}}}"
            if value == ref:
                return replacement
            if isinstance(replacement, str):
                value = value.replace(ref, replacement)
        return value
    if isinstance(value, list):
        return [_replace_input_refs(item, overrides) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_input_refs(item, overrides)
            for key, item in value.items()
        }
    return value


def _collect_step_ids(steps: list[Any]) -> list[str]:
    step_ids: list[str] = []
    for step in steps:
        if isinstance(step, dict) and "id" in step:
            step_ids.append(str(step["id"]))
    return step_ids


def _merge_child_inputs(
    parent_raw: dict[str, Any],
    child_raw: dict[str, Any],
    overrides: dict[str, Any],
) -> None:
    child_inputs = child_raw.get("inputs") or {}
    if not isinstance(child_inputs, dict):
        return
    parent_inputs = parent_raw.setdefault("inputs", {})
    if not isinstance(parent_inputs, dict):
        return
    for input_name, declaration in child_inputs.items():
        if input_name in overrides or input_name in parent_inputs:
            continue
        parent_inputs[input_name] = declaration


def _flatten_recipe_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge the recipe: sub-section with top-level inputs/steps/outputs.

    YAML recipe files use a 'recipe:' block for metadata and separate
    top-level keys for inputs, steps, and outputs. This function merges
    them into a flat dict matching the Recipe model's field layout.
    """
    merged: dict[str, Any] = {}

    if "recipe" in raw:
        merged.update(raw["recipe"])

    for key in ("inputs", "steps", "outputs", "include_blocks"):
        if key in raw:
            merged[key] = raw[key]

    # Normalize None inputs/params in each step to empty dicts.
    for step in merged.get("steps", []) or []:
        if isinstance(step, dict):
            if step.get("inputs") is None:
                step["inputs"] = {}
            if step.get("params") is None:
                step["params"] = {}

    return merged

