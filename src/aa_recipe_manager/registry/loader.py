# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Load built-in YAML spec files and populate the registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML

from aa_recipe_manager.model.types import Implementation, Spec
from aa_recipe_manager.registry.registry import Registry

_yaml = YAML()
_BUILTIN_SPECS_DIR = Path(__file__).parent / "builtin" / "specs"


def load_builtin_registry() -> Registry:
    """Scan the built-in specs directory and return a populated Registry.

    Each .yaml file may contain a spec definition and an optional
    'implementations' list. Specs without implementations are valid.
    """
    registry = Registry()
    for spec_path in sorted(_BUILTIN_SPECS_DIR.glob("*.yaml")):
        _load_spec_file(spec_path, registry)
    return registry


def load_registry_file(path: str | Path, registry: Registry) -> None:
    """Load a single external spec YAML file into an existing registry.

    Allows users to extend the registry with project-local or plugin specs.
    """
    _load_spec_file(Path(path), registry)


def _load_spec_file(path: Path, registry: Registry) -> None:
    """Parse one spec YAML file and register its spec and implementations."""
    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = _yaml.load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Spec file '{path}' must be a YAML mapping.")

    raw = dict(raw)
    implementations_data: list[dict[str, Any]] = raw.pop("implementations", None) or []

    try:
        spec = Spec.model_validate(raw)
    except ValidationError as exc:
        raise ValueError(f"Invalid spec in '{path}':\n{exc}") from exc

    registry.register_spec(spec)

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

