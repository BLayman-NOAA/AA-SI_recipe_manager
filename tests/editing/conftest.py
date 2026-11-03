# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Reference recipe files and fixture copies for the editing tests.

The example recipes live in the AA-SI_Full_Pipeline_Example repo, a sibling of
this one in the workspace; tests that need them skip when it is not checked
out alongside.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
FIXTURES = REPO / "tests" / "fixtures" / "client_recipes"
DOCS_RECIPES = REPO / "docs" / "recipes"
EXAMPLES = REPO.parent / "AA-SI_Full_Pipeline_Example" / "example_recipes"
UC1 = EXAMPLES / "HB1603" / "UC1"
HAVE_EXAMPLES = EXAMPLES.is_dir()

#: Folders whose YAML files are run outputs or demo caches, not recipes.
SKIPPED_FOLDERS = {"outputs", "demo"}


def _yaml_files(root: Path) -> list[Path]:
    """Every ``*.yaml`` under ``root`` outside output and demo folders."""
    return [
        path
        for path in sorted(root.rglob("*.yaml"))
        if not SKIPPED_FOLDERS & set(path.relative_to(root).parts[:-1])
    ]


#: Every reference YAML file, the example recipes first.
REFERENCE_YAML = [
    *(_yaml_files(EXAMPLES) if HAVE_EXAMPLES else []),
    *_yaml_files(DOCS_RECIPES),
    *_yaml_files(FIXTURES),
]

UC1_ROOTS = [
    UC1 / name
    for name in (
        "survey_sv.yaml",
        "dive_analysis.yaml",
        "dive_analysis_raw_cal.yaml",
        "demo_survey_sv.yaml",
        "demo_dive_analysis.yaml",
    )
]

DOCS_ROOTS = [
    DOCS_RECIPES / "quickstart" / "quickstart.yaml",
    DOCS_RECIPES / "HB1603" / "dive_analysis.yaml",
    DOCS_RECIPES / "HB1603" / "survey_echogram_pyramid.yaml",
    DOCS_RECIPES / "HB2407" / "Herring_10-15_per_file.yaml",
    *sorted((DOCS_RECIPES / "calibration").glob("*.yaml")),
]


def _reference_id(path: Path) -> str:
    for root in (EXAMPLES, REPO):
        if path.is_relative_to(root):
            return path.relative_to(root).as_posix()
    return path.name


@pytest.fixture(params=REFERENCE_YAML, ids=_reference_id)
def reference_yaml(request) -> Path:
    """One reference YAML file."""
    return request.param


def copy_fixture(tmp_path: Path, name: str) -> Path:
    """Copy a client fixture recipe and its ``sub/`` folder into ``tmp_path``.

    Args:
        tmp_path: The folder to copy into.
        name: The fixture's file name, such as ``with_sub.yaml``.

    Returns:
        The copied root recipe's path.
    """
    shutil.copy2(FIXTURES / name, tmp_path / name)
    shutil.copytree(FIXTURES / "sub", tmp_path / "sub", dirs_exist_ok=True)
    return tmp_path / name
