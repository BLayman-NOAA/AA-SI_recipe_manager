# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Per-user run configuration file discovery and loading.

Storage locations (``output_dir``, ``temp_dir``, ``outputs_dir``,
``survey_cache_dir``) and cloud credentials (``storage_options``) are
environment-specific, so they belong in a per-user config file rather than the
portable recipe. This module discovers and loads that file; the ``run`` CLI
command merges it under any explicit flags.

The recipe stays shareable (no bucket paths baked in); each user keeps their own
git-ignored config with their buckets.

Discovery order (first found wins):
  1. an explicit path (``--config`` / the ``explicit`` argument)
  2. the ``AA_RECIPE_CONFIG`` environment variable
  3. ``<recipe_dir>/<recipe_stem>.config.yaml`` — the per-recipe config, so
     multiple recipes sharing a directory can each target different buckets
  4. ``./aa-recipe.config.yaml`` (current working directory; shared defaults)
  5. ``~/.config/aa-recipe/config.yaml``

Only one file is loaded (no merging across locations).

Value precedence when the ``run`` command applies the config:
    explicit CLI flag > config file > recipe default > built-in default.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

CONFIG_FILENAME = "aa-recipe.config.yaml"
ENV_VAR = "AA_RECIPE_CONFIG"

#: Keys accepted at the top level of the config file.
_KNOWN_KEYS = frozenset(
    {
        "output_dir",
        "temp_dir",
        "outputs_dir",
        "survey_cache_dir",
        "storage_options",
        "inputs",
    }
)


@dataclass
class RunConfig:
    """Resolved contents of a per-user run configuration file.

    Every field is optional; an absent config yields an all-empty instance, so
    callers can unconditionally consult it and fall back to their own defaults.

    ``survey_cache_dir`` is the shared (curated) cache read tier — everyone
    reads it, only curated runs write it. The *write tier* is deliberately not
    a config key: writing to the survey tier is a per-run act selected with
    ``--cache-write-tier`` (and enforced by bucket IAM, not by this client).
    """

    output_dir: str | None = None
    temp_dir: str | None = None
    outputs_dir: str | None = None
    survey_cache_dir: str | None = None
    storage_options: dict[str, Any] | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    #: Path the config was loaded from, or ``None`` when no file was found.
    source: Path | None = None


def default_config_search_paths() -> list[Path]:
    """Conventional auto-discovery locations, in precedence order."""
    return [
        Path.cwd() / CONFIG_FILENAME,
        Path.home() / ".config" / "aa-recipe" / "config.yaml",
    ]


def recipe_config_candidate(recipe_path: str | os.PathLike[str]) -> Path:
    """Per-recipe config path: ``<recipe_stem>.config.yaml`` beside the recipe.

    E.g. ``example_recipes/hb1603_gcs.yaml`` -> ``example_recipes/hb1603_gcs.config.yaml``.
    Recipe-relative (not cwd-relative), so it is found no matter where the
    command is run from.
    """
    recipe = Path(recipe_path)
    return recipe.with_name(f"{recipe.stem}.config.yaml")


def discover_config_path(
    explicit: str | os.PathLike[str] | None = None,
    recipe_path: str | os.PathLike[str] | None = None,
) -> Path | None:
    """Locate a run-config file, or return ``None`` if none is found.

    An *explicit* path (or one named by ``AA_RECIPE_CONFIG``) that does not
    exist is an error — the user clearly intended a specific file. The
    per-recipe and conventional locations are simply skipped when absent.
    """
    if explicit is not None:
        path = Path(explicit)
        if not path.is_file():
            raise FileNotFoundError(f"Config file not found: {path}")
        return path

    env_value = os.environ.get(ENV_VAR)
    if env_value:
        path = Path(env_value)
        if not path.is_file():
            raise FileNotFoundError(
                f"{ENV_VAR} points to a missing config file: {path}"
            )
        return path

    if recipe_path is not None:
        candidate = recipe_config_candidate(recipe_path)
        if candidate.is_file():
            return candidate

    for candidate in default_config_search_paths():
        if candidate.is_file():
            return candidate
    return None


def load_run_config(
    explicit: str | os.PathLike[str] | None = None,
    recipe_path: str | os.PathLike[str] | None = None,
) -> RunConfig:
    """Discover and parse a run-config file into a :class:`RunConfig`.

    Returns an empty ``RunConfig`` when no file is found. Raises ``ValueError``
    for a malformed file (non-mapping top level, unknown keys, or wrong value
    types) so mistakes surface loudly instead of being silently ignored.
    """
    path = discover_config_path(explicit, recipe_path=recipe_path)
    if path is None:
        return RunConfig()

    with open(path, encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    if data is None:
        return RunConfig(source=path)
    if not isinstance(data, dict):
        raise ValueError(
            f"Config file {path} must contain a mapping at the top level, "
            f"got {type(data).__name__}."
        )

    unknown = set(data) - _KNOWN_KEYS
    if unknown:
        raise ValueError(
            f"Config file {path} has unknown key(s): {', '.join(sorted(unknown))}. "
            f"Allowed keys: {', '.join(sorted(_KNOWN_KEYS))}."
        )

    for str_key in ("output_dir", "temp_dir", "outputs_dir", "survey_cache_dir"):
        if str_key in data and data[str_key] is not None and not isinstance(
            data[str_key], str
        ):
            raise ValueError(
                f"Config file {path}: '{str_key}' must be a string, got "
                f"{type(data[str_key]).__name__}."
            )

    storage_options = data.get("storage_options")
    if storage_options is not None and not isinstance(storage_options, dict):
        raise ValueError(
            f"Config file {path}: 'storage_options' must be a mapping, got "
            f"{type(storage_options).__name__}."
        )

    inputs = data.get("inputs") or {}
    if not isinstance(inputs, dict):
        raise ValueError(
            f"Config file {path}: 'inputs' must be a mapping, got "
            f"{type(inputs).__name__}."
        )

    return RunConfig(
        output_dir=data.get("output_dir"),
        temp_dir=data.get("temp_dir"),
        outputs_dir=data.get("outputs_dir"),
        survey_cache_dir=data.get("survey_cache_dir"),
        storage_options=storage_options,
        inputs=dict(inputs),
        source=path,
    )
