# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for all Pydantic data models in model/types.py."""

import pytest
from pydantic import ValidationError

from aa_recipe_manager.model.types import (
    CustomSpec,
    DAGEdge,
    DAGNode,
    Dependency,
    ExecutionHints,
    Implementation,
    InputDeclaration,
    OutputDeclaration,
    ParamDeclaration,
    PipelineDAG,
    PortDeclaration,
    Provenance,
    Recipe,
    ResolvedStepInfo,
    Spec,
    Step,
    StepExecutionHints,
    SweepDeclaration,
)
from conftest import (
    make_dependency,
    make_implementation,
    make_recipe,
    make_spec,
    make_step,
)

# ---------------------------------------------------------------------------
# PortDeclaration
# ---------------------------------------------------------------------------


def test_port_declaration_minimal():
    port = PortDeclaration(type="Dataset")
    assert port.type == "Dataset"
    assert port.description is None
    assert port.expected_variables is None
    assert port.expected_coords is None


def test_port_declaration_full():
    port = PortDeclaration(
        type="Dataset",
        description="Calibrated Sv",
        expected_variables=["Sv"],
        expected_coords=["ping_time", "range_sample"],
    )
    assert port.expected_variables == ["Sv"]


def test_port_declaration_requires_type():
    with pytest.raises(ValidationError):
        PortDeclaration()


# ---------------------------------------------------------------------------
# ParamDeclaration
# ---------------------------------------------------------------------------


def test_param_declaration_defaults():
    param = ParamDeclaration()
    assert param.type is None
    assert param.required is True
    assert param.default is None
    assert param.units is None


def test_param_declaration_full():
    param = ParamDeclaration(
        type="float",
        units="dB",
        description="Noise floor threshold",
        default=-999.0,
        required=False,
        constraints={"min": -999, "max": 0},
    )
    assert param.type == "float"
    assert param.constraints == {"min": -999, "max": 0}


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------


def test_dependency_required_fields():
    dep = make_dependency()
    assert dep.name == "pytest"
    assert dep.source == "pypi"
    assert dep.url is None


def test_dependency_git_source():
    dep = Dependency(
        name="my_pkg",
        version=">=1.0",
        source="git",
        url="https://github.com/my-org/my-pkg",
    )
    assert dep.url == "https://github.com/my-org/my-pkg"


def test_dependency_invalid_source():
    with pytest.raises(ValidationError):
        Dependency(name="pkg", version=">=1.0", source="npm")


def test_dependency_missing_name():
    with pytest.raises(ValidationError):
        Dependency(version=">=1.0", source="pypi")


# ---------------------------------------------------------------------------
# CustomSpec
# ---------------------------------------------------------------------------


def test_custom_spec_minimal():
    spec = CustomSpec(description="My custom step", callable_path="my.module.func")
    assert spec.extends is None
    assert spec.inputs is None
    assert spec.param_map is None


def test_custom_spec_with_extends():
    spec = CustomSpec(
        extends="compute_sv",
        description="Custom Sv step",
        callable_path="my.module.func",
        params={"extra_param": ParamDeclaration(type="float")},
    )
    assert spec.extends == "compute_sv"
    assert "extra_param" in spec.params


def test_custom_spec_missing_callable():
    with pytest.raises(ValidationError):
        CustomSpec(description="missing callable")


# ---------------------------------------------------------------------------
# SweepDeclaration
# ---------------------------------------------------------------------------


def test_sweep_declaration_defaults():
    sweep = SweepDeclaration(param_lists={"threshold": [-70.0, -80.0, -90.0]})
    assert sweep.mode == "zip"


def test_sweep_declaration_grid_mode():
    sweep = SweepDeclaration(
        param_lists={"a": [1, 2], "b": [10, 20]},
        mode="grid",
    )
    assert sweep.mode == "grid"


def test_sweep_declaration_invalid_mode():
    with pytest.raises(ValidationError):
        SweepDeclaration(param_lists={"a": [1, 2]}, mode="foobar")


# ---------------------------------------------------------------------------
# StepExecutionHints and ExecutionHints
# ---------------------------------------------------------------------------


def test_step_execution_hints_all_optional():
    hints = StepExecutionHints()
    assert hints.dask_config is None
    assert hints.prefect_config is None


def test_execution_hints_defaults():
    hints = ExecutionHints()
    assert hints.parallel_branches is False
    assert hints.executor is None
    assert hints.split_after is None


def test_execution_hints_full():
    hints = ExecutionHints(
        executor="dask",
        parallel_branches=True,
        dask_config={"n_workers": 4},
    )
    assert hints.executor == "dask"
    assert hints.dask_config == {"n_workers": 4}


# ---------------------------------------------------------------------------
# Step
# ---------------------------------------------------------------------------


def test_step_minimal():
    step = make_step()
    assert step.id == "compute_sv"
    assert step.op == "compute_sv"
    assert step.inputs == {}
    assert step.params == {}
    assert step.depends_on is None


def test_step_full():
    step = Step(
        id="calibrate",
        op="compute_sv",
        description="Calibrate Sv",
        inputs={"echodata": "${open_raw.echodata}"},
        params={"cal_params": None},
        depends_on=["open_raw"],
        implementation_override="echopype_default",
        map_over="${open_raw.echodata_list}",
    )
    assert step.inputs["echodata"] == "${open_raw.echodata}"
    assert step.depends_on == ["open_raw"]


def test_step_requires_id_and_op():
    with pytest.raises(ValidationError):
        Step(op="compute_sv")
    with pytest.raises(ValidationError):
        Step(id="step1")


# ---------------------------------------------------------------------------
# InputDeclaration
# ---------------------------------------------------------------------------


def test_input_required_by_default():
    inp = InputDeclaration(type="path")
    assert inp.required is True


def test_input_default_sets_required_false():
    inp = InputDeclaration(type="path", default="./raw_files")
    assert inp.required is False


def test_input_explicit_required_false_no_default():
    inp = InputDeclaration(type="str", required=False)
    assert inp.required is False


def test_input_required_true_with_default_raises():
    with pytest.raises(ValidationError, match="'required' cannot be True"):
        InputDeclaration(type="str", required=True, default="fallback")


def test_input_requires_type():
    with pytest.raises(ValidationError):
        InputDeclaration()


# ---------------------------------------------------------------------------
# OutputDeclaration
# ---------------------------------------------------------------------------


def test_output_declaration():
    out = OutputDeclaration(step_id="compute_sv", output_name="ds_Sv")
    assert out.step_id == "compute_sv"
    assert out.save_to is None


def test_output_declaration_with_path():
    out = OutputDeclaration(
        step_id="compute_sv",
        output_name="ds_Sv",
        save_to="./output/sv.nc",
    )
    assert out.save_to is not None


def test_output_declaration_missing_fields():
    with pytest.raises(ValidationError):
        OutputDeclaration(step_id="compute_sv")


# ---------------------------------------------------------------------------
# Recipe
# ---------------------------------------------------------------------------


def test_recipe_minimal():
    recipe = make_recipe()
    assert recipe.name == "test_pipeline"
    assert recipe.schema_version == "1"
    assert len(recipe.steps) == 1


def test_recipe_full():
    recipe = Recipe(
        name="hb1603",
        version="1.0.0",
        description="Full survey pipeline",
        author="NOAA",
        schema_version="1",
        inputs={
            "raw_folder": InputDeclaration(type="path"),
            "output_dir": InputDeclaration(type="path", default="./output"),
        },
        steps=[
            Step(id="open_raw", op="open_raw_files"),
            Step(
                id="calibrate",
                op="compute_sv",
                inputs={"echodata": "${open_raw.echodata}"},
            ),
        ],
        outputs={
            "sv_dataset": OutputDeclaration(step_id="calibrate", output_name="ds_Sv")
        },
        execution=ExecutionHints(executor="sequential"),
    )
    assert len(recipe.steps) == 2
    assert "raw_folder" in recipe.inputs
    assert recipe.inputs["output_dir"].required is False


def test_recipe_unsupported_schema_version():
    with pytest.raises(ValidationError) as exc_info:
        make_recipe(schema_version="99")
    assert "99" in str(exc_info.value)
    assert "Unsupported schema_version" in str(exc_info.value)


def test_recipe_missing_required_fields():
    with pytest.raises(ValidationError):
        Recipe(version="1.0.0", steps=[make_step()], schema_version="1")  # name missing
    with pytest.raises(ValidationError):
        Recipe(name="test", version="1.0.0", schema_version="1")  # steps missing


def test_recipe_empty_steps_rejected():
    with pytest.raises(ValidationError):
        Recipe(name="test", version="1.0.0", schema_version="1", steps=[])


def test_recipe_round_trip():
    recipe = make_recipe()
    data = recipe.model_dump()
    reconstructed = Recipe.model_validate(data)
    assert reconstructed.name == recipe.name
    assert reconstructed.schema_version == recipe.schema_version


# ---------------------------------------------------------------------------
# Spec and Implementation
# ---------------------------------------------------------------------------


def test_spec_minimal():
    spec = make_spec()
    assert spec.op == "compute_sv"
    assert spec.inputs == {}
    assert spec.category is None


def test_spec_with_ports():
    spec = Spec(
        op="compute_sv",
        description="Compute Sv",
        category="calibration",
        inputs={"echodata": PortDeclaration(type="EchoData")},
        outputs={"ds_Sv": PortDeclaration(type="Dataset")},
        params={"cal_params": ParamDeclaration(type="dict", required=False)},
    )
    assert "echodata" in spec.inputs
    assert spec.category == "calibration"


def test_implementation_minimal():
    impl = make_implementation()
    assert impl.key == "echopype_default"
    assert impl.default is False
    assert impl.param_map == {}


def test_implementation_full():
    impl = Implementation(
        op="compute_sv",
        key="echopype_default",
        callable_path="echopype.calibrate.compute_Sv",
        dependency=make_dependency(),
        param_map={"echodata": "echodata", "cal_params": "cal_params"},
        output_map={"ds_Sv": "__return__"},
        default=True,
        tested_versions=["0.9.0", "0.9.1"],
    )
    assert impl.default is True
    assert impl.output_map == {"ds_Sv": "__return__"}


def test_implementation_missing_fields():
    with pytest.raises(ValidationError):
        Implementation(op="compute_sv", key="k", callable_path="a.b")  # no dependency


# ---------------------------------------------------------------------------
# DAG models
# ---------------------------------------------------------------------------


def test_dag_edge():
    edge = DAGEdge(
        source_step_id="open_raw",
        source_output="echodata",
        target_step_id="calibrate",
        target_input="echodata",
    )
    assert edge.source_step_id == "open_raw"


def test_dag_node():
    node = DAGNode(
        step=make_step(),
        spec=make_spec(),
        implementation=make_implementation(),
    )
    assert node.is_mapped is False
    assert node.resolved_params == {}


def test_pipeline_dag():
    recipe = make_recipe()
    dag = PipelineDAG(recipe=recipe)
    assert dag.nodes == {}
    assert dag.edges == []
    assert dag.topological_order == []


def test_pipeline_dag_round_trip():
    recipe = make_recipe()
    node = DAGNode(
        step=make_step(),
        spec=make_spec(),
        implementation=make_implementation(),
        resolved_params={"threshold": -70.0},
    )
    edge = DAGEdge(
        source_step_id="open_raw",
        source_output="echodata",
        target_step_id="compute_sv",
        target_input="echodata",
    )
    dag = PipelineDAG(
        recipe=recipe,
        nodes={"compute_sv": node},
        edges=[edge],
        topological_order=["open_raw", "compute_sv"],
    )
    data = dag.model_dump()
    reconstructed = PipelineDAG.model_validate(data)
    assert reconstructed.topological_order == ["open_raw", "compute_sv"]


# ---------------------------------------------------------------------------
# Provenance models
# ---------------------------------------------------------------------------


def test_resolved_step_info():
    info = ResolvedStepInfo(
        op="compute_sv",
        implementation_key="echopype_default",
        callable_path="echopype.calibrate.compute_Sv",
        package_name="echopype",
        installed_version="0.9.1",
        params_used={"threshold": -70.0},
    )
    assert info.installed_version == "0.9.1"


def test_provenance():
    from datetime import datetime, timezone

    prov = Provenance(
        recipe_hash="abc123",
        recipe_name="hb1603",
        recipe_version="1.0.0",
        timestamp=datetime(2026, 4, 17, tzinfo=timezone.utc),
        python_version="3.12.3",
        os_info="Windows-10",
        resolved_steps={
            "compute_sv": ResolvedStepInfo(
                op="compute_sv",
                implementation_key="echopype_default",
                callable_path="echopype.calibrate.compute_Sv",
                package_name="echopype",
                installed_version="0.9.1",
            )
        },
        resolved_dependencies={"echopype": "0.9.1"},
    )
    assert prov.resolved_dependencies["echopype"] == "0.9.1"
    assert "compute_sv" in prov.resolved_steps


# ---------------------------------------------------------------------------
# JSON Schema export (Stage 1c)
# ---------------------------------------------------------------------------


def test_export_schema_returns_dict():
    import aa_recipe_manager

    schema = aa_recipe_manager.export_schema()
    assert isinstance(schema, dict)
    assert "properties" in schema or "$defs" in schema


def test_export_schema_is_valid_json_schema():
    import jsonschema
    import jsonschema.validators

    import aa_recipe_manager

    schema = aa_recipe_manager.export_schema()
    # Validate that the schema itself is a valid JSON Schema document.
    validator_cls = jsonschema.validators.validator_for(schema)
    validator_cls.check_schema(schema)


def test_schema_validates_valid_recipe():
    import jsonschema

    import aa_recipe_manager

    schema = aa_recipe_manager.export_schema()
    valid_recipe = {
        "name": "test",
        "version": "1.0.0",
        "schema_version": "1",
        "steps": [{"id": "step1", "op": "compute_sv"}],
    }
    # Should not raise.
    jsonschema.validate(instance=valid_recipe, schema=schema)


def test_schema_rejects_missing_required_fields():
    import jsonschema

    import aa_recipe_manager

    schema = aa_recipe_manager.export_schema()
    invalid_recipe = {"version": "1.0.0", "schema_version": "1", "steps": []}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=invalid_recipe, schema=schema)
