# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the dependency resolver."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from aa_recipe_manager.model.types import (
    Dependency,
    Implementation,
    Spec,
    Step,
)
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry.loader import load_builtin_registry
from aa_recipe_manager.registry.registry import Registry
from aa_recipe_manager.resolver.dependencies import (
    ResolvedDependencies,
    ResolvedDependency,
    resolve_dependencies,
)


def _write_recipe(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(content))
    return p


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
    p = _write_recipe(tmp_path, recipe_text)
    recipe = load_recipe(p)
    reg = load_builtin_registry()
    return build_dag(recipe, reg)


class TestResolvedDependencies:
    def test_to_requirements_txt_pypi(self):
        rd = ResolvedDependencies()
        rd.packages["echopype"] = ResolvedDependency(
            name="echopype",
            merged_specifier=">=0.9,<1.0",
            source="pypi",
            url=None,
            requiring_steps=["calibrate"],
        )
        txt = rd.to_requirements_txt()
        assert "echopype>=0.9,<1.0" in txt

    def test_to_requirements_txt_git(self):
        rd = ResolvedDependencies()
        rd.packages["my_pkg"] = ResolvedDependency(
            name="my_pkg",
            merged_specifier="",
            source="git",
            url="https://github.com/org/my_pkg.git",
            requiring_steps=["step1"],
        )
        txt = rd.to_requirements_txt()
        assert "git+https://github.com/org/my_pkg.git" in txt

    def test_to_requirements_txt_local(self):
        rd = ResolvedDependencies()
        rd.packages["my_pkg"] = ResolvedDependency(
            name="my_pkg",
            merged_specifier="",
            source="local",
            url="./my_pkg",
            requiring_steps=["step1"],
        )
        txt = rd.to_requirements_txt()
        assert "-e ./my_pkg" in txt

    def test_to_pyproject_snippet_contains_dependencies(self):
        rd = ResolvedDependencies()
        rd.packages["echopype"] = ResolvedDependency(
            name="echopype",
            merged_specifier=">=0.9",
            source="pypi",
            url=None,
            requiring_steps=["calibrate"],
        )
        snippet = rd.to_pyproject_snippet()
        assert "echopype>=0.9" in snippet
        assert "[project]" in snippet

    def test_to_pyproject_snippet_git_is_valid_requirement(self):
        from packaging.requirements import Requirement

        rd = ResolvedDependencies()
        rd.packages["my_pkg"] = ResolvedDependency(
            name="my_pkg",
            merged_specifier="",
            source="git",
            url="https://github.com/org/my_pkg.git",
            requiring_steps=["step1"],
        )
        entry = rd.to_pyproject_snippet().splitlines()[2].strip().strip(",").strip('"')
        Requirement(entry)

    def test_to_conda_env_yml_structure(self):
        rd = ResolvedDependencies()
        rd.packages["numpy"] = ResolvedDependency(
            name="numpy",
            merged_specifier=">=1.24",
            source="pypi",
            url=None,
            requiring_steps=["step1"],
        )
        yml = rd.to_conda_env_yml()
        assert "name: pipeline-env" in yml
        assert "numpy>=1.24" in yml

    def test_to_conda_env_yml_pip_section_for_git(self):
        rd = ResolvedDependencies()
        rd.packages["my_pkg"] = ResolvedDependency(
            name="my_pkg",
            merged_specifier="",
            source="git",
            url="https://github.com/org/my_pkg.git",
            requiring_steps=["step1"],
        )
        yml = rd.to_conda_env_yml()
        assert "- pip:" in yml
        assert "git+" in yml

    def test_has_conflicts_false_when_no_conflicts(self):
        rd = ResolvedDependencies()
        rd.packages["numpy"] = ResolvedDependency(
            name="numpy",
            merged_specifier=">=1.0",
            source="pypi",
            url=None,
            requiring_steps=["step1"],
        )
        assert rd.has_conflicts is False

    def test_has_conflicts_true_when_conflict(self):
        rd = ResolvedDependencies()
        rd.packages["numpy"] = ResolvedDependency(
            name="numpy",
            merged_specifier=">=1.0",
            source="pypi",
            url=None,
            requiring_steps=["step1"],
            conflict=True,
            conflict_message="incompatible",
        )
        assert rd.has_conflicts is True


class TestResolveDependencies:
    def test_four_step_returns_resolved_dependencies(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        result = resolve_dependencies(dag)
        assert isinstance(result, ResolvedDependencies)

    def test_with_implementations_has_packages(self):
        """A DAG with implementations should yield at least one package."""
        from aa_recipe_manager.model.types import (
            DAGNode,
            PipelineDAG,
            Recipe,
        )

        dep = Dependency(name="echopype", version=">=0.9", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl = Implementation(
            op="compute_sv",
            key="echopype_default",
            callable_path="echopype.calibrate.compute_Sv",
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
        result = resolve_dependencies(dag)
        assert len(result.packages) == 1
        assert "echopype" in result.packages

    def test_no_conflicts_in_four_step(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        result = resolve_dependencies(dag)
        assert not result.has_conflicts

    def test_merged_specifier_intersection(self, tmp_path):
        """Two steps requiring >=0.9 and <1.0 for the same package merge correctly."""
        from aa_recipe_manager.model.types import (
            DAGEdge,
            DAGNode,
            PipelineDAG,
            PortDeclaration,
            Recipe,
        )

        dep_a = Dependency(name="echopype", version=">=0.9", source="pypi")
        dep_b = Dependency(name="echopype", version="<1.0", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl_a = Implementation(
            op="compute_sv",
            key="impl_a",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep_a,
        )
        impl_b = Implementation(
            op="compute_sv",
            key="impl_b",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep_b,
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="step_a", op="compute_sv"),
                Step(id="step_b", op="compute_sv"),
            ],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "step_a": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl_a),
                "step_b": DAGNode(step=recipe.steps[1], spec=spec, implementation=impl_b),
            },
            edges=[],
            topological_order=["step_a", "step_b"],
        )
        result = resolve_dependencies(dag)
        echopype = result.packages["echopype"]
        assert not echopype.conflict
        assert ">=0.9" in echopype.merged_specifier
        assert "<1.0" in echopype.merged_specifier

    def test_conflict_detected_for_incompatible_ranges(self, tmp_path):
        """>=2.0 and <1.0 are incompatible."""
        from aa_recipe_manager.model.types import (
            DAGNode,
            PipelineDAG,
            Recipe,
        )

        dep_a = Dependency(name="echopype", version=">=2.0", source="pypi")
        dep_b = Dependency(name="echopype", version="<1.0", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl_a = Implementation(
            op="compute_sv",
            key="impl_a",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep_a,
        )
        impl_b = Implementation(
            op="compute_sv",
            key="impl_b",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep_b,
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="step_a", op="compute_sv"),
                Step(id="step_b", op="compute_sv"),
            ],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "step_a": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl_a),
                "step_b": DAGNode(step=recipe.steps[1], spec=spec, implementation=impl_b),
            },
            edges=[],
            topological_order=["step_a", "step_b"],
        )
        result = resolve_dependencies(dag)
        assert result.has_conflicts
        assert result.packages["echopype"].conflict

    def test_open_interval_merge_does_not_false_conflict(self):
        from aa_recipe_manager.model.types import (
            DAGNode,
            PipelineDAG,
            Recipe,
        )

        dep_a = Dependency(name="echopype", version=">1.0", source="pypi")
        dep_b = Dependency(name="echopype", version="<1.1", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl_a = Implementation(
            op="compute_sv",
            key="impl_a",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep_a,
        )
        impl_b = Implementation(
            op="compute_sv",
            key="impl_b",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep_b,
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="step_a", op="compute_sv"),
                Step(id="step_b", op="compute_sv"),
            ],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "step_a": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl_a),
                "step_b": DAGNode(step=recipe.steps[1], spec=spec, implementation=impl_b),
            },
            edges=[],
            topological_order=["step_a", "step_b"],
        )

        result = resolve_dependencies(dag)
        assert not result.has_conflicts
        assert result.packages["echopype"].merged_specifier == "<1.1,>1.0"

    def test_requiring_steps_collected(self):
        """requiring_steps should list all step IDs that need a package."""
        from aa_recipe_manager.model.types import (
            DAGNode,
            PipelineDAG,
            Recipe,
        )

        dep = Dependency(name="echopype", version=">=0.9", source="pypi")
        spec = Spec(op="compute_sv", description="test")
        impl = Implementation(
            op="compute_sv",
            key="echopype_default",
            callable_path="echopype.calibrate.compute_Sv",
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
        result = resolve_dependencies(dag)
        assert "step1" in result.packages["echopype"].requiring_steps

    def test_not_equal_exclusion_does_not_false_conflict(self):
        """>=1.2.0,<1.3.0,!=1.2.9 is satisfiable (e.g. 1.2.8 matches)."""
        from aa_recipe_manager.model.types import (
            DAGNode,
            PipelineDAG,
            Recipe,
        )

        dep_a = Dependency(name="pkg", version=">=1.2.0", source="pypi")
        dep_b = Dependency(name="pkg", version="<1.3.0", source="pypi")
        dep_c = Dependency(name="pkg", version="!=1.2.9", source="pypi")
        spec = Spec(op="op", description="test")
        impl_a = Implementation(op="op", key="a", callable_path="pkg.f", dependency=dep_a)
        impl_b = Implementation(op="op", key="b", callable_path="pkg.f", dependency=dep_b)
        impl_c = Implementation(op="op", key="c", callable_path="pkg.f", dependency=dep_c)
        recipe = Recipe(
            name="test", version="1.0", schema_version="1",
            steps=[
                Step(id="step_a", op="op"),
                Step(id="step_b", op="op"),
                Step(id="step_c", op="op"),
            ],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "step_a": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl_a),
                "step_b": DAGNode(step=recipe.steps[1], spec=spec, implementation=impl_b),
                "step_c": DAGNode(step=recipe.steps[2], spec=spec, implementation=impl_c),
            },
            edges=[],
            topological_order=["step_a", "step_b", "step_c"],
        )
        result = resolve_dependencies(dag)
        assert not result.has_conflicts

    def test_git_url_conflict_detected(self):
        """Same package from two different git URLs should be a conflict."""
        dep_a = Dependency(
            name="my_pkg", version="", source="git",
            url="https://github.com/org/my_pkg.git",
        )
        dep_b = Dependency(
            name="my_pkg", version="", source="git",
            url="https://github.com/org/my_pkg_fork.git",
        )
        rd = ResolvedDependencies()
        rd.packages["my_pkg"] = ResolvedDependency(
            name="my_pkg",
            merged_specifier="",
            source="git",
            url="https://github.com/org/my_pkg.git",
            requiring_steps=["step_a"],
        )
        from aa_recipe_manager.resolver.dependencies import _merge_dependency

        _merge_dependency(rd, dep_b, "step_b")
        assert rd.packages["my_pkg"].conflict is True
        assert "fork" in (rd.packages["my_pkg"].conflict_message or "")

    def test_git_same_url_no_conflict(self):
        """Same package from the same git URL on two steps is not a conflict."""
        dep = Dependency(
            name="my_pkg", version="", source="git",
            url="https://github.com/org/my_pkg.git",
        )
        rd = ResolvedDependencies()
        rd.packages["my_pkg"] = ResolvedDependency(
            name="my_pkg",
            merged_specifier="",
            source="git",
            url="https://github.com/org/my_pkg.git",
            requiring_steps=["step_a"],
        )
        from aa_recipe_manager.resolver.dependencies import _merge_dependency

        _merge_dependency(rd, dep, "step_b")
        assert not rd.packages["my_pkg"].conflict
