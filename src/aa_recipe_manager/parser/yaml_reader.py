# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Parse YAML recipe files into Recipe objects."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from aa_recipe_manager.exceptions import RecipeParseError
from aa_recipe_manager.model.types import Recipe


def _new_yaml() -> YAML:
    """A round trip parser for one load; ruamel parsers are not thread safe."""
    yaml = YAML()
    yaml.preserve_quotes = True
    return yaml


def _strip_ruamel_types(value: Any) -> Any:
    """Recursively convert ruamel.yaml container/scalar subclasses to plain types.

    ruamel.yaml's round-trip loader (which we use to preserve quote style) returns
    subclasses such as ``CommentedMap``, ``CommentedSeq``, ``DoubleQuotedScalarString``,
    ``ScalarInt``, etc. Downstream user callables often serialize values with
    PyYAML's ``SafeDumper``/custom ``Dumper`` subclasses, whose single-type
    representer dispatch keys on ``type(data) is X`` (no MRO walk). Subclass
    instances therefore raise ``RepresenterError`` even though they ARE ``str``
    or ``int``. Stripping to plain Python types at the parser boundary removes
    this whole class of foot-gun.

    Booleans must be checked before ``int`` because ``bool`` subclasses ``int``.
    Unknown types are returned unchanged.
    """
    if isinstance(value, dict):
        return {_strip_ruamel_types(k): _strip_ruamel_types(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_strip_ruamel_types(item) for item in value]
    if isinstance(value, bool):
        return bool(value)
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    return value


def load_recipe(path: str | Path) -> Recipe:
    """Parse a YAML recipe file and return a validated Recipe object.

    Raises RecipeParseError if the file cannot be read or fails validation.
    """
    path = Path(path)
    try:
        with open(path, encoding="utf-8") as f:
            raw = _strip_ruamel_types(_new_yaml().load(f))
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
            own_inputs = _own_inputs(child_raw)
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
            bindings = _input_bindings(child_resolved.raw.get("steps") or [], overrides)
            child_raw = _apply_input_overrides(child_resolved.raw, overrides)
            child_steps = child_raw.get("steps") or []
            # map_over on an include entry fans the *whole* sub-workflow out
            # once per segment (FR-14.4): every included step becomes mapped
            # over the same source so they form one mapped chain, and the
            # sub-recipe's entry input is bound to ${_item} via input_overrides
            # (e.g. ``input_overrides: {store_path: ${_item}}``).
            map_over = step.get("map_over")
            if map_over is not None:
                for child in child_steps:
                    if isinstance(child, dict) and not child.get("map_over"):
                        child["map_over"] = map_over
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
            promoted = _merge_child_inputs(raw, child_raw, overrides)
            root = stack[0] if stack else path
            info = child_resolved.raw.get("recipe") or {}
            include_blocks.append(
                {
                    "source": include_path.name,
                    "step_ids": child_ids,
                    "path": _include_path_label(include_path, root),
                    "name": info.get("name"),
                    "description": info.get("description"),
                    "overrides": dict(overrides),
                    "map_over": map_over,
                    "bindings": bindings,
                    "inputs": own_inputs,
                    "promoted": promoted,
                }
            )
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
            raw = _strip_ruamel_types(_new_yaml().load(f))
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


def _include_path_label(include_path: Path, root: Path) -> str:
    """Posix path of an included file relative to the root file's folder.

    Falls back to the absolute path when no relative path exists, as for a
    file on another Windows drive.
    """
    try:
        return Path(os.path.relpath(include_path, root.parent)).as_posix()
    except ValueError:
        return include_path.as_posix()


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


def _input_bindings(
    steps: list[Any],
    overrides: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    """Where each overridden input is substituted, as (step, slot) pairs.

    The steps are the included recipe's, with its own includes already
    resolved, so an input passed on through a nested include points at the
    nested steps that finally read it. A slot is ``inputs.<port>``,
    ``params.<name>`` or another step key such as ``map_over``.

    Args:
        steps: The included recipe's steps before the overrides are applied.
        overrides: The include entry's ``input_overrides``.

    Returns:
        Each override name and the slots it was substituted into, in step
        order.
    """
    bindings: dict[str, list[dict[str, str]]] = {name: [] for name in overrides}
    for step in steps:
        if not isinstance(step, dict) or "id" not in step:
            continue
        slots: list[tuple[str, Any]] = []
        for key, value in step.items():
            if key in ("inputs", "params") and isinstance(value, dict):
                slots.extend((f"{key}.{name}", item) for name, item in value.items())
            elif key != "id":
                slots.append((key, value))
        for slot, value in slots:
            for name, replacement in overrides.items():
                if _substitutes(value, f"${{inputs.{name}}}", replacement):
                    bindings[name].append({"step": str(step["id"]), "slot": slot})
    return bindings


def _substitutes(value: Any, ref: str, replacement: Any) -> bool:
    """Whether _replace_input_refs replaces ``ref`` somewhere in a value."""
    if isinstance(value, str):
        return value == ref or (ref in value and isinstance(replacement, str))
    if isinstance(value, list):
        return any(_substitutes(item, ref, replacement) for item in value)
    if isinstance(value, dict):
        return any(_substitutes(item, ref, replacement) for item in value.values())
    return False


def _collect_step_ids(steps: list[Any]) -> list[str]:
    step_ids: list[str] = []
    for step in steps:
        if isinstance(step, dict) and "id" in step:
            step_ids.append(str(step["id"]))
    return step_ids


def _own_inputs(raw: dict[str, Any]) -> dict[str, Any]:
    """The inputs a file declares itself, copied before its includes add to them."""
    inputs = raw.get("inputs")
    return dict(inputs) if isinstance(inputs, dict) else {}


def _merge_child_inputs(
    parent_raw: dict[str, Any],
    child_raw: dict[str, Any],
    overrides: dict[str, Any],
) -> list[str]:
    """Copy a child's inputs into its parent where neither sets them.

    Returns:
        The names added to the parent's inputs, in the child's order.
    """
    child_inputs = child_raw.get("inputs") or {}
    if not isinstance(child_inputs, dict):
        return []
    parent_inputs = parent_raw.setdefault("inputs", {})
    if not isinstance(parent_inputs, dict):
        return []
    promoted: list[str] = []
    for input_name, declaration in child_inputs.items():
        if input_name in overrides or input_name in parent_inputs:
            continue
        parent_inputs[input_name] = declaration
        promoted.append(input_name)
    return promoted


def _flatten_recipe_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge the recipe: sub-section with top-level inputs/steps/outputs.

    YAML recipe files use a 'recipe:' block for metadata and separate
    top-level keys for inputs, steps, and outputs. This function merges
    them into a flat dict matching the Recipe model's field layout.
    """
    merged: dict[str, Any] = {}

    if "recipe" in raw:
        merged.update(raw["recipe"])

    # ``execution`` (executor selection, dask_config/prefect_config, checkpoint
    # policy) is a top-level block per design §5; keep supporting it nested in
    # ``recipe:`` too, with the explicit top-level value winning.
    for key in ("inputs", "steps", "outputs", "include_blocks", "execution"):
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

