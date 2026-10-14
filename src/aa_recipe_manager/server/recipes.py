# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Find the recipe files under the configured recipe roots."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

SKIPPED_FOLDERS = {"node_modules", ".venv", "exe_temp", "outputs", "recipe_cache"}


@dataclass
class RecipeEntry:
    """One recipe file found under a root.

    Attributes:
        name: ``recipe.name`` from the file.
        description: ``recipe.description``, if any.
        version: ``recipe.version``, if any.
        path: Absolute posix path of the file.
        root: Absolute posix path of the root it was found under.
        relative: Posix path of the file relative to the root.
        modified: Last modification time, ISO 8601 UTC.
        included_by: Absolute paths of the recipes that include this file.
        error: Why the file could not be read as YAML, with the line, or
            None for a readable recipe.
    """

    name: str
    description: str | None
    version: str | None
    path: str
    root: str
    relative: str
    modified: str
    included_by: list[str] = field(default_factory=list)
    error: str | None = None


#: Parsed files by path: the (mtime, size) read, the recipe or None, the error.
ScanCache = dict[str, tuple[tuple[int, int], dict[str, Any] | None, str | None]]


def scan_recipes(
    roots: list[Path], cache: ScanCache | None = None
) -> list[RecipeEntry]:
    """List every recipe under the roots, sorted by relative path.

    A YAML file is a recipe when its top level has ``recipe`` and ``steps``.
    A file that is not valid YAML is listed with its error, since it may be a
    recipe with a typo.

    Args:
        roots: Folders to search.
        cache: Parses kept between calls; a file is read again only when its
            modification time or size changes.
    """
    cache = {} if cache is None else cache
    entries: dict[str, RecipeEntry] = {}
    includes: dict[str, list[str]] = {}
    for root in roots:
        root = Path(root).resolve()
        for path in _yaml_files(root):
            key = path.as_posix()
            if key in entries:
                continue
            raw, error = _read(path, cache)
            if raw is None and error is None:
                continue
            info = raw["recipe"] if raw and isinstance(raw["recipe"], dict) else {}
            entries[key] = RecipeEntry(
                name=str(info.get("name") or path.stem),
                description=_text(info.get("description")),
                version=_text(info.get("version")),
                path=key,
                root=root.as_posix(),
                relative=path.relative_to(root).as_posix(),
                modified=_modified(path),
                error=error,
            )
            includes[key] = _included_files(raw, path) if raw else []
    for key, children in includes.items():
        for child in children:
            if child in entries:
                entries[child].included_by.append(key)
    for entry in entries.values():
        entry.included_by.sort()
    return sorted(entries.values(), key=lambda entry: (entry.relative, entry.path))


def _yaml_files(root: Path):
    for folder, subfolders, files in os.walk(root):
        subfolders[:] = sorted(
            name
            for name in subfolders
            if not name.startswith(".") and name not in SKIPPED_FOLDERS
        )
        for name in sorted(files):
            lower = name.lower()
            if lower.endswith((".yaml", ".yml")) and not lower.endswith(
                (".config.yaml", ".config.yml")
            ):
                yield Path(folder) / name


def _read(path: Path, cache: ScanCache) -> tuple[dict[str, Any] | None, str | None]:
    """The file as a recipe (None when it is not one) and its parse error."""
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = cache.get(path.as_posix())
    if cached is not None and cached[0] == stamp:
        return cached[1], cached[2]
    raw, error = None, None
    try:
        with open(path, encoding="utf-8") as handle:
            loaded = YAML(typ="safe").load(handle)
    except Exception as exc:
        error = str(exc)
    else:
        if isinstance(loaded, dict) and "recipe" in loaded and "steps" in loaded:
            raw = loaded
    cache[path.as_posix()] = (stamp, raw, error)
    return raw, error


def _included_files(raw: dict[str, Any], path: Path) -> list[str]:
    """Files a recipe includes, resolved beside it as the recipe loader does."""
    steps = raw.get("steps")
    if not isinstance(steps, list):
        return []
    found = []
    for step in steps:
        if isinstance(step, dict) and isinstance(step.get("include"), str):
            found.append((path.parent / step["include"]).resolve().as_posix())
    return found


def _text(value: Any) -> str | None:
    return None if value is None else str(value)


def _modified(path: Path) -> str:
    stamp = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
    return stamp.isoformat(timespec="seconds").replace("+00:00", "Z")
