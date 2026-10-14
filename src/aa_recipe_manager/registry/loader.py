# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Load built-in YAML spec files and populate the registry."""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from aa_recipe_manager.model.types import Implementation, Spec
from aa_recipe_manager.registry.registry import Registry

_BUILTIN_SPECS_DIR = Path(__file__).parent / "builtin" / "specs"
EXPERIMENTAL_SPECS_DIR = _BUILTIN_SPECS_DIR / "experimental"

#: Parsed spec files by path, with the (mtime, size) they were parsed at.
_parsed_specs: dict[Path, tuple[tuple[int, int], Any]] = {}


def load_builtin_registry() -> Registry:
    """Scan the built-in specs directory and return a populated Registry.

    Each .yaml file may contain a spec definition and an optional
    'implementations' list. Specs without implementations are valid.

    Specs under ``experimental/`` are registered alongside the rest so recipes
    can use them, but they are held to a different promise: they may depend on
    unreleased packages, so the ``all-builtin-specs`` extra deliberately does
    not cover them and a recipe using one needs its own environment from
    ``aa-recipe env create``.
    """
    registry = Registry()
    for spec_path in sorted(_BUILTIN_SPECS_DIR.glob("*.yaml")):
        _load_spec_file(spec_path, registry)
    for spec_path in sorted(EXPERIMENTAL_SPECS_DIR.glob("*.yaml")):
        _load_spec_file(spec_path, registry)
    return registry


def experimental_ops() -> set[str]:
    """Return the op names whose specs live under ``experimental/``."""
    names: set[str] = set()
    for path in EXPERIMENTAL_SPECS_DIR.glob("*.yaml"):
        with open(path, encoding="utf-8") as f:
            raw = YAML().load(f) or {}
        names.add(raw.get("op", path.stem))
    return names


def load_registry_file(path: str | Path, registry: Registry) -> None:
    """Load a single external spec YAML file into an existing registry.

    Allows users to extend the registry with project-local or plugin specs.
    """
    _load_spec_file(Path(path), registry)


def _load_spec_file(path: Path, registry: Registry) -> None:
    """Parse one spec YAML file and register its spec and implementations."""
    raw: dict[str, Any] = _read_spec_file(path)

    if not isinstance(raw, dict):
        raise ValueError(f"Spec file '{path}' must be a YAML mapping.")

    raw = dict(raw)
    implementations_data: list[dict[str, Any]] = raw.pop("implementations", None) or []

    try:
        spec = Spec.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid spec in '{path}':\n{exc}") from exc

    registry.register_spec(spec, path=path)

    for impl_data in implementations_data:
        impl_data = dict(impl_data)
        impl_data.setdefault("op", spec.op)
        try:
            impl = Implementation.model_validate(impl_data)
        except ValidationError as exc:
            raise ValueError(
                f"Invalid implementation in '{path}' (key '{impl_data.get('key')}'):\n{exc}"
            ) from exc
        registry.register_implementation(impl)


def _read_spec_file(path: Path) -> Any:
    """Parse a spec file, reusing the last parse while the file is unchanged.

    Parsing dominates building the registry, which happens for every recipe
    load, so a long running process such as the client server parses each
    file once. Each caller gets its own copy. Every parse uses its own YAML
    instance, because a ruamel parser is not safe to share between threads.
    """
    stat = path.stat()
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _parsed_specs.get(path)
    if cached is None or cached[0] != stamp:
        with open(path, encoding="utf-8") as f:
            cached = (stamp, YAML().load(f))
        _parsed_specs[path] = cached
    return copy.deepcopy(cached[1])
