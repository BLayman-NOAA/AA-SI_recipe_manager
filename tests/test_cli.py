# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""CLI integration tests using click.testing.CliRunner."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from aa_recipe_manager.cli import main


FOUR_STEP_RECIPE = """\
    recipe:
      name: simple_ek60_pipeline
      version: "1.0"
      schema_version: "1"
    inputs:
      raw_input_folder:
        type: path
        default: "__RAW_INPUT_FOLDER__"
      netcdf_output_folder:
        type: path
        default: "__NETCDF_OUTPUT_FOLDER__"
    steps:
      - id: query_ncei
        op: query_ncei_data
        params:
          file_time_start: "2016-07-25T20:58"
          file_time_end: "2016-07-25T21:45"
      - id: download_raw
        op: download_ncei_data
        inputs:
          results: ${query_ncei.ncei_results}
        params:
          output_dir: ${inputs.raw_input_folder}
      - id: setup_files
        op: setup_raw_files
        depends_on: [download_raw]
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          netcdf_output_folder: ${inputs.netcdf_output_folder}
          sv_output_folder: "./sv_files"
          output_logs_folder: "./logs"
      - id: open_raw
        op: open_raw_files
        inputs:
          raw_file_paths: ${setup_files.raw_file_paths}
        params:
          netcdf_output_folder: ${inputs.netcdf_output_folder}
          sonar_model: "EK60"
    """


def _write_recipe(tmp_path: Path) -> Path:
    raw_dir = tmp_path / "raw_files"
    raw_dir.mkdir()
    content = FOUR_STEP_RECIPE.replace(
        "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
    ).replace(
        "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
    )
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(content))
    return p


class TestGenerateCommand:
    def test_generate_exits_zero(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert result.exit_code == 0, result.output

    def test_generate_creates_output_file(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert out.exists()

    def test_generate_prints_output_path(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert "Generated" in result.output

    def test_generate_fails_without_overwrite_if_file_exists(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        out.write_text("{}")
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path), "-o", str(out)])
        assert result.exit_code != 0

    def test_generate_succeeds_with_overwrite_flag(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        out.write_text("{}")
        runner = CliRunner()
        result = runner.invoke(
            main, ["generate", str(recipe_path), "-o", str(out), "--overwrite"]
        )
        assert result.exit_code == 0, result.output

    def test_generate_script_format_creates_python_script(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.py"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(recipe_path), "-o", str(out), "--format", "script"],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()
        assert "tracker = PipelineTracker" in out.read_text(encoding="utf-8")

    def test_generate_no_provenance_omits_provenance_recorder(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(recipe_path), "-o", str(out), "--no-provenance"],
        )
        assert result.exit_code == 0, result.output
        assert "ProvenanceRecorder" not in out.read_text(encoding="utf-8")

    def test_generate_cache_aware_emits_cache_code(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "out.ipynb"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["generate", str(recipe_path), "-o", str(out), "--cache-aware"],
        )
        assert result.exit_code == 0, result.output
        notebook = json.loads(out.read_text(encoding="utf-8"))
        sources: list[str] = []
        for cell in notebook["cells"]:
            source = cell["source"]
            if isinstance(source, list):
                sources.append("".join(source))
            else:
                sources.append(source)
        combined = "\n".join(sources)
        assert '_recipe_manager_cache_dir = "outputs"' in combined

    def test_generate_default_output_path_next_to_recipe(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["generate", str(recipe_path)])
        assert result.exit_code == 0, result.output
        expected = tmp_path / "simple_ek60_pipeline.ipynb"
        assert expected.exists()

    def test_generate_nonexistent_recipe_fails(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["generate", "/no/such/recipe.yaml"])
        assert result.exit_code != 0


class TestDryRunCommand:
    def test_dry_run_exits_zero_for_valid_recipe(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", str(recipe_path), "--no-check-versions"])
        assert result.exit_code == 0, result.output

    def test_dry_run_output_contains_step_ids(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", str(recipe_path), "--no-check-versions"])
        assert "query_ncei" in result.output
        assert "open_raw" in result.output

    def test_dry_run_output_contains_recipe_name(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", str(recipe_path), "--no-check-versions"])
        assert "simple_ek60_pipeline" in result.output

    def test_dry_run_visualize_includes_mermaid(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main, ["dry-run", str(recipe_path), "--no-check-versions", "--visualize"]
        )
        assert "graph TD" in result.output

    def test_dry_run_nonexistent_recipe_fails(self, tmp_path):
        runner = CliRunner()
        result = runner.invoke(main, ["dry-run", "/no/such/recipe.yaml"])
        assert result.exit_code != 0

    def test_dry_run_bad_input_format_fails(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["dry-run", str(recipe_path), "--no-check-versions", "--input", "badformat"],
        )
        assert result.exit_code != 0

    def test_dry_run_input_option_accepted(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        override_dir = tmp_path / "override_raw"
        override_dir.mkdir()
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                "dry-run",
                str(recipe_path),
                "--no-check-versions",
                "--input",
                f"raw_input_folder={override_dir.as_posix()}",
            ],
        )
        assert result.exit_code == 0, result.output
        assert override_dir.as_posix() in result.output


class TestDepsCommand:
    def test_deps_text_exits_zero(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path)])
        assert result.exit_code == 0, result.output

    def test_deps_text_shows_dependency_info(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path)])
        assert "simple_ek60_pipeline" in result.output

    def test_deps_requirements_format(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path), "--format", "requirements"])
        assert result.exit_code == 0, result.output

    def test_deps_conda_format(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path), "--format", "conda"])
        assert result.exit_code == 0, result.output

    def test_deps_pyproject_format(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        runner = CliRunner()
        result = runner.invoke(main, ["deps", str(recipe_path), "--format", "pyproject"])
        assert result.exit_code == 0, result.output

    def test_deps_output_file(self, tmp_path):
        recipe_path = _write_recipe(tmp_path)
        out = tmp_path / "requirements.txt"
        runner = CliRunner()
        result = runner.invoke(
            main,
            ["deps", str(recipe_path), "--format", "requirements", "-o", str(out)],
        )
        assert result.exit_code == 0, result.output
        assert out.exists()


class TestSchemaCommand:
    def test_schema_exits_zero(self):
        runner = CliRunner()
        result = runner.invoke(main, ["schema"])
        assert result.exit_code == 0, result.output

    def test_schema_outputs_valid_json(self):
        runner = CliRunner()
        result = runner.invoke(main, ["schema"])
        parsed = json.loads(result.output)
        assert isinstance(parsed, dict)

    def test_schema_contains_recipe_type(self):
        runner = CliRunner()
        result = runner.invoke(main, ["schema"])
        parsed = json.loads(result.output)
        assert "title" in parsed or "$defs" in parsed or "properties" in parsed

    def test_schema_output_to_file(self, tmp_path):
        out = tmp_path / "schema.json"
        runner = CliRunner()
        result = runner.invoke(main, ["schema", "-o", str(out)])
        assert result.exit_code == 0, result.output
        assert out.exists()
        with open(out) as fh:
            parsed = json.load(fh)
        assert isinstance(parsed, dict)


class TestHelpOutput:
    def test_main_help_lists_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "generate" in result.output
        assert "dry-run" in result.output
        assert "deps" in result.output
        assert "schema" in result.output
