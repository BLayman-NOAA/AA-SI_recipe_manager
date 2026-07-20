# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Warn-only environment check against curated-run provenance.

When a run hits the shared survey cache, the artifacts it reuses were
produced by the *curator's* environment, recorded in the provenance file
published next to the survey cache. This module compares the local
environment against that record and reports mismatches — prominently for
packages that implement ops used by the recipe, quietly otherwise.

The check never blocks or downgrades a cache hit (global_cache_plan.md
§5.1/§6.2): the whole point of a curated cache is that users consume the
blessed products even on slightly different environments. The mismatch
report is the honest footnote, and exact reproduction remains available by
recreating the environment from the provenance file.
"""

from __future__ import annotations

import importlib.metadata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Mapping

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import PipelineDAG

PROMINENT = "prominent"
NOTE = "note"


@dataclass
class EnvMismatch:
    """One package whose local version differs from the curated environment."""

    package: str
    curated_version: str | None
    local_version: str | None
    severity: str  # PROMINENT for op-implementing packages, NOTE otherwise
    provenance_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "package": self.package,
            "curated_version": self.curated_version,
            "local_version": self.local_version,
            "severity": self.severity,
            "provenance_ref": self.provenance_ref,
        }


def _curated_version(entry: Any) -> str | None:
    """Version string from a provenance ``resolved_dependencies`` entry.

    Entries are ``{"installed_version": ..., "source": ...}`` dicts in current
    provenance files; bare strings are accepted for older records.
    """
    if isinstance(entry, str):
        return entry
    if isinstance(entry, Mapping):
        value = entry.get("installed_version")
        return str(value) if value is not None else None
    return None


def _op_packages(dag: PipelineDAG) -> set[str]:
    """Packages that implement ops used by this DAG (prominent-warning set)."""
    packages: set[str] = set()
    for node in dag.nodes.values():
        impl = node.implementation
        if impl is not None and impl.dependency is not None:
            packages.add(impl.dependency.name)
        custom = node.step.custom_spec
        if custom is not None and custom.dependency is not None:
            packages.add(custom.dependency.name)
    return packages


def check_environment_against_provenance(
    provenance: Mapping[str, Any],
    dag: PipelineDAG,
) -> list[EnvMismatch]:
    """Compare the live environment to a curated provenance record.

    Returns one :class:`EnvMismatch` per package whose locally installed
    version differs from the version recorded by the curated run (including
    packages recorded there but not installed locally). Packages whose
    curated version was not resolvable (``"unknown"``) are skipped — there is
    nothing meaningful to compare.
    """
    deps = provenance.get("resolved_dependencies") or {}
    prominent_packages = _op_packages(dag)
    mismatches: list[EnvMismatch] = []
    for package, entry in sorted(deps.items()):
        curated = _curated_version(entry)
        if not curated or curated == "unknown":
            continue
        try:
            local: str | None = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            local = None
        if local == curated:
            continue
        mismatches.append(
            EnvMismatch(
                package=package,
                curated_version=curated,
                local_version=local,
                severity=PROMINENT if package in prominent_packages else NOTE,
            )
        )
    return mismatches
