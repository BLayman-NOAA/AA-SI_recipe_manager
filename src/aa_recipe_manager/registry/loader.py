# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Load YAML spec files and populate the registry."""

from __future__ import annotations

import copy
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from aa_recipe_manager.model.types import Implementation, Spec
from aa_recipe_manager.registry.registry import Registry

_BUILTIN_SPECS_DIR = Path(__file__).parent / "builtin" / "specs"
EXPERIMENTAL_SPECS_DIR = _BUILTIN_SPECS_DIR / "experimental"

#: Root of the opt in spec sets shipped with the package. Each set is a folder
#: ``contrib/<name>/specs/`` and is loaded only when named, never by default.
CONTRIB_SPECS_ROOT = Path(__file__).parent / "contrib"
_CONTRIB_PREFIX = "contrib:"

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


def resolve_spec_dir(spec_dir: str | Path) -> Path:
    """Return the folder a ``spec_dirs`` entry or ``--spec-dir`` value names.

    Args:
        spec_dir: A folder path, or ``contrib:<name>`` for a spec set shipped
            under ``registry/contrib/<name>/specs``.

    Returns:
        The folder path.

    Raises:
        FileNotFoundError: If the folder does not exist.
    """
    text = str(spec_dir)
    if text.startswith(_CONTRIB_PREFIX):
        name = text[len(_CONTRIB_PREFIX):]
        path = CONTRIB_SPECS_ROOT / name / "specs"
        if not path.is_dir():
            available = sorted(
                p.parent.name for p in CONTRIB_SPECS_ROOT.glob("*/specs") if p.is_dir()
            )
            raise FileNotFoundError(
                f"No contrib spec set named {name!r}. Available: {available}"
            )
        return path
    path = Path(spec_dir)
    if not path.is_dir():
        raise FileNotFoundError(f"Spec folder not found: {path}")
    return path


def spec_libraries(spec_dirs: Iterable[str | Path] = ()) -> list[Path]:
    """Folders searched for ops that are not built in.

    The given folders come first, in order, then every contrib set shipped
    under ``registry/contrib/<name>/specs``.

    Raises:
        FileNotFoundError: If a given folder does not exist.
    """
    folders = [resolve_spec_dir(spec_dir) for spec_dir in spec_dirs]
    folders += sorted(p for p in CONTRIB_SPECS_ROOT.glob("*/specs") if p.is_dir())
    return list(dict.fromkeys(folders))


def load_registry(
    ops: Iterable[str] = (), spec_dirs: Iterable[str | Path] = ()
) -> Registry:
    """Return the built in registry plus the library specs for ``ops``.

    Each op that is not built in is looked up in the spec libraries (see
    :func:`spec_libraries`) by the ``op`` its spec file declares, and only the
    specs a recipe uses are loaded. A library can therefore add ops but never
    replace a built in one, so built in step hashes do not depend on it. An op
    found in no library is left out, for the DAG builder to report.

    Args:
        ops: Op names a recipe uses, normally :func:`recipe_ops`.
        spec_dirs: Extra library folders, or ``contrib:<name>``.

    Returns:
        A populated Registry.

    Raises:
        FileNotFoundError: If a given folder does not exist.
        ValueError: If two library files define the same needed op.
    """
    registry = load_builtin_registry()
    missing = sorted({op for op in ops if not registry.has_spec(op)})
    if not missing:
        return registry
    found: dict[str, list[Path]] = {}
    for folder in spec_libraries(spec_dirs):
        for spec_path in sorted(folder.glob("*.yaml")):
            raw = _read_spec_file(spec_path)
            op = raw.get("op") if isinstance(raw, dict) else None
            if op in missing:
                found.setdefault(op, []).append(spec_path)
    for op, paths in found.items():
        if len(paths) > 1:
            raise ValueError(
                f"Op {op!r} is defined by more than one spec library: "
                + ", ".join(str(path) for path in paths)
            )
        _load_spec_file(paths[0], registry)
    return registry


def recipe_ops(recipe: Any) -> set[str]:
    """Op names used by a recipe's steps, after includes are flattened."""
    return {step.op for step in recipe.steps if step.op != "custom"}


def _load_spec_file(path: Path, registry: Registry) -> None:
    """Parse one spec YAML file and register its spec and implementations."""
    _register_spec_data(_read_spec_file(path), path, registry)


def _register_spec_data(raw: Any, path: Path, registry: Registry) -> None:
    """Register the spec and implementations parsed from ``path``."""
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
