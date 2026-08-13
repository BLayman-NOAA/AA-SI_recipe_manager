# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Ensure the `all-builtin-specs` extra in pyproject.toml stays in sync with
the dependencies declared by the built-in spec registry.

If this test fails, either:
  * a new built-in spec introduced a dependency not listed in the extra, or
  * the extra references a package no built-in spec actually requires.
Update pyproject.toml's `[project.optional-dependencies] all-builtin-specs`
list to match.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - fallback for 3.10
    import tomli as tomllib

from ruamel.yaml import YAML

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
SPECS_DIR = REPO_ROOT / "src" / "aa_recipe_manager" / "registry" / "builtin" / "specs"
EXPERIMENTAL_SPECS_DIR = SPECS_DIR / "experimental"

# Packages the extra includes that don't come from a spec dependency block
# (notebook runtime essentials).
NON_SPEC_EXTRAS = {"ipykernel", "ipywidgets"}

# Ports and params allowed to ship without a description. The generated op
# reference renders these as blanks, so the list is empty and should stay that
# way; add an entry only for a field that is deliberately undocumented.
KNOWN_MISSING_DESCRIPTIONS: set[str] = set()


def _spec_dep_names() -> set[str]:
    """Dependency names declared by the stable specs.

    Experimental specs are excluded on purpose: they may pin a package to an
    unreleased build, and the extra is meant to install one environment that
    runs any stable recipe. Rolling those pins in would push every user onto
    an unreleased dependency for ops most of them will never call.
    """
    yaml = YAML(typ="safe")
    names: set[str] = set()
    for path in sorted(SPECS_DIR.glob("*.yaml")):
        spec = yaml.load(path.read_text(encoding="utf-8")) or {}
        for impl in spec.get("implementations", []) or []:
            dep = impl.get("dependency")
            if isinstance(dep, dict) and dep.get("name"):
                names.add(dep["name"])
    return names


def _extra_pkg_names() -> set[str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    extras = data["project"]["optional-dependencies"]["all-builtin-specs"]
    names: set[str] = set()
    for entry in extras:
        # Strip whitespace, then take everything before the first separator.
        token = entry.strip()
        for sep in (" @ ", "[", ">", "<", "=", "!", "~", ";", " "):
            if sep in token:
                token = token.split(sep, 1)[0]
        names.add(token.strip())
    return names


def test_extra_covers_every_builtin_spec_dependency():
    spec_deps = _spec_dep_names()
    extra_pkgs = _extra_pkg_names()
    missing = spec_deps - extra_pkgs
    assert not missing, (
        "Built-in spec dependencies missing from "
        "[project.optional-dependencies].all-builtin-specs in pyproject.toml: "
        f"{sorted(missing)}"
    )


def _experimental_dep_names() -> set[str]:
    """Dependency names declared only by experimental specs."""
    yaml = YAML(typ="safe")
    names: set[str] = set()
    for path in sorted(EXPERIMENTAL_SPECS_DIR.glob("*.yaml")):
        spec = yaml.load(path.read_text(encoding="utf-8")) or {}
        for impl in spec.get("implementations", []) or []:
            dep = impl.get("dependency")
            if isinstance(dep, dict) and dep.get("name"):
                names.add(dep["name"])
    return names


def test_experimental_only_dependencies_stay_out_of_the_extra():
    """The extra must not drag experimental-only pins into every install."""
    extra_pkgs = _extra_pkg_names()
    experimental_only = _experimental_dep_names() - _spec_dep_names()
    leaked = experimental_only & extra_pkgs
    assert not leaked, (
        "These packages are required only by experimental specs and must not "
        "appear in [project.optional-dependencies].all-builtin-specs, which is "
        f"meant to install a working stable environment: {sorted(leaked)}"
    )


def _undescribed_fields() -> set[str]:
    """Every spec, port, and param that ships without a description."""
    yaml = YAML(typ="safe")
    missing: set[str] = set()
    for path in sorted([*SPECS_DIR.glob("*.yaml"), *EXPERIMENTAL_SPECS_DIR.glob("*.yaml")]):
        spec = yaml.load(path.read_text(encoding="utf-8")) or {}
        op = spec.get("op", path.stem)
        if not str(spec.get("description") or "").strip():
            missing.add(op)
        for group in ("inputs", "outputs", "params"):
            for name, declaration in (spec.get(group) or {}).items():
                text = (declaration or {}).get("description")
                if not str(text or "").strip():
                    missing.add(f"{op}.{group}.{name}")
    return missing


def test_no_new_undescribed_spec_fields():
    missing = _undescribed_fields()
    new = missing - KNOWN_MISSING_DESCRIPTIONS
    assert not new, (
        "These spec fields have no description. Add one, or extend "
        f"KNOWN_MISSING_DESCRIPTIONS if that is deliberate: {sorted(new)}"
    )


def test_known_missing_descriptions_has_no_stale_entries():
    stale = KNOWN_MISSING_DESCRIPTIONS - _undescribed_fields()
    assert not stale, (
        "These entries now have descriptions and should be removed from "
        f"KNOWN_MISSING_DESCRIPTIONS: {sorted(stale)}"
    )


def test_extra_has_no_unused_entries():
    spec_deps = _spec_dep_names()
    extra_pkgs = _extra_pkg_names()
    unused = extra_pkgs - spec_deps - NON_SPEC_EXTRAS
    assert not unused, (
        "Entries in [project.optional-dependencies].all-builtin-specs are not "
        "required by any built-in spec (and are not in the allow-list of "
        f"notebook runtime extras): {sorted(unused)}"
    )
