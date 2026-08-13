# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for create_env() and the env CLI subcommand."""

from __future__ import annotations

import sys
from typing import Any
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


# ---------------------------------------------------------------------------
# create_env_from_provenance
# ---------------------------------------------------------------------------


class TestCreateEnvFromProvenance:
    def _write_provenance(
        self,
        tmp_path: object,
        deps: dict,
        python_version: str = "3.10.4",
        *,
        rich: bool = False,
    ) -> object:
        """Write a provenance.yaml.

        When *rich* is True, deps values are expected to already be dicts with
        ``installed_version``/``source``/``url`` keys (new format). When False
        (default), deps values are plain version strings (legacy format).
        """
        from pathlib import Path
        import io
        from ruamel.yaml import YAML

        prov_path = Path(str(tmp_path)) / "provenance.yaml"
        data = {
            "python_version_number": python_version,
            "resolved_dependencies": deps,
        }
        yaml = YAML()
        yaml.default_flow_style = False
        stream = io.StringIO()
        yaml.dump(data, stream)
        prov_path.write_text(stream.getvalue(), encoding="utf-8")
        return prov_path

    def test_creates_venv_from_provenance(self, tmp_path):
        prov_path = self._write_provenance(tmp_path, {"packaging": "21.0"})
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = api.create_env_from_provenance(prov_path, env_path)
        first_args = mock_run.call_args_list[0][0][0]
        assert "venv" in first_args
        assert str(env_path) in first_args
        assert result.env_path == env_path

    def test_installs_pinned_packages_legacy_format(self, tmp_path):
        """Flat string (legacy) format still pins correctly."""
        prov_path = self._write_provenance(tmp_path, {"packaging": "21.0", "numpy": "1.24.0"})
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = api.create_env_from_provenance(prov_path, env_path)
        all_calls_str = str(mock_run.call_args_list)
        assert "packaging==21.0" in all_calls_str
        assert "numpy==1.24.0" in all_calls_str
        assert result.installed

    def test_installs_pinned_packages_rich_format(self, tmp_path):
        """New rich dict format installs PyPI packages with pinned versions."""
        deps = {
            "echopype": {"installed_version": "0.11.1", "source": "pypi"},
            "numpy": {"installed_version": "1.24.0", "source": "pypi"},
        }
        prov_path = self._write_provenance(tmp_path, deps, rich=True)
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            result = api.create_env_from_provenance(prov_path, env_path)
        all_calls_str = str(mock_run.call_args_list)
        assert "echopype==0.11.1" in all_calls_str
        assert "numpy==1.24.0" in all_calls_str
        assert result.installed

    def test_git_source_installed_as_git_url(self, tmp_path):
        """Packages with source=git are installed via git+ URL."""
        git_url = "https://github.com/BLayman-NOAA/AA-SI_Utils.git"
        deps = {
            "aa-si-utils": {"installed_version": "0.2.0", "source": "git", "url": git_url},
        }
        prov_path = self._write_provenance(tmp_path, deps, rich=True)
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            api.create_env_from_provenance(prov_path, env_path)
        all_calls_str = str(mock_run.call_args_list)
        assert f"git+{git_url}" in all_calls_str

    def test_local_source_without_override_goes_to_skipped_local(self, tmp_path):
        """source=local packages without a --local-pkg override are skipped, not failed."""
        import warnings

        deps = {"aa-si-ml": {"installed_version": "0.1.0", "source": "local"}}
        prov_path = self._write_provenance(tmp_path, deps, rich=True)
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = api.create_env_from_provenance(prov_path, env_path)
        assert "aa-si-ml" in result.skipped_local
        assert any("aa-si-ml" in str(warning.message) for warning in w)

    def test_failed_pypi_install_goes_to_skipped_local(self, tmp_path):
        """Packages not found on PyPI (legacy format) are skipped with a warning."""
        import warnings

        prov_path = self._write_provenance(
            tmp_path, {"echopype": "0.11.1", "aa-si-utils": "0.1.0"}
        )
        env_path = tmp_path / "test_env"

        def fake_run(args, **kwargs):
            mock = MagicMock()
            if "aa-si-utils==0.1.0" in args:
                mock.returncode = 1
            else:
                mock.returncode = 0
            return mock

        with patch("subprocess.run", side_effect=fake_run):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                result = api.create_env_from_provenance(prov_path, env_path)

        assert "aa-si-utils" in result.skipped_local
        assert any("aa-si-utils" in str(warning.message) for warning in w)
        assert any("echopype==0.11.1" in pkg for pkg in result.installed)

    def test_skips_version_pin_for_unknown_version(self, tmp_path):
        prov_path = self._write_provenance(tmp_path, {"some-pkg": "unknown"})
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            api.create_env_from_provenance(prov_path, env_path)
        all_calls_str = str(mock_run.call_args_list)
        assert "some-pkg==unknown" not in all_calls_str
        assert "some-pkg" in all_calls_str

    def test_local_override_used_for_editable_install(self, tmp_path):
        prov_path = self._write_provenance(tmp_path, {"aa-si-utils": "0.5.0"})
        env_path = tmp_path / "test_env"
        override_path = "/path/to/aa-si-utils"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            api.create_env_from_provenance(
                prov_path, env_path, local_overrides={"aa-si-utils": override_path}
            )
        all_calls_str = str(mock_run.call_args_list)
        assert override_path in all_calls_str
        assert "-e" in all_calls_str

    def test_python_version_mismatch_emits_warning(self, tmp_path):
        import warnings

        prov_path = self._write_provenance(tmp_path, {}, python_version="2.7.18")
        env_path = tmp_path / "test_env"
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            with warnings.catch_warnings(record=True) as w:
                warnings.simplefilter("always")
                api.create_env_from_provenance(prov_path, env_path)
        assert any("2.7.18" in str(warning.message) for warning in w)

    def test_invalid_provenance_raises_value_error(self, tmp_path):
        bad_path = tmp_path / "bad.yaml"
        bad_path.write_text(": invalid: yaml: [[[", encoding="utf-8")
        with pytest.raises((ValueError, Exception)):
            api.create_env_from_provenance(bad_path, tmp_path / "env")


@pytest.mark.e2e
class TestEnvCreateFromProvenanceCLI:
    def _write_provenance(self, tmp_path: Any, deps: dict) -> Any:
        import io
        from ruamel.yaml import YAML

        prov_path = tmp_path / "provenance.yaml"
        data = {"python_version_number": "3.10.4", "resolved_dependencies": deps}
        yaml = YAML()
        stream = io.StringIO()
        yaml.dump(data, stream)
        prov_path.write_text(stream.getvalue(), encoding="utf-8")
        return prov_path

    def test_create_from_provenance_exits_zero(self, cli_runner, tmp_path):
        """env create auto-detects a provenance file and exits cleanly."""
        prov_path = self._write_provenance(tmp_path, {"packaging": "21.0"})
        env_path = tmp_path / "test_env"
        args = ["env", "create", str(prov_path), "--path", str(env_path)]
        with patch("subprocess.run", return_value=MagicMock(returncode=0)):
            result = cli_runner.invoke(main, args)
        assert result.exit_code == 0, f"CLI failed:\n{result.output}"
        assert str(env_path) in result.output

    def test_env_create_help_describes_both_file_types(self, cli_runner):
        result = cli_runner.invoke(main, ["env", "create", "--help"])
        assert result.exit_code == 0
        assert "provenance" in result.output.lower()
        assert "recipe" in result.output.lower()



@pytest.mark.e2e
class TestCreateEnvRefusesConflicts:
    """A conflicted recipe must fail loudly instead of installing one side.

    The resolver keeps one entry per package name, so before this an
    unreconcilable recipe quietly produced an environment holding a build some
    step had not asked for.
    """

    @staticmethod
    def _conflicted():
        from aa_recipe_manager.resolver.dependencies import (
            ResolvedDependencies,
            ResolvedDependency,
        )

        resolved = ResolvedDependencies()
        resolved.packages["echopype"] = ResolvedDependency(
            name="echopype",
            merged_specifier="",
            source="git",
            url="https://github.com/OSOceanAcoustics/echopype.git@abc123",
            requiring_steps=["resample", "detect"],
            conflict=True,
            conflict_message="Package 'echopype' required from two different git URLs.",
        )
        return resolved

    def test_create_env_raises_and_installs_nothing(self, tmp_path):
        from aa_recipe_manager.exceptions import DependencyConflictError

        with (
            patch("aa_recipe_manager.api._load_dag", return_value=MagicMock()),
            patch(
                "aa_recipe_manager.resolver.dependencies.resolve_dependencies",
                return_value=self._conflicted(),
            ),
            patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run,
            pytest.raises(DependencyConflictError) as excinfo,
        ):
            api.create_env("fake_recipe.yaml", tmp_path / "env")

        assert "two different git URLs" in str(excinfo.value)
        # The venv is never even created, so there is no half-built env left.
        assert mock_run.call_count == 0

    def test_conflict_message_names_the_requiring_steps(self, tmp_path):
        from aa_recipe_manager.exceptions import DependencyConflictError

        with (
            patch("aa_recipe_manager.api._load_dag", return_value=MagicMock()),
            patch(
                "aa_recipe_manager.resolver.dependencies.resolve_dependencies",
                return_value=self._conflicted(),
            ),
            patch("subprocess.run", return_value=MagicMock(returncode=0)),
            pytest.raises(DependencyConflictError) as excinfo,
        ):
            api.create_env("fake_recipe.yaml", tmp_path / "env")

        message = str(excinfo.value)
        assert "resample" in message and "detect" in message
