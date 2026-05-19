# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Pytest configuration and shared fixtures."""

import importlib.metadata
from pathlib import Path

import pytest

from aa_recipe_manager.model.types import (
    Dependency,
    Implementation,
    Recipe,
    Spec,
    Step,
)


_LOCAL_PACKAGE_VERSIONS = {
    "aa-si-calibration": "0.1.0",
    "aa-si-ml": "0.1.0",
    "aa-si-utils": "0.1.0",
    "aa-si-visualization": "0.1.0",
    "echopype": "0.6.0",
}


@pytest.fixture(autouse=True)
def patch_local_package_versions(monkeypatch):
    original_version = importlib.metadata.version

    def _version(name: str):
        if name in _LOCAL_PACKAGE_VERSIONS:
            return _LOCAL_PACKAGE_VERSIONS[name]
        return original_version(name)

    monkeypatch.setattr(importlib.metadata, "version", _version)


def make_dependency(**kwargs):
    defaults = {"name": "pytest", "version": ">=7.0", "source": "pypi"}
    return Dependency(**{**defaults, **kwargs})


def make_step(**kwargs):
    defaults = {"id": "compute_sv", "op": "compute_sv"}
    return Step(**{**defaults, **kwargs})


def make_spec(**kwargs):
    defaults = {"op": "compute_sv", "description": "Compute volume backscattering strength."}
    return Spec(**{**defaults, **kwargs})


def make_implementation(**kwargs):
    defaults = {
        "op": "compute_sv",
        "key": "echopype_default",
        "callable_path": "echopype.calibrate.compute_Sv",
        "dependency": make_dependency(),
    }
    return Implementation(**{**defaults, **kwargs})


def make_recipe(**kwargs):
    defaults = {
        "name": "test_pipeline",
        "version": "1.0.0",
        "steps": [make_step()],
        "schema_version": "1",
    }
    return Recipe(**{**defaults, **kwargs})


@pytest.fixture
def hb1603_recipe_path():
    return (
        Path(__file__).parent.parent
        / "example_recipes"
        / "Workshop_example_recipe_refactor"
        / "hb1603_survey_pipeline.yaml"
    )


@pytest.fixture
def hb1603_extra_calibration_recipe_path():
    return (
        Path(__file__).parent.parent
        / "example_recipes"
        / "Workshop_example_recipe_refactor"
        / "extra_calibration_hb1603_survey_pipeline.yaml"
    )


def _hb1603_example_inputs(recipe_path):
    recipe_dir = recipe_path.parent
    return {
        "raw_input_folder": str(recipe_dir / "raw_file_inputs"),
        "cal_input_folder": str(recipe_dir / "calibration_files" / "HB201607_cal"),
        "output_base": str(recipe_dir / "nefsc_uc_e2_cal_outputs"),
        "netcdf_output_folder": str(recipe_dir / "NetCDF-files"),
        "sv_output_folder": str(recipe_dir / "Sv-files"),
        "output_logs_folder": str(recipe_dir / "Output-Logs"),
        "line_file_path": str(
            recipe_dir
            / "line_files"
            / "SpermWhaleClicks_click_data_HB1603_SpermWhaleDive_Span0.2_07252016_2120_UTC.csv"
        ),
    }


@pytest.fixture
def hb1603_example_inputs(hb1603_recipe_path):
    return _hb1603_example_inputs(hb1603_recipe_path)


@pytest.fixture
def hb1603_extra_calibration_inputs(hb1603_extra_calibration_recipe_path):
    return _hb1603_example_inputs(hb1603_extra_calibration_recipe_path)


@pytest.fixture
def cli_runner():
    from click.testing import CliRunner
    return CliRunner()
