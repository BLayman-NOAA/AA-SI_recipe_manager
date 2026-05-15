# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the code generator and notebook backend."""

from __future__ import annotations

import ast
import json
import textwrap
from pathlib import Path

import nbformat
import pytest

import aa_recipe_manager
import aa_recipe_manager.api as public_api
from aa_recipe_manager.generator.backends.notebook import NotebookBackend, _build_variable_name_map_from_dag
from aa_recipe_manager.generator.backends.script import ScriptBackend
from aa_recipe_manager.generator.core import CodeGenerator, build_variable_name_map
from aa_recipe_manager.model.types import (
    DAGEdge,
    DAGNode,
    Dependency,
    InputDeclaration,
    Implementation,
    PipelineDAG,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry.loader import load_builtin_registry
from aa_recipe_manager.registry.registry import Registry
from aa_recipe_manager.resolver.dependencies import resolve_dependencies


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


def _write_recipe(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "recipe.yaml"
    p.write_text(textwrap.dedent(content))
    return p


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


def _generate_notebook(dag: PipelineDAG, tmp_path: Path) -> Path:
    out = tmp_path / "pipeline.ipynb"
    resolved_deps = resolve_dependencies(dag)
    backend = NotebookBackend()
    return backend.generate(dag, resolved_deps, out)


def _read_notebook(path: Path) -> nbformat.NotebookNode:
    with open(path) as fh:
        return nbformat.read(fh, as_version=4)


def _combined_code_sources(path: Path) -> str:
    nb = _read_notebook(path)
    return "\n".join(c.source for c in nb.cells if c.cell_type == "code")


class TestVariableNameMap:
    def test_unique_output_uses_output_name(self):
        """Steps with unique output names use just the output name."""
        spec_a = Spec(
            op="op_a",
            description="a",
            outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        )
        spec_b = Spec(
            op="op_b",
            description="b",
            outputs={"ds_MVBS": PortDeclaration(type="Dataset")},
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step_a", op="op_a"), Step(id="step_b", op="op_b")],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "step_a": DAGNode(step=recipe.steps[0], spec=spec_a),
                "step_b": DAGNode(step=recipe.steps[1], spec=spec_b),
            },
            edges=[],
            topological_order=["step_a", "step_b"],
        )
        name_map = build_variable_name_map(dag)
        assert name_map[("step_a", "ds_Sv")] == "ds_Sv"
        assert name_map[("step_b", "ds_MVBS")] == "ds_MVBS"

    def test_collision_uses_step_id_prefix(self):
        """Two steps with the same output name get step_id_ prefix."""
        spec = Spec(
            op="compute_sv",
            description="sv",
            outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="compute_baseline_sv", op="compute_sv"),
                Step(id="compute_calibrated_sv", op="compute_sv"),
            ],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "compute_baseline_sv": DAGNode(step=recipe.steps[0], spec=spec),
                "compute_calibrated_sv": DAGNode(step=recipe.steps[1], spec=spec),
            },
            edges=[],
            topological_order=["compute_baseline_sv", "compute_calibrated_sv"],
        )
        name_map = build_variable_name_map(dag)
        assert name_map[("compute_baseline_sv", "ds_Sv")] == "compute_baseline_sv_ds_Sv"
        assert name_map[("compute_calibrated_sv", "ds_Sv")] == "compute_calibrated_sv_ds_Sv"


class TestIncludedRecipeNotebookRendering:
    def test_included_steps_are_bracketed_and_annotated(self, tmp_path):
        child = tmp_path / "child.yaml"
        child.write_text(textwrap.dedent("""\
            recipe:
              name: child
              version: "1.0"
              schema_version: "1"
            steps:
              - id: preprocess
                op: op_a
            """))
        parent = _write_recipe(tmp_path, """\
            recipe:
              name: parent
              version: "1.0"
              schema_version: "1"
            steps:
              - include: child.yaml
              - id: analyze
                op: op_b
                inputs:
                  x: ${preprocess.y}
            """)
        registry = Registry()
        registry.register_spec(
            Spec(
                op="op_a",
                description="Step A",
                outputs={"y": PortDeclaration(type="Dataset")},
            )
        )
        registry.register_spec(
            Spec(
                op="op_b",
                description="Step B",
                inputs={"x": PortDeclaration(type="Dataset")},
            )
        )

        dag = build_dag(load_recipe(parent), registry)
        notebook_path = _generate_notebook(dag, tmp_path)
        markdown_sources = [
            cell.source
            for cell in _read_notebook(notebook_path).cells
            if cell.cell_type == "markdown"
        ]

        assert any("## Included: child.yaml" in source for source in markdown_sources)
        assert any("Steps: preprocess" in source for source in markdown_sources)
        assert any(
            "### Step: `preprocess` (from child.yaml)" in source
            for source in markdown_sources
        )
        assert any(
            "*End of included section: child.yaml*" in source
            for source in markdown_sources
        )


class TestNotebookGeneration:
    def test_generates_valid_ipynb_json(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        assert out.exists()
        with open(out) as fh:
            parsed = json.load(fh)
        assert "cells" in parsed
        assert "nbformat" in parsed

    def test_notebook_has_cells(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        assert len(nb.cells) > 0

    def test_first_cell_is_markdown_with_recipe_name(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        first = nb.cells[0]
        assert first.cell_type == "markdown"
        assert "simple_ek60_pipeline" in first.source

    def test_contains_imports_cell(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        code_sources = [c.source for c in nb.cells if c.cell_type == "code"]
        combined = "\n".join(code_sources)
        assert "PipelineTracker" in combined
        assert "ProvenanceRecorder" in combined

    def test_contains_tracker_init_cell(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        code_sources = [c.source for c in nb.cells if c.cell_type == "code"]
        combined = "\n".join(code_sources)
        assert "PipelineTracker" in combined

    def test_include_tracker_false_omits_tracker_cells_and_wrappers(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = tmp_path / "no_tracker.ipynb"
        NotebookBackend().generate(
            dag,
            resolve_dependencies(dag),
            out,
            options={"include_tracker": False},
        )
        code_sources = _combined_code_sources(out)
        assert "PipelineTracker" not in code_sources
        assert "tracker =" not in code_sources
        assert "tracker.step(" not in code_sources
        assert "save_recipe" not in code_sources
        assert "query_ncei_data(" in code_sources

    def test_include_tracker_false_with_cache_aware_omits_tracker_wrappers(
        self,
        tmp_path,
    ):
        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(
            op="compute_sv",
            description="test",
            outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        )
        impl = Implementation(
            op="compute_sv",
            key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
            output_map={"ds_Sv": "__return__"},
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="compute_sv", params={"version": "1.0"})],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "step1": DAGNode(
                    step=recipe.steps[0],
                    spec=spec,
                    implementation=impl,
                )
            },
            edges=[],
            topological_order=["step1"],
        )
        out = tmp_path / "cache_aware_no_tracker.ipynb"
        NotebookBackend().generate(
            dag,
            resolve_dependencies(dag),
            out,
            options={"cache_aware": True, "include_tracker": False},
        )
        code_sources = _combined_code_sources(out)
        assert '_recipe_manager_cache_dir = "outputs"' in code_sources
        assert "with tracker.step(" not in code_sources
        assert "ds_Sv = Version(version='1.0')" in code_sources

    def test_contains_inputs_cell(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        code_sources = [c.source for c in nb.cells if c.cell_type == "code"]
        combined = "\n".join(code_sources)
        assert "raw_input_folder" in combined
        assert "netcdf_output_folder" in combined

    def test_all_steps_appear_in_notebook(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        all_sources = "\n".join(c.source for c in nb.cells)
        for step_id in ["query_ncei", "download_raw", "setup_files", "open_raw"]:
            assert step_id in all_sources

    def test_save_recipe_cell_present(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        code_sources = [c.source for c in nb.cells if c.cell_type == "code"]
        combined = "\n".join(code_sources)
        assert "save_recipe" in combined

    def test_provenance_cell_present(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        code_sources = [c.source for c in nb.cells if c.cell_type == "code"]
        combined = "\n".join(code_sources)
        assert "ProvenanceRecorder" in combined

    def test_public_api_can_omit_tracker(self, tmp_path):
        raw_dir = tmp_path / "raw_files"
        raw_dir.mkdir()
        recipe_text = FOUR_STEP_RECIPE.replace(
            "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
        ).replace(
            "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
        )
        recipe_path = _write_recipe(tmp_path, recipe_text)
        out = tmp_path / "api_no_tracker.ipynb"
        public_api.generate(recipe_path, output=out, include_tracker=False)
        assert "PipelineTracker" not in _combined_code_sources(out)

    def test_all_code_cells_are_syntactically_valid(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        for i, cell in enumerate(nb.cells):
            if cell.cell_type == "code":
                try:
                    ast.parse(cell.source)
                except SyntaxError as exc:
                    pytest.fail(
                        f"Code cell {i} has invalid syntax:\n{cell.source}\n\nError: {exc}"
                    )

    def test_tracker_step_calls_present_when_impl_exists(self, tmp_path):
        """Steps with implementations emit tracker.step() context manager calls."""
        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(
            op="compute_sv",
            description="test",
            outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        )
        impl = Implementation(
            op="compute_sv", key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
            output_map={"ds_Sv": "__return__"},
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
        out = tmp_path / "impl_test.ipynb"
        resolved_deps = resolve_dependencies(dag)
        backend = NotebookBackend()
        backend.generate(dag, resolved_deps, out)
        nb = _read_notebook(out)
        code_sources = [c.source for c in nb.cells if c.cell_type == "code"]
        step_cells = [s for s in code_sources if "tracker.step(" in s]
        assert len(step_cells) == 1

    def test_cache_aware_generation_wraps_implemented_steps(self, tmp_path):
        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(
            op="compute_sv",
            description="test",
            outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        )
        impl = Implementation(
            op="compute_sv",
            key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
            output_map={"ds_Sv": "__return__"},
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="compute_sv", params={"version": "1.0"})],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={"step1": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl)},
            edges=[],
            topological_order=["step1"],
        )
        out = tmp_path / "cache_aware.ipynb"
        resolved_deps = resolve_dependencies(dag)
        backend = NotebookBackend()
        backend.generate(dag, resolved_deps, out, options={"cache_aware": True})
        nb = _read_notebook(out)
        code_sources = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
        assert '_recipe_manager_cache_dir = "outputs"' in code_sources
        assert '_cache_dir = _Path(_recipe_manager_cache_dir)' in code_sources
        assert '_cache_meta_path = _cache_dir / ' in code_sources
        assert 'step1_ds_Sv.pkl' in code_sources
        assert '_step_signature = _hashlib.sha256(' in code_sources
        assert code_sources.count("with tracker.step(") == 2

    def test_cache_aware_generation_does_not_override_output_dir_input(self, tmp_path):
        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(
            op="compute_sv",
            description="test",
            outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        )
        impl = Implementation(
            op="compute_sv",
            key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
            output_map={"ds_Sv": "__return__"},
        )
        recipe = Recipe(
            name="test",
            version="1.0",
            schema_version="1",
            inputs={
                "output_dir": InputDeclaration(type="path", default="/tmp/user-output")
            },
            steps=[Step(id="step1", op="compute_sv", params={"version": "1.0"})],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={"step1": DAGNode(step=recipe.steps[0], spec=spec, implementation=impl)},
            edges=[],
            topological_order=["step1"],
        )
        out = tmp_path / "cache_input_collision.ipynb"
        NotebookBackend().generate(
            dag,
            resolve_dependencies(dag),
            out,
            options={"cache_aware": True},
        )
        nb = _read_notebook(out)
        code_sources = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
        assert "output_dir = '/tmp/user-output'" in code_sources
        assert 'output_dir = "outputs"' not in code_sources
        assert '_recipe_manager_cache_dir = "outputs"' in code_sources

    def test_cache_aware_script_invalidates_stale_cache_and_tracks_cache_hits(
        self,
        tmp_path,
        monkeypatch,
    ):
        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(
            op="compute_sv",
            description="test",
            outputs={"version_obj": PortDeclaration(type="str")},
        )
        impl = Implementation(
            op="compute_sv",
            key="key",
            callable_path="packaging.version.Version",
            dependency=dep,
            output_map={"version_obj": "__return__"},
        )
        backend = ScriptBackend()

        recipe_v1 = Recipe(
            name="cache_test",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="compute_sv", params={"version": "1.0"})],
        )
        dag_v1 = PipelineDAG(
            recipe=recipe_v1,
            nodes={
                "step1": DAGNode(
                    step=recipe_v1.steps[0],
                    spec=spec,
                    implementation=impl,
                    resolved_params={"version": "1.0"},
                )
            },
            edges=[],
            topological_order=["step1"],
        )
        script_v1 = tmp_path / "run_v1.py"
        backend.generate(
            dag_v1,
            resolve_dependencies(dag_v1),
            script_v1,
            options={"cache_aware": True, "include_provenance": False},
        )

        monkeypatch.chdir(tmp_path)

        ns_v1 = {"__name__": "__main__"}
        exec(compile(script_v1.read_text(encoding="utf-8"), str(script_v1), "exec"), ns_v1)
        assert str(ns_v1["version_obj"]) == "1.0"

        ns_v1_cached = {"__name__": "__main__"}
        exec(
            compile(script_v1.read_text(encoding="utf-8"), str(script_v1), "exec"),
            ns_v1_cached,
        )
        assert str(ns_v1_cached["version_obj"]) == "1.0"
        saved_recipe = (tmp_path / "pipeline_modified.yaml").read_text(encoding="utf-8")
        assert "- id: step1" in saved_recipe

        recipe_v2 = Recipe(
            name="cache_test",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="compute_sv", params={"version": "2.0"})],
        )
        dag_v2 = PipelineDAG(
            recipe=recipe_v2,
            nodes={
                "step1": DAGNode(
                    step=recipe_v2.steps[0],
                    spec=spec,
                    implementation=impl,
                    resolved_params={"version": "2.0"},
                )
            },
            edges=[],
            topological_order=["step1"],
        )
        script_v2 = tmp_path / "run_v2.py"
        backend.generate(
            dag_v2,
            resolve_dependencies(dag_v2),
            script_v2,
            options={"cache_aware": True, "include_provenance": False},
        )

        ns_v2 = {"__name__": "__main__"}
        exec(compile(script_v2.read_text(encoding="utf-8"), str(script_v2), "exec"), ns_v2)
        assert str(ns_v2["version_obj"]) == "2.0"

    def test_todo_cell_when_no_implementation(self, tmp_path):
        """Steps without implementations emit a # TODO comment cell."""
        spec = Spec(op="compute_sv", description="test")
        recipe = Recipe(
            name="test", version="1.0", schema_version="1",
            steps=[Step(id="step1", op="compute_sv")],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={"step1": DAGNode(step=recipe.steps[0], spec=spec)},
            edges=[],
            topological_order=["step1"],
        )
        out = tmp_path / "todo_test.ipynb"
        resolved_deps = resolve_dependencies(dag)
        backend = NotebookBackend()
        backend.generate(dag, resolved_deps, out)
        nb = _read_notebook(out)
        code_sources = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
        assert "# TODO: no implementation found" in code_sources

    def test_step_markdown_cells_present(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = _generate_notebook(dag, tmp_path)
        nb = _read_notebook(out)
        md_sources = [c.source for c in nb.cells if c.cell_type == "markdown"]
        combined = "\n".join(md_sources)
        assert "query_ncei" in combined


class TestVariableNameCollision:
    def test_collision_variables_in_generated_code(self, tmp_path):
        """Two steps that both produce ds_Sv should get prefixed variable names."""
        dep = Dependency(name="echopype", version=">=0.9", source="pypi")
        spec = Spec(
            op="compute_sv",
            description="Compute Sv",
            outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        )
        impl_a = Implementation(
            op="compute_sv",
            key="impl_a",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep,
            output_map={"ds_Sv": "__return__"},
        )
        impl_b = Implementation(
            op="compute_sv",
            key="impl_b",
            callable_path="echopype.calibrate.compute_Sv",
            dependency=dep,
            output_map={"ds_Sv": "__return__"},
        )
        recipe = Recipe(
            name="collision_test",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="compute_baseline_sv", op="compute_sv"),
                Step(id="compute_calibrated_sv", op="compute_sv"),
            ],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "compute_baseline_sv": DAGNode(
                    step=recipe.steps[0],
                    spec=spec,
                    implementation=impl_a,
                ),
                "compute_calibrated_sv": DAGNode(
                    step=recipe.steps[1],
                    spec=spec,
                    implementation=impl_b,
                ),
            },
            edges=[],
            topological_order=["compute_baseline_sv", "compute_calibrated_sv"],
        )
        out = tmp_path / "collision.ipynb"
        resolved_deps = resolve_dependencies(dag)
        backend = NotebookBackend()
        backend.generate(dag, resolved_deps, out)
        nb = _read_notebook(out)
        code_sources = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
        assert "compute_baseline_sv_ds_Sv" in code_sources
        assert "compute_calibrated_sv_ds_Sv" in code_sources

    def test_callable_name_collision_uses_import_aliases(self, tmp_path):
        dep = Dependency(name="pytest", version=">=7.0", source="pypi")
        spec_a = Spec(op="op_a", description="a")
        spec_b = Spec(op="op_b", description="b")
        impl_a = Implementation(
            op="op_a",
            key="impl_a",
            callable_path="pkg_a.run",
            dependency=dep,
        )
        impl_b = Implementation(
            op="op_b",
            key="impl_b",
            callable_path="pkg_b.run",
            dependency=dep,
        )
        recipe = Recipe(
            name="callable_collision_test",
            version="1.0",
            schema_version="1",
            steps=[
                Step(id="step_a", op="op_a"),
                Step(id="step_b", op="op_b"),
            ],
        )
        dag = PipelineDAG(
            recipe=recipe,
            nodes={
                "step_a": DAGNode(step=recipe.steps[0], spec=spec_a, implementation=impl_a),
                "step_b": DAGNode(step=recipe.steps[1], spec=spec_b, implementation=impl_b),
            },
            edges=[],
            topological_order=["step_a", "step_b"],
        )

        out = tmp_path / "callable_collision.ipynb"
        NotebookBackend().generate(dag, resolve_dependencies(dag), out)
        nb = _read_notebook(out)
        code_sources = "\n".join(c.source for c in nb.cells if c.cell_type == "code")
        assert "from pkg_a import run as pkg_a_run" in code_sources
        assert "from pkg_b import run as pkg_b_run" in code_sources
        assert "pkg_a_run()" in code_sources
        assert "pkg_b_run()" in code_sources


class TestPublicApiGenerate:
    def test_generate_returns_path(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        out = tmp_path / "via_api.ipynb"
        result = aa_recipe_manager.generate(dag, out)
        assert result == out
        assert out.exists()

    def test_generate_unknown_backend_raises(self, tmp_path):
        dag = _build_four_step_dag(tmp_path)
        with pytest.raises(ValueError, match="Unknown backend"):
            aa_recipe_manager.generate(dag, tmp_path / "out.txt", backend="nonexistent")

    def test_api_generate_defaults_output_next_to_recipe(self, tmp_path):
        recipe_path = _write_recipe(
            tmp_path,
            FOUR_STEP_RECIPE.replace(
                "__RAW_INPUT_FOLDER__", (tmp_path / "raw_files").as_posix()
            ).replace(
                "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
            ),
        )
        (tmp_path / "raw_files").mkdir(exist_ok=True)

        result = public_api.generate(recipe_path)

        assert result == recipe_path.with_name("simple_ek60_pipeline.ipynb")
        assert result.exists()

    def test_api_generate_implementation_override_is_applied(self, tmp_path, monkeypatch):
        dep = Dependency(name="packaging", version=">=21.0", source="pypi")
        spec = Spec(
            op="op_a",
            description="test",
            outputs={"out": PortDeclaration(type="str")},
        )
        impl_a = Implementation(
            op="op_a",
            key="impl_a",
            callable_path="pkg_a.run",
            dependency=dep,
            default=True,
            output_map={"out": "__return__"},
        )
        impl_b = Implementation(
            op="op_a",
            key="impl_b",
            callable_path="pkg_b.run",
            dependency=dep,
            output_map={"out": "__return__"},
        )
        recipe = Recipe(
            name="override_test",
            version="1.0",
            schema_version="1",
            steps=[Step(id="step1", op="op_a")],
        )
        reg = Registry()
        reg.register_spec(spec)
        reg.register_implementation(impl_a)
        reg.register_implementation(impl_b)

        monkeypatch.setattr(
            "aa_recipe_manager.registry.loader.load_builtin_registry",
            lambda: reg,
        )

        out = tmp_path / "override.py"
        result = public_api.generate(
            recipe,
            output=out,
            output_format="script",
            implementation_override="impl_b",
        )

        assert result == out
        content = out.read_text(encoding="utf-8")
        assert "from pkg_b import run" in content
        assert "from pkg_a import run" not in content

    def test_api_generate_script_output_is_valid_python(self, tmp_path):
        raw_dir = tmp_path / "raw_files"
        raw_dir.mkdir()
        recipe_path = _write_recipe(
            tmp_path,
            FOUR_STEP_RECIPE.replace(
                "__RAW_INPUT_FOLDER__", raw_dir.as_posix()
            ).replace(
                "__NETCDF_OUTPUT_FOLDER__", (tmp_path / "netcdf").as_posix()
            ),
        )
        out = tmp_path / "pipeline.py"

        result = public_api.generate(recipe_path, output=out, output_format="script")

        assert result == out
        ast.parse(out.read_text(encoding="utf-8"))
