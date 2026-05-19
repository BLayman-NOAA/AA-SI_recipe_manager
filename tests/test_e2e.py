# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""End-to-end tests using the real HB1603 example recipes.

These tests exercise the full parse -> registry -> DAG -> dry-run/generate
pipeline against the current example workflows and supporting files.
"""

import ast
import json

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.cli import main


SIMPLIFIED_STEP_IDS = [
    "query_ncei",
    "download_raw",
    "setup_raw_files",
    "gen_cal_mapping",
    "open_raw",
    "extract_cal_params",
    "compute_sv",
    "log_seafloor_stats",
    "detect_seafloor",
    "create_seafloor_mask",
    "create_surface_mask",
    "create_frequency_mask",
    "combine_masks",
    "apply_mask",
    "remove_noise",
    "compute_cell_stats",
    "mask_sparse",
    "compute_mvbs",
    "add_line_overlay",
    "plot_sv_clean",
    "plot_mvbs",
    "reshape_ml",
    "normalize_ml",
    "plot_normalized_ml",
    "run_hdbscan",
    "embed_results",
    "plot_clustering_report",
]


EXTRA_CALIBRATION_STEP_IDS = [
    "query_ncei",
    "download_raw",
    "setup_raw_files",
    "gen_cal_mapping",
    "open_raw",
    "extract_cal_params",
    "compute_sv_baseline",
    "compute_sv_calibrated",
    "log_seafloor_stats",
    "detect_seafloor",
    "create_seafloor_mask",
    "create_surface_mask",
    "create_frequency_mask",
    "combine_masks",
    "apply_mask_baseline",
    "apply_mask_calibrated",
    "remove_noise",
    "compute_cell_stats",
    "mask_sparse",
    "compute_mvbs",
    "add_line_overlay",
    "plot_sv_clean",
    "plot_mvbs",
    "reshape_ml",
    "add_aux_features",
    "normalize_ml",
    "plot_normalized_ml",
    "run_hdbscan",
    "embed_results",
    "plot_clustering_report",
]


@pytest.mark.e2e
class TestDryRunE2E:
    def test_is_valid(self, hb1603_recipe_path, hb1603_example_inputs):
        report = api.dry_run(
            hb1603_recipe_path,
            inputs=hb1603_example_inputs,
            check_versions=False,
        )
        assert report.is_valid, f"Expected valid report but got errors: {report.errors}"

    def test_has_all_steps(self, hb1603_recipe_path, hb1603_example_inputs):
        report = api.dry_run(
            hb1603_recipe_path,
            inputs=hb1603_example_inputs,
            check_versions=False,
        )
        step_ids = {s.step_id for s in report.resolved_steps}
        for expected_id in SIMPLIFIED_STEP_IDS:
            assert expected_id in step_ids, f"Missing step: {expected_id}"
        assert "compute_sv_baseline" not in step_ids
        assert "compute_sv_calibrated" not in step_ids

    def test_no_errors(self, hb1603_recipe_path, hb1603_example_inputs):
        report = api.dry_run(
            hb1603_recipe_path,
            inputs=hb1603_example_inputs,
            check_versions=False,
        )
        assert not report.errors

    def test_visualize_produces_mermaid(self, hb1603_recipe_path, hb1603_example_inputs):
        report = api.dry_run(
            hb1603_recipe_path,
            inputs=hb1603_example_inputs,
            check_versions=False,
            visualize=True,
        )
        assert report.dag_diagram is not None
        assert report.dag_diagram.startswith("graph TD")
        for step_id in SIMPLIFIED_STEP_IDS:
            assert step_id in report.dag_diagram

    def test_format_text_contains_recipe_name(self, hb1603_recipe_path, hb1603_example_inputs):
        report = api.dry_run(
            hb1603_recipe_path,
            inputs=hb1603_example_inputs,
            check_versions=False,
        )
        assert "hb1603_survey_pipeline" in report.format_text()


@pytest.mark.e2e
class TestGenerateE2E:
    def test_generate_notebook_is_valid_json(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        out = api.generate(
            hb1603_recipe_path,
            output=tmp_path / "test.ipynb",
            inputs=hb1603_example_inputs,
        )
        assert out.exists()
        nb = json.loads(out.read_text(encoding="utf-8"))
        assert nb["nbformat"] >= 4

    def test_generate_notebook_contains_all_steps(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        out = api.generate(
            hb1603_recipe_path,
            output=tmp_path / "test.ipynb",
            inputs=hb1603_example_inputs,
        )
        cell_sources = "\n".join(
            "".join(c["source"])
            for c in json.loads(out.read_text(encoding="utf-8"))["cells"]
        )
        for step_id in SIMPLIFIED_STEP_IDS:
            assert step_id in cell_sources, f"Step '{step_id}' missing from notebook"

    def test_generate_notebook_code_cells_valid_syntax(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        out = api.generate(
            hb1603_recipe_path,
            output=tmp_path / "test.ipynb",
            inputs=hb1603_example_inputs,
        )
        nb = json.loads(out.read_text(encoding="utf-8"))
        for i, cell in enumerate(nb["cells"]):
            if cell["cell_type"] == "code":
                source = "".join(cell["source"])
                try:
                    ast.parse(source)
                except SyntaxError as exc:
                    pytest.fail(f"Syntax error in cell {i}: {exc}\n{source}")

    def test_generate_script_contains_all_steps(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        out = api.generate(
            hb1603_recipe_path,
            output=tmp_path / "test.py",
            output_format="script",
            inputs=hb1603_example_inputs,
        )
        assert out.exists()
        source = out.read_text(encoding="utf-8")
        for step_id in SIMPLIFIED_STEP_IDS:
            assert step_id in source, f"Step '{step_id}' missing from script"


@pytest.mark.e2e
class TestCLIE2E:
    def test_dry_run_exits_zero(self, cli_runner, hb1603_recipe_path, hb1603_example_inputs):
        args = ["dry-run", "--no-check-versions", str(hb1603_recipe_path)]
        for name, value in hb1603_example_inputs.items():
            args += ["--input", f"{name}={value}"]
        result = cli_runner.invoke(main, args)
        assert result.exit_code == 0, f"CLI exited non-zero:\n{result.output}"

    def test_dry_run_output_contains_step_ids(self, cli_runner, hb1603_recipe_path, hb1603_example_inputs):
        args = ["dry-run", "--no-check-versions", str(hb1603_recipe_path)]
        for name, value in hb1603_example_inputs.items():
            args += ["--input", f"{name}={value}"]
        result = cli_runner.invoke(main, args)
        for step_id in SIMPLIFIED_STEP_IDS:
            assert step_id in result.output, f"Step '{step_id}' missing from dry-run output"

    def test_generate_exits_zero_and_creates_file(self, cli_runner, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        out_path = tmp_path / "test.ipynb"
        args = ["generate", str(hb1603_recipe_path), "--output", str(out_path)]
        for name, value in hb1603_example_inputs.items():
            args += ["--input", f"{name}={value}"]
        result = cli_runner.invoke(main, args)
        assert result.exit_code == 0, f"CLI exited non-zero:\n{result.output}"
        assert out_path.exists()


@pytest.mark.e2e
class TestExtraCalibrationE2E:
    def test_extra_calibration_recipe_has_expected_steps(
        self,
        hb1603_extra_calibration_recipe_path,
        hb1603_extra_calibration_inputs,
    ):
        report = api.dry_run(
            hb1603_extra_calibration_recipe_path,
            inputs=hb1603_extra_calibration_inputs,
            check_versions=False,
        )
        assert report.is_valid, f"Expected valid report but got errors: {report.errors}"
        step_ids = {s.step_id for s in report.resolved_steps}
        for expected_id in EXTRA_CALIBRATION_STEP_IDS:
            assert expected_id in step_ids, f"Missing step: {expected_id}"

    def test_extra_calibration_generate_script_contains_branch_steps(
        self,
        hb1603_extra_calibration_recipe_path,
        hb1603_extra_calibration_inputs,
        tmp_path,
    ):
        out = api.generate(
            hb1603_extra_calibration_recipe_path,
            output=tmp_path / "extra_calibration.py",
            output_format="script",
            inputs=hb1603_extra_calibration_inputs,
        )
        source = out.read_text(encoding="utf-8")
        for step_id in (
            "compute_sv_baseline",
            "compute_sv_calibrated",
            "apply_mask_baseline",
            "apply_mask_calibrated",
        ):
            assert step_id in source, f"Step '{step_id}' missing from script"
