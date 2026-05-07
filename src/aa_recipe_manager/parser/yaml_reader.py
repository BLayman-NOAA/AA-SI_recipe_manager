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

    merged = _flatten_recipe_yaml(raw)
    try:
        return Recipe.model_validate(merged)
    except ValidationError as exc:
        raise RecipeParseError(f"Invalid recipe '{path}':\n{exc}") from exc


def _flatten_recipe_yaml(raw: dict[str, Any]) -> dict[str, Any]:
    """Merge the recipe: sub-section with top-level inputs/steps/outputs.

    YAML recipe files use a 'recipe:' block for metadata and separate
    top-level keys for inputs, steps, and outputs. This function merges
    them into a flat dict matching the Recipe model's field layout.
    """
    merged: dict[str, Any] = {}

    if "recipe" in raw:
        merged.update(raw["recipe"])

    for key in ("inputs", "steps", "outputs"):
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

