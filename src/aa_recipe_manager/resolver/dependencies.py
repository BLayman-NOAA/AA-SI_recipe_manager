# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Aggregate and de-duplicate implementation dependencies from a PipelineDAG."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from packaging.specifiers import SpecifierSet
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from aa_recipe_manager.model.types import Dependency, PipelineDAG


@dataclass
class ResolvedDependency:
    """A single package resolved across all steps that require it."""

    name: str
    merged_specifier: str
    source: str  # "pypi", "git", or "local"
    url: str | None
    requiring_steps: list[str]
    conflict: bool = False
    conflict_message: str | None = None


class ResolvedDependencies:
    """Collection of resolved dependencies with manifest emitters."""

    def __init__(self) -> None:
        self.packages: dict[str, ResolvedDependency] = {}

    @property
    def has_conflicts(self) -> bool:
        return any(d.conflict for d in self.packages.values())

    def to_requirements_txt(self) -> str:
        lines: list[str] = []
        for dep in self.packages.values():
            if dep.source == "git":
                url = dep.url or dep.name
                lines.append(f"git+{url}")
            elif dep.source == "local":
                url = dep.url or f"./{dep.name}"
                lines.append(f"-e {url}")
            else:
                spec = dep.merged_specifier
                lines.append(f"{dep.name}{spec}" if spec else dep.name)
        return "\n".join(lines)

    def to_pyproject_snippet(self) -> str:
        lines = ["[project]", "dependencies = ["]
        for dep in self.packages.values():
            if dep.source == "git":
                url = dep.url or dep.name
                lines.append(f'    "{dep.name} @ git+{url}",')
            elif dep.source == "local":
                url = dep.url or f"./{dep.name}"
                lines.append(f'    "{dep.name} @ {url}",')
            else:
                spec = dep.merged_specifier
                entry = f"{dep.name}{spec}" if spec else dep.name
                lines.append(f'    "{entry}",')
        lines.append("]")
        return "\n".join(lines)

    def to_conda_env_yml(self) -> str:
        conda_lines: list[str] = []
        pip_lines: list[str] = []
        for dep in self.packages.values():
            if dep.source == "git":
                url = dep.url or dep.name
                pip_lines.append(f"    - git+{url}")
            elif dep.source == "local":
                url = dep.url or f"./{dep.name}"
                pip_lines.append(f"    - -e {url}")
            else:
                spec = dep.merged_specifier
                conda_lines.append(f"  - {dep.name}{spec}" if spec else f"  - {dep.name}")
        parts = ["name: pipeline-env", "dependencies:"]
        parts.extend(conda_lines)
        if pip_lines:
            parts.append("  - pip:")
            parts.extend(pip_lines)
        return "\n".join(parts)


def resolve_dependencies(dag: PipelineDAG) -> ResolvedDependencies:
    """Walk a PipelineDAG and resolve all implementation dependencies.

    Merges version specifiers for the same package across steps. Detects and
    records conflicts when specifier sets have an empty intersection. Git and
    local dependencies are collected as-is without version merging.
    """
    result = ResolvedDependencies()

    for step_id in dag.topological_order:
        node = dag.nodes[step_id]
        if node.implementation is None:
            continue
        deps: list[Dependency] = []
        if node.implementation.dependency:
            deps.append(node.implementation.dependency)
        for dep in deps:
            _merge_dependency(result, dep, step_id)

    return result


def _merge_dependency(
    result: ResolvedDependencies,
    dep: Dependency,
    step_id: str,
) -> None:
    name = dep.name
    if name not in result.packages:
        result.packages[name] = ResolvedDependency(
            name=name,
            merged_specifier=dep.version if dep.source == "pypi" else "",
            source=dep.source,
            url=dep.url,
            requiring_steps=[step_id],
        )
        return

    existing = result.packages[name]
    existing.requiring_steps.append(step_id)

    # Non-pypi sources: record URL; no version merging needed.
    if dep.source != "pypi" or existing.source != "pypi":
        if existing.source != dep.source:
            existing.conflict = True
            existing.conflict_message = (
                f"Package '{name}' required as both '{existing.source}' "
                f"and '{dep.source}' source types."
            )
        elif dep.source == "git" and dep.url and existing.url and dep.url != existing.url:
            existing.conflict = True
            existing.conflict_message = (
                f"Package '{name}' required from two different git URLs: "
                f"'{existing.url}' and '{dep.url}'."
            )
        return

    # Merge pypi version specifiers.
    combined = f"{existing.merged_specifier},{dep.version}".strip(",")
    try:
        merged = SpecifierSet(combined)
        if not _specifier_is_satisfiable(merged):
            existing.conflict = True
            existing.conflict_message = (
                f"Package '{name}' has incompatible version requirements: "
                f"'{existing.merged_specifier}' and '{dep.version}'."
            )
        else:
            existing.merged_specifier = str(merged)
    except Exception:
        existing.conflict = True
        existing.conflict_message = (
            f"Package '{name}' has unparseable version requirements: '{combined}'."
        )


def _specifier_is_satisfiable(spec: SpecifierSet) -> bool:
    """Heuristic satisfiability check using versions derived from the bounds.

    The previous implementation tested only a fixed global sample of versions,
    which produced false conflicts for valid open intervals like ``>1.0,<1.1``.
    This version probes candidates near each declared bound instead.
    """
    raw = str(spec).strip()
    if not raw:
        return True

    candidates: set[Version] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue

        operator, version_text = _split_specifier(part)
        if version_text.endswith(".*"):
            version_text = version_text[:-2]

        try:
            base = Version(version_text)
        except InvalidVersion:
            continue

        candidates.update(_nearby_versions(base))
        if operator in {">", ">="}:
            candidates.update(_higher_nearby_versions(base))
        elif operator in {"<", "<="}:
            candidates.update(_lower_nearby_versions(base))
        elif operator == "~=":
            candidates.update(_compatible_nearby_versions(base))

    return any(candidate in spec for candidate in sorted(candidates))


def _split_specifier(part: str) -> tuple[str, str]:
    for operator in ("~=", "===", "==", "!=", ">=", "<=", ">", "<"):
        if part.startswith(operator):
            return operator, part[len(operator) :].strip()
    return "", part


def _normalize_release(release: tuple[int, ...], *, min_parts: int = 3) -> list[int]:
    normalized = list(release)
    while len(normalized) < min_parts:
        normalized.append(0)
    return normalized


def _version_from_parts(parts: list[int], *, trim: bool = False) -> Version:
    if trim:
        while len(parts) > 1 and parts[-1] == 0:
            parts.pop()
    return Version(".".join(str(part) for part in parts))


def _nearby_versions(base: Version) -> set[Version]:
    parts = _normalize_release(base.release)
    return {
        base,
        _version_from_parts(parts.copy()),
        _version_from_parts(parts.copy(), trim=True),
    }


def _higher_nearby_versions(base: Version) -> set[Version]:
    parts = _normalize_release(base.release)
    patch_bump = parts.copy()
    patch_bump[-1] += 1

    minor_bump = parts.copy()
    minor_bump[-2] += 1
    minor_bump[-1] = 0

    return {
        _version_from_parts(patch_bump),
        _version_from_parts(minor_bump),
    }


def _lower_nearby_versions(base: Version) -> set[Version]:
    """Return a spread of versions below *base* to probe ``<`` and ``<=`` bounds.

    For each component of the release tuple, we decrement that component and
    try several values for the trailing components (0, 1, 5, 9) rather than
    hardcoding 9.  This ensures that narrow ranges like ``>=1.2.0,<1.3.0``
    containing a ``!=1.2.9`` exclusion are still detected as satisfiable
    because candidates like 1.2.8 or 1.2.5 will be probed.
    """
    parts = _normalize_release(base.release)
    candidates: set[Version] = set()
    trailing_probes = [0, 1, 5, 9]
    for index in range(len(parts) - 1, -1, -1):
        if parts[index] == 0:
            continue
        lowered = parts.copy()
        lowered[index] -= 1
        # Probe multiple trailing values instead of always filling with 9.
        for trailing in trailing_probes:
            candidate = lowered.copy()
            for reset_index in range(index + 1, len(candidate)):
                candidate[reset_index] = trailing
            candidates.add(_version_from_parts(candidate))
    return candidates


def _compatible_nearby_versions(base: Version) -> set[Version]:
    parts = _normalize_release(base.release)
    candidates = _nearby_versions(base) | _higher_nearby_versions(base)

    upper = parts.copy()
    if len(base.release) <= 2:
        upper[0] += 1
        for index in range(1, len(upper)):
            upper[index] = 0
    else:
        upper[-2] += 1
        upper[-1] = 0
    candidates.add(_version_from_parts(upper))
    return candidates
