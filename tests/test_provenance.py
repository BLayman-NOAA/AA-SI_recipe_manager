# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the provenance recorder."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from aa_recipe_manager.model.types import Provenance
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.provenance.recorder import (
    ProvenanceRecorder,
    to_dict,
    to_json,
    to_netcdf_attrs,
    to_yaml,
)
from aa_recipe_manager.registry.loader import load_builtin_registry


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


def _build_four_step_dag(tmp_path: Path):
    raw_dir = tmp_path / "raw_files"
    raw_dir.mkdir()
    recipe_text = FOUR_STEP_RECIPE.replace(
        "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
    ).replace(
        "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
    )
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(recipe_text))
    recipe = load_recipe(p)
    reg = load_builtin_registry()
    return build_dag(recipe, reg), p


class TestProvenanceCapture:
    def test_returns_provenance_object(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        assert isinstance(prov, Provenance)

    def test_recipe_name_and_version(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        assert prov.recipe_name == "simple_ek60_pipeline"
        assert prov.recipe_version == "1.0"

    def test_recipe_hash_is_sha256_hex(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        assert len(prov.recipe_hash) == 64
        assert all(c in "0123456789abcdef" for c in prov.recipe_hash)

    def test_recipe_hash_from_file(self, tmp_path):
        dag, recipe_path = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag, recipe_path=recipe_path)
        assert len(prov.recipe_hash) == 64

    def test_python_version_present(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        assert prov.python_version

    def test_os_info_present(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        assert prov.os_info

    def test_timestamp_is_utc(self, tmp_path):
        from datetime import timezone

        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        assert prov.timestamp.tzinfo == timezone.utc

    def test_resolved_steps_present(self):
        from aa_recipe_manager.model.types import (
            DAGNode,
            Dependency,
            Implementation,
            PipelineDAG,
            Recipe,
            Spec,
            Step,
        )

        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl = Implementation(
            op="compute_sv", key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
        )
        recipe = Recipe(
            name="test", version="1.0", schema_version="1",
            steps=[Step(id="step1", op="compute_sv")],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={"step1": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl)},
            edges=[],
            topological_order=["step1"],
        )
        prov = ProvenanceRecorder.capture(dag)
        assert len(prov.resolved_steps) == 1
        assert "step1" in prov.resolved_steps

    def test_resolved_steps_have_callable_path(self):
        from aa_recipe_manager.model.types import (
            DAGNode,
            Dependency,
            Implementation,
            PipelineDAG,
            Recipe,
            Spec,
            Step,
        )

        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl = Implementation(
            op="compute_sv", key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
        )
        recipe = Recipe(
            name="test", version="1.0", schema_version="1",
            steps=[Step(id="step1", op="compute_sv")],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={"step1": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl)},
            edges=[],
            topological_order=["step1"],
        )
        prov = ProvenanceRecorder.capture(dag)
        for step_info in prov.resolved_steps.values():
            assert step_info.callable_path

    def test_resolved_dependencies_present(self):
        from aa_recipe_manager.model.types import (
            DAGNode,
            Dependency,
            Implementation,
            PipelineDAG,
            Recipe,
            Spec,
            Step,
        )

        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl = Implementation(
            op="compute_sv", key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
        )
        recipe = Recipe(
            name="test", version="1.0", schema_version="1",
            steps=[Step(id="step1", op="compute_sv")],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={"step1": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl)},
            edges=[],
            topological_order=["step1"],
        )
        prov = ProvenanceRecorder.capture(dag)
        assert isinstance(prov.resolved_dependencies, dict)
        assert "packaging" in prov.resolved_dependencies

    def test_unknown_package_stored_as_unknown(self, tmp_path):
        """Packages not installed should store 'unknown' without raising."""
        from aa_recipe_manager.model.types import (
            DAGNode,
            Dependency,
            Implementation,
            PipelineDAG,
            Recipe,
            Spec,
            Step,
        )

        dep = Dependency(name="nonexistent_xyz_pkg", version=">=1.0", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl = Implementation(
            op="compute_sv",
            key="key",
            callable_path="pkg.func",
            dependency=dep,
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="compute_sv")],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={"step1": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl)},
            edges=[],
            topological_order=["step1"],
        )
        prov = ProvenanceRecorder.capture(dag)
        assert prov.resolved_dependencies.get("nonexistent_xyz_pkg") == "unknown"


class TestProvenanceSerializers:
    def test_to_dict_returns_dict(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        d = to_dict(prov)
        assert isinstance(d, dict)
        assert "recipe_hash" in d

    def test_to_json_is_valid_json(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        s = to_json(prov)
        parsed = json.loads(s)
        assert parsed["recipe_name"] == "simple_ek60_pipeline"

    def test_to_yaml_is_string(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        s = to_yaml(prov)
        assert isinstance(s, str)
        assert "recipe_hash" in s

    def test_to_netcdf_attrs_flat_dict(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        attrs = to_netcdf_attrs(prov)
        assert isinstance(attrs, dict)
        assert all(isinstance(v, str) for v in attrs.values())

    def test_to_netcdf_attrs_prefixed_keys(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        attrs = to_netcdf_attrs(prov)
        assert all(k.startswith("provenance_") for k in attrs)

    def test_to_netcdf_attrs_required_fields(self, tmp_path):
        dag, _ = _build_four_step_dag(tmp_path)
        prov = ProvenanceRecorder.capture(dag)
        attrs = to_netcdf_attrs(prov)
        for key in [
            "provenance_recipe_hash",
            "provenance_recipe_name",
            "provenance_recipe_version",
            "provenance_timestamp",
            "provenance_python_version",
            "provenance_os_info",
        ]:
            assert key in attrs


class TestCaptureEnvironment:
    def test_capture_environment_returns_dict(self):
        result = ProvenanceRecorder.capture_environment()
        assert isinstance(result, dict)
        assert "python_version" in result
        assert "timestamp" in result

    def test_capture_environment_with_packages(self):
        result = ProvenanceRecorder.capture_environment(["packaging"])
        assert "installed_packages" in result
        assert "packaging" in result["installed_packages"]

    def test_capture_environment_unknown_pkg(self):
        result = ProvenanceRecorder.capture_environment(["nonexistent_xyz_pkg_abc"])
        assert result["installed_packages"]["nonexistent_xyz_pkg_abc"] == "unknown"
