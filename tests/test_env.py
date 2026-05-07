# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for create_env() and the env CLI subcommand."""

import sys
from unittest.mock import MagicMock, patch

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.cli import main


@pytest.mark.e2e
class TestCreateEnvAPI:
    def test_returns_env_path(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = api.create_env(
                hb1603_recipe_path,
                env_path,
                inputs=hb1603_example_inputs,
            )
        assert result.env_path == env_path

    def test_creates_venv(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            api.create_env(
                hb1603_recipe_path,
                env_path,
                inputs=hb1603_example_inputs,
            )
        first_args = mock_run.call_args_list[0][0][0]
        assert "-m" in first_args
        assert "venv" in first_args
        assert str(env_path) in first_args

    def test_calls_pip_install(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = api.create_env(
                hb1603_recipe_path,
                env_path,
                inputs=hb1603_example_inputs,
            )
        all_calls_str = str(mock_run.call_args_list)
        assert "pip" in all_calls_str
        assert "install" in all_calls_str
        assert result.installed

    def test_default_python_is_current_interpreter(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            api.create_env(
                hb1603_recipe_path,
                env_path,
                inputs=hb1603_example_inputs,
            )
        first_args = mock_run.call_args_list[0][0][0]
        assert sys.executable in first_args

    def test_custom_python_used_for_venv(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        custom_python = "/custom/python3"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            api.create_env(
                hb1603_recipe_path,
                env_path,
                python=custom_python,
                inputs=hb1603_example_inputs,
            )
        first_args = mock_run.call_args_list[0][0][0]
        assert custom_python in first_args

    def test_local_override_used_for_editable_install(self, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        override_path = "/path/to/aa-si-utils"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = api.create_env(
                hb1603_recipe_path,
                env_path,
                inputs=hb1603_example_inputs,
                local_overrides={"aa-si-utils": override_path},
            )
        all_calls_str = str(mock_run.call_args_list)
        assert override_path in all_calls_str
        assert any(f"-e {override_path}" in pkg for pkg in result.installed)


class TestCreateEnvLocalDep:
    def test_local_source_dep_without_url_goes_to_skipped(self, tmp_path):
        from aa_recipe_manager.resolver.dependencies import ResolvedDependencies, ResolvedDependency

        env_path = tmp_path / "test_env"
        mock_resolved = ResolvedDependencies()
        mock_resolved.packages["my-local-pkg"] = ResolvedDependency(
            name="my-local-pkg",
            merged_specifier="",
            source="local",
            url=None,
            requiring_steps=["step1"],
        )
        with (
            patch("aa_recipe_manager.api._load_dag", return_value=MagicMock()),
            patch(
                "aa_recipe_manager.resolver.dependencies.resolve_dependencies",
                return_value=mock_resolved,
            ),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
        ):
            result = api.create_env("fake_recipe.yaml", env_path)

        assert "my-local-pkg" in result.skipped_local

    def test_local_source_dep_with_url_gets_installed(self, tmp_path):
        from aa_recipe_manager.resolver.dependencies import ResolvedDependencies, ResolvedDependency

        env_path = tmp_path / "test_env"
        mock_resolved = ResolvedDependencies()
        mock_resolved.packages["my-local-pkg"] = ResolvedDependency(
            name="my-local-pkg",
            merged_specifier="",
            source="local",
            url="/path/to/my-local-pkg",
            requiring_steps=["step1"],
        )
        with (
            patch("aa_recipe_manager.api._load_dag", return_value=MagicMock()),
            patch(
                "aa_recipe_manager.resolver.dependencies.resolve_dependencies",
                return_value=mock_resolved,
            ),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
        ):
            result = api.create_env("fake_recipe.yaml", env_path)

        assert not result.skipped_local
        assert any("/path/to/my-local-pkg" in pkg for pkg in result.installed)
        all_calls_str = str(mock_run.call_args_list)
        assert "-e" in all_calls_str


@pytest.mark.e2e
class TestEnvCLI:
    def test_env_create_exits_zero(self, cli_runner, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        args = ["env", "create", str(hb1603_recipe_path), "--path", str(env_path)]
        for name, value in hb1603_example_inputs.items():
            args += ["--input", f"{name}={value}"]
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = cli_runner.invoke(main, args)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    def test_env_create_output_shows_env_path(self, cli_runner, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        args = ["env", "create", str(hb1603_recipe_path), "--path", str(env_path)]
        for name, value in hb1603_example_inputs.items():
            args += ["--input", f"{name}={value}"]
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = cli_runner.invoke(main, args)
        assert str(env_path) in result.output

    def test_env_create_local_pkg_option_accepted(self, cli_runner, hb1603_recipe_path, hb1603_example_inputs, tmp_path):
        env_path = tmp_path / "test_env"
        args = [
            "env", "create", str(hb1603_recipe_path),
            "--path", str(env_path),
            "--local-pkg", "aa-si-utils=/path/to/aa-si-utils",
        ]
        for name, value in hb1603_example_inputs.items():
            args += ["--input", f"{name}={value}"]
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = cli_runner.invoke(main, args)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"

    def test_env_create_bad_local_pkg_format_fails(self, cli_runner, hb1603_recipe_path, tmp_path):
        env_path = tmp_path / "test_env"
        args = [
            "env", "create", str(hb1603_recipe_path),
            "--path", str(env_path),
            "--local-pkg", "bad_format_no_equals",
        ]
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = cli_runner.invoke(main, args)
        assert result.exit_code != 0

    def test_env_group_help_lists_create(self, cli_runner):
        result = cli_runner.invoke(main, ["env", "--help"])
        assert result.exit_code == 0
        assert "create" in result.output

    def test_main_help_lists_env(self, cli_runner):
        result = cli_runner.invoke(main, ["--help"])
        assert "env" in result.output
