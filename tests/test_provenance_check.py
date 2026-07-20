# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for curated provenance publication + the warn-only environment check.

Curated (survey write tier) runs publish their provenance next to the survey
cache and stamp every sidecar with a ``provenance_ref``; user runs that hit
the survey tier diff their environment against that record and warn — never
error, never skip the hit.
"""

from __future__ import annotations

import importlib.metadata
import json
import warnings as warnings_module

import pytest

from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.executor.sequential import MANIFEST_FILENAME
from aa_recipe_manager.provenance.env_check import (
    check_environment_against_provenance,
)

from test_executor import (  # noqa: F401  (helper scaffolding)
    _iter_meta,
    _linear_inc_dag,
    helper_module,
)


def _curated_prewarm(dag, tmp_path):
    return SequentialExecutor().execute(
        dag,
        inputs={"seed": 1},
        output_dir=tmp_path / "curator_cache",
        survey_cache_dir=tmp_path / "survey",
        cache_write_tier="survey",
        checkpoint_mode="eager",
    )


def _user_run(dag, tmp_path, **kwargs):
    return SequentialExecutor().execute(
        dag,
        inputs={"seed": 1},
        output_dir=tmp_path / "user_cache",
        survey_cache_dir=tmp_path / "survey",
        checkpoint_mode="eager",
        **kwargs,
    )


def _provenance_files(tmp_path):
    return sorted((tmp_path / "survey" / "provenance").glob("*.json"))


class TestProvenancePublication:
    def test_curated_run_publishes_provenance(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        result = _curated_prewarm(dag, tmp_path)

        files = _provenance_files(tmp_path)
        assert len(files) == 1
        assert files[0].name == f"stage6_pipeline@{result.run_id}.json"
        provenance = json.loads(files[0].read_text(encoding="utf-8"))
        assert provenance["recipe_name"] == "stage6_pipeline"
        assert "resolved_dependencies" in provenance
        assert provenance["inputs"] == {"seed": 1}

    def test_curated_sidecars_carry_provenance_ref(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        result = _curated_prewarm(dag, tmp_path)
        expected_ref = f"provenance/stage6_pipeline@{result.run_id}.json"
        sidecars = _iter_meta(tmp_path / "survey")
        assert sidecars
        assert all(meta["provenance_ref"] == expected_ref for meta in sidecars)

    def test_user_run_does_not_publish_provenance(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        _curated_prewarm(dag, tmp_path)
        before = _provenance_files(tmp_path)
        _user_run(dag, tmp_path)
        assert _provenance_files(tmp_path) == before
        # User-tier sidecars carry no provenance_ref.
        for meta in _iter_meta(tmp_path / "user_cache"):
            assert meta["provenance_ref"] is None


class TestEnvironmentCheckUnit:
    def _provenance(self, deps):
        return {"resolved_dependencies": deps}

    def test_op_package_mismatch_is_prominent(self):
        dag = _linear_inc_dag()  # ops depend on package "pytest" (see _dep())
        curated = self._provenance(
            {"pytest": {"installed_version": "0.0.1-curated", "source": "pypi"}}
        )
        mismatches = check_environment_against_provenance(curated, dag)
        assert len(mismatches) == 1
        assert mismatches[0].package == "pytest"
        assert mismatches[0].severity == "prominent"
        assert mismatches[0].curated_version == "0.0.1-curated"
        assert mismatches[0].local_version == importlib.metadata.version("pytest")

    def test_non_op_package_mismatch_is_note(self):
        dag = _linear_inc_dag()
        curated = self._provenance(
            {"click": {"installed_version": "0.0.1-curated", "source": "pypi"}}
        )
        mismatches = check_environment_against_provenance(curated, dag)
        assert [m.severity for m in mismatches] == ["note"]

    def test_matching_versions_produce_no_mismatch(self):
        dag = _linear_inc_dag()
        local = importlib.metadata.version("pytest")
        curated = self._provenance(
            {"pytest": {"installed_version": local, "source": "pypi"}}
        )
        assert check_environment_against_provenance(curated, dag) == []

    def test_unknown_curated_version_skipped(self):
        dag = _linear_inc_dag()
        curated = self._provenance(
            {"pytest": {"installed_version": "unknown", "source": "local"}}
        )
        assert check_environment_against_provenance(curated, dag) == []

    def test_package_missing_locally_reported(self):
        dag = _linear_inc_dag()
        curated = self._provenance(
            {"no-such-package-anywhere": {"installed_version": "1.0", "source": "pypi"}}
        )
        mismatches = check_environment_against_provenance(curated, dag)
        assert len(mismatches) == 1
        assert mismatches[0].local_version is None


class TestEnvironmentCheckEndToEnd:
    def test_survey_hit_with_version_mismatch_warns_but_still_hits(
        self, helper_module, tmp_path, monkeypatch
    ):
        dag = _linear_inc_dag()
        _curated_prewarm(dag, tmp_path)

        # Simulate the user having a different version of the op-implementing
        # package than the curator recorded.
        real_version = importlib.metadata.version

        def bumped(name):
            if name == "pytest":
                return f"{real_version(name)}.userpatch"
            return real_version(name)

        monkeypatch.setattr(importlib.metadata, "version", bumped)

        helper_module.call_log.clear()
        with pytest.warns(RuntimeWarning, match="op-implementing package"):
            result = _user_run(dag, tmp_path)

        # The hit is unaffected: nothing recomputed.
        assert result.executed_steps == []
        assert helper_module.call_log == []
        assert result.step_dispositions["second"].disposition == "hit-survey-cache"
        # The mismatch is recorded on the result and in the manifest.
        assert result.environment_mismatches
        mismatch = result.environment_mismatches[0]
        assert mismatch["package"] == "pytest"
        assert mismatch["severity"] == "prominent"
        assert mismatch["provenance_ref"].startswith("provenance/")

        manifest = json.loads(
            (tmp_path / "outputs" / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        assert manifest["environment_mismatches"] == result.environment_mismatches

    def test_matching_environment_produces_no_mismatches(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _curated_prewarm(dag, tmp_path)
        with warnings_module.catch_warnings():
            warnings_module.simplefilter("error", RuntimeWarning)
            result = _user_run(dag, tmp_path)
        assert result.environment_mismatches == []

    def test_missing_provenance_file_soft_warns_and_run_succeeds(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _curated_prewarm(dag, tmp_path)
        for prov_file in _provenance_files(tmp_path):
            prov_file.unlink()

        with pytest.warns(RuntimeWarning, match="could not be read"):
            result = _user_run(dag, tmp_path)
        # Hits are unaffected; the check is skipped softly.
        assert result.executed_steps == []
        assert result.environment_mismatches == []

    def test_user_tier_hits_perform_no_check(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        # Warm the USER tier only (no curated run at all).
        _user_run(dag, tmp_path)
        with warnings_module.catch_warnings():
            warnings_module.simplefilter("error", RuntimeWarning)
            result = _user_run(dag, tmp_path)
        assert result.environment_mismatches == []
        assert result.step_dispositions["second"].disposition == "hit-user-cache"
