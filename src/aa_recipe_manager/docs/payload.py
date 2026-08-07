# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Turn the op registry into the JSON document the HTML reference renders.

The payload carries every field a reader needs, already flattened and ordered,
so the page itself only has to loop over lists. Nothing in here imports a
scientific package unless source resolution is asked for.
"""

from __future__ import annotations

import importlib.metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager.docs import sources

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import (
        Implementation,
        ParamDeclaration,
        PortDeclaration,
        Spec,
    )
    from aa_recipe_manager.registry.registry import Registry

#: Bumped when the payload shape changes in a way the template must react to.
PAYLOAD_SCHEMA = 1

_SOURCES_DISABLED = "source links were not requested"


def build_payload(
    registry: Registry | None = None,
    *,
    resolve_sources: bool = True,
) -> dict[str, Any]:
    """Build the JSON-safe document model for every op in the registry.

    Args:
        registry: Registry to document. Defaults to the built-in registry.
        resolve_sources: Import each implementation's package to attach its
            source location, signature, and docstring. Disable to skip all
            third-party imports.

    Returns:
        A dict containing only JSON-serializable values.
    """
    if registry is None:
        from aa_recipe_manager.registry.loader import load_builtin_registry

        registry = load_builtin_registry()

    ops = [
        _op_entry(
            registry.get_spec(op), _implementations(registry, op), resolve_sources
        )
        for op in registry.list_ops()
    ]

    implementations = [impl for op in ops for impl in op["implementations"]]
    return {
        "schema": PAYLOAD_SCHEMA,
        "generator": _generator(),
        "source_links": resolve_sources,
        "counts": {
            "ops": len(ops),
            "implementations": len(implementations),
            "sources_resolved": sum(
                1 for impl in implementations if impl["source"]["resolved"]
            ),
        },
        "categories": sorted({op["category"] for op in ops if op["category"]}),
        "ops": ops,
    }


def unresolved_ops(payload: dict[str, Any]) -> list[str]:
    """Op names whose implementation source could not be located."""
    return [
        op["op"]
        for op in payload["ops"]
        if any(not impl["source"]["resolved"] for impl in op["implementations"])
    ]


def stale_link_ops(payload: dict[str, Any]) -> list[str]:
    """Op names linked to a file that has uncommitted local edits."""
    return [
        op["op"]
        for op in payload["ops"]
        if any(
            impl["source"]["note"] == sources.DIRTY_NOTE
            for impl in op["implementations"]
        )
    ]


def _generator() -> str:
    """Name and version of the package that produced a payload."""
    try:
        version = importlib.metadata.version("aa-recipe-manager")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return f"aa-recipe-manager {version}"


def _implementations(registry: Registry, op: str) -> list[Implementation]:
    """Every implementation registered for an op, in key order."""
    return [
        registry.get_implementation(op, key, check_versions=False)
        for key in registry.list_implementations(op)
    ]


def _op_entry(
    spec: Spec, implementations: list[Implementation], resolve_sources: bool
) -> dict[str, Any]:
    """One op's full documentation entry."""
    entry = {
        "op": spec.op,
        "description": _clean(spec.description),
        "category": spec.category,
        "sink": spec.sink,
        "cache_key": spec.cache_key or spec.op,
        "version": spec.version,
        "inputs": [_port_entry(name, port) for name, port in spec.inputs.items()],
        "outputs": [_port_entry(name, port) for name, port in spec.outputs.items()],
        "params": [_param_entry(name, param) for name, param in spec.params.items()],
        "implementations": [
            _impl_entry(impl, resolve_sources) for impl in implementations
        ],
    }
    entry["search"] = _search_blob(entry)
    return entry


def _port_entry(name: str, port: PortDeclaration) -> dict[str, Any]:
    """A single input or output row."""
    return {
        "name": name,
        "type": port.type,
        "description": _clean(port.description),
        "required": port.required,
        "default": _json_safe(port.default),
        "many": port.many,
        "expected_variables": list(port.expected_variables or []),
        "expected_coords": list(port.expected_coords or []),
        "provenance_role": port.provenance_role,
    }


def _param_entry(name: str, param: ParamDeclaration) -> dict[str, Any]:
    """A single parameter row."""
    return {
        "name": name,
        "type": param.type,
        "units": param.units,
        "description": _clean(param.description),
        "default": _json_safe(param.default),
        "required": param.required,
        "constraints": _json_safe(param.constraints or {}),
        "fingerprint_mode": param.fingerprint_mode,
    }


def _impl_entry(impl: Implementation, resolve_sources: bool) -> dict[str, Any]:
    """One implementation, including where its callable lives."""
    dependency = impl.dependency
    if resolve_sources:
        location = sources.resolve_source(
            impl.callable_path,
            distribution=dependency.name if dependency else None,
            fallback_url=dependency.url if dependency else None,
        )
    else:
        location = sources.unresolved(impl.callable_path, _SOURCES_DISABLED)

    return {
        "key": impl.key,
        "default": impl.default,
        "callable_path": impl.callable_path,
        "version": impl.version,
        "tested_versions": list(impl.tested_versions or []),
        "setup": impl.setup,
        "teardown": impl.teardown,
        "dependency": (
            None
            if dependency is None
            else {
                "name": dependency.name,
                "version": dependency.version,
                "source": dependency.source,
                "url": dependency.url,
            }
        ),
        "param_map": [
            {"spec": key, "callable": value} for key, value in impl.param_map.items()
        ],
        "output_map": [
            {"spec": key, "expression": value} for key, value in impl.output_map.items()
        ],
        "source": location.to_dict(),
    }


def _search_blob(entry: dict[str, Any]) -> str:
    """Lowercase haystack the sidebar filter matches against."""
    parts: list[str] = [
        entry["op"],
        entry["category"] or "",
        entry["description"] or "",
    ]
    for group in ("inputs", "outputs", "params"):
        for row in entry[group]:
            parts.append(row["name"])
            parts.append(row["description"] or "")
    for impl in entry["implementations"]:
        parts.append(impl["callable_path"])
        if impl["dependency"]:
            parts.append(impl["dependency"]["name"])
    return " ".join(" ".join(parts).split()).lower()


def _clean(text: str | None) -> str | None:
    """Collapse the trailing newline a folded YAML block leaves behind."""
    if text is None:
        return None
    return str(text).strip() or None


def _json_safe(value: Any) -> Any:
    """Coerce a value (including ruamel scalar subclasses) to plain JSON types."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return str(value)
