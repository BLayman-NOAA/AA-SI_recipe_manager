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

# Packages the extra includes that don't come from a spec dependency block
# (notebook runtime essentials).
NON_SPEC_EXTRAS = {"ipykernel", "ipywidgets"}


def _spec_dep_names() -> set[str]:
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


def test_extra_has_no_unused_entries():
    spec_deps = _spec_dep_names()
    extra_pkgs = _extra_pkg_names()
    unused = extra_pkgs - spec_deps - NON_SPEC_EXTRAS
    assert not unused, (
        "Entries in [project.optional-dependencies].all-builtin-specs are not "
        "required by any built-in spec (and are not in the allow-list of "
        f"notebook runtime extras): {sorted(unused)}"
    )
