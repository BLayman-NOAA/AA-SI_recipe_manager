# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the Stage 6 direct pipeline execution path."""

from __future__ import annotations

import importlib
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
import xarray as xr

from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.executor import (
    CheckpointManager,
    NullProgressCallback,
    RuntimeContext,
    SequentialExecutor,
    build_kwargs,
    classify_steps,
    compute_recipe_hash,
    explicit_checkpoint_steps,
    extract_outputs,
    import_callable,
    resolve_checkpoint_policy,
)
from aa_recipe_manager.executor.checkpoint import (
    CACHE_METADATA_DIR,
    OTHER_DATA_DIR,
    ZARR_DATA_DIR,
    _checkpoint_artifact_stem,
)
from aa_recipe_manager.model.types import (
    DAGEdge,
    DAGNode,
    Dependency,
    ExecutionHints,
    Implementation,
    PipelineDAG,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)


# ---------------------------------------------------------------------------
# Synthetic callable module
# ---------------------------------------------------------------------------


_HELPER_MODULE_NAME = "ar_stage6_test_helpers"


class EchoData:
    __module__ = "echopype.echodata.echodata"
    last_zarr_kwargs: dict[str, object] = {}

    def __init__(self, payload: str) -> None:
        self.payload = payload

    def to_netcdf(self, save_path: Path | str, **kwargs) -> None:
        Path(save_path).write_text(self.payload, encoding="utf-8")

    def to_zarr(self, save_path: Path | str, **kwargs) -> None:
        type(self).last_zarr_kwargs = kwargs
        # Simulate a zarr store as a directory containing a single file
        store = Path(save_path)
        store.mkdir(parents=True, exist_ok=True)
        (store / "payload.txt").write_text(self.payload, encoding="utf-8")

    @classmethod
    def from_file(cls, path: str) -> "EchoData":
        p = Path(path)
        if p.is_dir():
            return cls((p / "payload.txt").read_text(encoding="utf-8"))
        return cls(p.read_text(encoding="utf-8"))


def _meta_path(root: Path, step_id: str) -> Path:
    return root / CACHE_METADATA_DIR / f"{step_id}__cache_meta.json"


def _meta_names(root: Path) -> set[str]:
    meta_dir = root / CACHE_METADATA_DIR
    if not meta_dir.exists():
        return set()
    return {p.name for p in meta_dir.glob("*__cache_meta.json")}


def _install_helper_module() -> types.ModuleType:
    """Register a throwaway module with stable callables for tests to import."""
    if _HELPER_MODULE_NAME in sys.modules:
        return sys.modules[_HELPER_MODULE_NAME]

    module = types.ModuleType(_HELPER_MODULE_NAME)
    module.call_log = []  # type: ignore[attr-defined]

    def _record(name: str, **kwargs: Any) -> None:
        module.call_log.append((name, kwargs))  # type: ignore[attr-defined]

    def add_one(x: int) -> int:
        _record("add_one", x=x)
        return x + 1

    def multiply(x: int, factor: int = 2) -> int:
        _record("multiply", x=x, factor=factor)
        return x * factor

    def make_pair(value: int) -> tuple[int, int]:
        _record("make_pair", value=value)
        return (value, -value)

    def make_dict(value: int) -> dict[str, int]:
        _record("make_dict", value=value)
        return {"positive": value, "negative": -value}

    def fan_in_sum(values: list[int]) -> int:
        _record("fan_in_sum", values=values)
        return sum(values)

    def renamed_arg(target: int) -> int:
        _record("renamed_arg", target=target)
        return target * 10

    def sink_step(value: int) -> None:
        _record("sink_step", value=value)
        return None

    def boom(value: int) -> int:
        _record("boom", value=value)
        raise RuntimeError("kaboom")

    class _Container:
        def __init__(self, value: int) -> None:
            self.value = value

    def make_container(value: int) -> _Container:
        _record("make_container", value=value)
        return _Container(value)

    module.add_one = add_one  # type: ignore[attr-defined]
    module.multiply = multiply  # type: ignore[attr-defined]
    module.make_pair = make_pair  # type: ignore[attr-defined]
    module.make_dict = make_dict  # type: ignore[attr-defined]
    module.fan_in_sum = fan_in_sum  # type: ignore[attr-defined]
    module.renamed_arg = renamed_arg  # type: ignore[attr-defined]
    module.sink_step = sink_step  # type: ignore[attr-defined]
    module.boom = boom  # type: ignore[attr-defined]
    module.make_container = make_container  # type: ignore[attr-defined]
    module.Container = _Container  # type: ignore[attr-defined]

    sys.modules[_HELPER_MODULE_NAME] = module
    return module


@pytest.fixture
def helper_module() -> types.ModuleType:
    module = _install_helper_module()
    module.call_log.clear()  # type: ignore[attr-defined]
    return module


# ---------------------------------------------------------------------------
# Recipe / DAG construction helpers
# ---------------------------------------------------------------------------


def _dep() -> Dependency:
    return Dependency(name="pytest", version=">=7.0", source="pypi")


def _make_dag(nodes: list[DAGNode], edges: list[DAGEdge]) -> PipelineDAG:
    steps = [n.step for n in nodes]
    recipe = Recipe(
        name="stage6_pipeline",
        version="1.0.0",
        steps=steps,
        schema_version="1",
    )
    return PipelineDAG(
        recipe=recipe,
        nodes={n.step.id: n for n in nodes},
        edges=edges,
        topological_order=[n.step.id for n in nodes],
    )


def _linear_inc_dag() -> PipelineDAG:
    """``start -> first -> second`` chain calling add_one each time."""
    spec = Spec(
        op="add_one",
        description="add one",
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    impl = Implementation(
        op="add_one",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.add_one",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    start = DAGNode(
        step=Step(id="start", op="add_one", inputs={"x": "${inputs.seed}"}),
        spec=spec,
        implementation=impl,
    )
    first = DAGNode(
        step=Step(id="first", op="add_one", inputs={"x": "${start.out}"}),
        spec=spec,
        implementation=impl,
    )
    second = DAGNode(
        step=Step(id="second", op="add_one", inputs={"x": "${first.out}"}),
        spec=spec,
        implementation=impl,
    )
    edges = [
        DAGEdge(
            source_step_id="start",
            source_output="out",
            target_step_id="first",
            target_input="x",
        ),
        DAGEdge(
            source_step_id="first",
            source_output="out",
            target_step_id="second",
            target_input="x",
        ),
    ]
    return _make_dag([start, first, second], edges)


# ---------------------------------------------------------------------------
# import_callable
# ---------------------------------------------------------------------------


class TestImportCallable:
    def test_imports_function(self, helper_module):
        fn = import_callable(f"{_HELPER_MODULE_NAME}.add_one")
        assert fn(2) == 3

    def test_imports_attribute_on_class(self, helper_module):
        fn = import_callable(f"{_HELPER_MODULE_NAME}.Container")
        assert fn(7).value == 7

    def test_missing_attribute_raises(self, helper_module):
        with pytest.raises(AttributeError):
            import_callable(f"{_HELPER_MODULE_NAME}.does_not_exist")

    def test_requires_dotted_path(self):
        with pytest.raises(ImportError):
            import_callable("bare_name")


# ---------------------------------------------------------------------------
# Kwarg construction (Stage 6a)
# ---------------------------------------------------------------------------


class TestBuildKwargs:
    def test_param_map_renames_input_port(self, helper_module):
        spec = Spec(
            op="renamed",
            description="renamed arg",
            inputs={"src": PortDeclaration(type="int")},
            outputs={"out": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="renamed",
            key="default",
            callable_path=f"{_HELPER_MODULE_NAME}.renamed_arg",
            dependency=_dep(),
            param_map={"src": "target"},
        )
        node = DAGNode(
            step=Step(id="rename", op="renamed", inputs={"src": "${inputs.seed}"}),
            spec=spec,
            implementation=impl,
        )
        runtime = RuntimeContext()
        kwargs = build_kwargs(node, runtime, {"seed": 4})
        assert kwargs == {"target": 4}

    def test_fan_in_input_collects_list(self, helper_module):
        upstream_a = Spec(
            op="make",
            description="",
            outputs={"val": PortDeclaration(type="int")},
        )
        impl_a = Implementation(
            op="make",
            key="default",
            callable_path=f"{_HELPER_MODULE_NAME}.add_one",
            dependency=_dep(),
            output_map={"val": "__return__"},
        )
        sum_spec = Spec(
            op="fan_in",
            description="",
            inputs={"values": PortDeclaration(type="list", many=True)},
            outputs={"total": PortDeclaration(type="int")},
        )
        sum_impl = Implementation(
            op="fan_in",
            key="default",
            callable_path=f"{_HELPER_MODULE_NAME}.fan_in_sum",
            dependency=_dep(),
            output_map={"total": "__return__"},
        )
        runtime = RuntimeContext()
        runtime.record("a", {"val": 1})
        runtime.record("b", {"val": 2})
        runtime.record("c", {"val": 3})
        node = DAGNode(
            step=Step(
                id="combine",
                op="fan_in",
                inputs={"values": ["${a.val}", "${b.val}", "${c.val}"]},
            ),
            spec=sum_spec,
            implementation=sum_impl,
        )
        kwargs = build_kwargs(node, runtime, {})
        assert kwargs == {"values": [1, 2, 3]}

    def test_missing_optional_input_is_skipped(self, helper_module):
        spec = Spec(
            op="opt",
            description="",
            inputs={
                "x": PortDeclaration(type="int"),
                "extra": PortDeclaration(type="int", required=False),
            },
            outputs={"out": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="opt",
            key="default",
            callable_path=f"{_HELPER_MODULE_NAME}.add_one",
            dependency=_dep(),
        )
        node = DAGNode(
            step=Step(
                id="opt",
                op="opt",
                inputs={
                    "x": "${inputs.seed}",
                    "extra": "${inputs.absent}",
                },
            ),
            spec=spec,
            implementation=impl,
        )
        kwargs = build_kwargs(node, RuntimeContext(), {"seed": 5})
        assert kwargs == {"x": 5}


# ---------------------------------------------------------------------------
# Output extraction (Stage 6a)
# ---------------------------------------------------------------------------


class TestExtractOutputs:
    def _node(self, spec: Spec, impl: Implementation) -> DAGNode:
        return DAGNode(
            step=Step(id="s", op=spec.op),
            spec=spec,
            implementation=impl,
        )

    def test_implicit_single_output(self):
        spec = Spec(
            op="t",
            description="",
            outputs={"out": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="t", key="d", callable_path="m.f", dependency=_dep()
        )
        outputs = extract_outputs(self._node(spec, impl), 42)
        assert outputs == {"out": 42}

    def test_dict_key_extraction(self):
        spec = Spec(
            op="t",
            description="",
            outputs={
                "pos": PortDeclaration(type="int"),
                "neg": PortDeclaration(type="int"),
            },
        )
        impl = Implementation(
            op="t",
            key="d",
            callable_path="m.f",
            dependency=_dep(),
            output_map={"pos": "['positive']", "neg": "['negative']"},
        )
        outputs = extract_outputs(
            self._node(spec, impl), {"positive": 3, "negative": -3}
        )
        assert outputs == {"pos": 3, "neg": -3}

    def test_tuple_index_extraction(self):
        spec = Spec(
            op="t",
            description="",
            outputs={
                "first": PortDeclaration(type="int"),
                "second": PortDeclaration(type="int"),
            },
        )
        impl = Implementation(
            op="t",
            key="d",
            callable_path="m.f",
            dependency=_dep(),
            output_map={"first": "[0]", "second": "[1]"},
        )
        outputs = extract_outputs(self._node(spec, impl), (10, 20))
        assert outputs == {"first": 10, "second": 20}

    def test_attribute_extraction(self):
        spec = Spec(
            op="t",
            description="",
            outputs={"value": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="t",
            key="d",
            callable_path="m.f",
            dependency=_dep(),
            output_map={"value": ".value"},
        )

        class _Holder:
            value = 99

        outputs = extract_outputs(self._node(spec, impl), _Holder())
        assert outputs == {"value": 99}

    def test_optional_output_falls_back_to_default(self):
        spec = Spec(
            op="t",
            description="",
            outputs={
                "primary": PortDeclaration(type="int"),
                "maybe": PortDeclaration(
                    type="int", required=False, default=-1
                ),
            },
        )
        impl = Implementation(
            op="t",
            key="d",
            callable_path="m.f",
            dependency=_dep(),
            output_map={"primary": "['primary']", "maybe": "['missing']"},
        )
        outputs = extract_outputs(
            self._node(spec, impl), {"primary": 1}
        )
        assert outputs == {"primary": 1, "maybe": -1}


# ---------------------------------------------------------------------------
# Sequential executor
# ---------------------------------------------------------------------------


class TestSequentialExecutor:
    def test_runs_linear_chain(self, helper_module):
        dag = _linear_inc_dag()
        executor = SequentialExecutor()
        result = executor.execute(dag, inputs={"seed": 10})
        assert result.outputs["start"]["out"] == 11
        assert result.outputs["first"]["out"] == 12
        assert result.outputs["second"]["out"] == 13
        assert result.executed_steps == ["start", "first", "second"]
        assert result.skipped_steps == []
        assert result.provenance is not None

    def test_step_failure_raises_pipeline_execution_error(self, helper_module):
        spec = Spec(
            op="boom",
            description="",
            inputs={"value": PortDeclaration(type="int")},
            outputs={"out": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="boom",
            key="d",
            callable_path=f"{_HELPER_MODULE_NAME}.boom",
            dependency=_dep(),
        )
        node = DAGNode(
            step=Step(id="boom", op="boom", inputs={"value": "${inputs.seed}"}),
            spec=spec,
            implementation=impl,
        )
        dag = _make_dag([node], [])
        with pytest.raises(PipelineExecutionError) as exc_info:
            SequentialExecutor().execute(dag, inputs={"seed": 1})
        assert exc_info.value.step_id == "boom"
        assert exc_info.value.callable_path.endswith(".boom")
        assert isinstance(exc_info.value.original, RuntimeError)

    def test_sink_step_executes_but_records_no_outputs(self, helper_module):
        spec = Spec(
            op="sink",
            description="",
            sink=True,
            inputs={"value": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="sink",
            key="d",
            callable_path=f"{_HELPER_MODULE_NAME}.sink_step",
            dependency=_dep(),
        )
        node = DAGNode(
            step=Step(id="sink", op="sink", inputs={"value": "${inputs.seed}"}),
            spec=spec,
            implementation=impl,
        )
        result = SequentialExecutor().execute(
            _make_dag([node], []), inputs={"seed": 7}
        )
        assert result.outputs["sink"] == {}
        assert ("sink_step", {"value": 7}) in helper_module.call_log

    def test_skip_sinks_does_not_invoke_callable(self, helper_module):
        spec = Spec(
            op="sink",
            description="",
            sink=True,
            inputs={"value": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="sink",
            key="d",
            callable_path=f"{_HELPER_MODULE_NAME}.sink_step",
            dependency=_dep(),
        )
        node = DAGNode(
            step=Step(id="sink", op="sink", inputs={"value": "${inputs.seed}"}),
            spec=spec,
            implementation=impl,
        )
        SequentialExecutor().execute(
            _make_dag([node], []), inputs={"seed": 1}, skip_sinks=True
        )
        assert helper_module.call_log == []

    def test_progress_callback_receives_step_indices(self, helper_module):
        dag = _linear_inc_dag()
        events: list[tuple[str, str, int, int, bool]] = []

        class _Probe:
            def on_step_start(self, step_id, index, total):
                events.append(("start", step_id, index, total, False))

            def on_step_end(self, step_id, index, total, *, skipped=False,
                             elapsed=0.0, error=None):
                events.append(("end", step_id, index, total, skipped))

        SequentialExecutor().execute(
            dag, inputs={"seed": 0}, progress=_Probe()
        )
        starts = [e for e in events if e[0] == "start"]
        ends = [e for e in events if e[0] == "end"]
        assert [e[1] for e in starts] == ["start", "first", "second"]
        assert [e[2] for e in starts] == [1, 2, 3]
        assert all(e[3] == 3 for e in starts)
        assert all(not e[4] for e in ends)


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


class TestCheckpointing:
    def test_skips_steps_on_second_run(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        executor = SequentialExecutor()
        first = executor.execute(dag, inputs={"seed": 1}, output_dir=out)
        assert first.executed_steps == ["start", "first", "second"]
        assert first.skipped_steps == []
        helper_module.call_log.clear()

        second = executor.execute(dag, inputs={"seed": 1}, output_dir=out)
        assert second.skipped_steps == ["start", "first", "second"]
        assert second.executed_steps == []
        assert helper_module.call_log == []
        assert second.outputs["second"]["out"] == 4

    def test_force_re_executes_all_steps(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        helper_module.call_log.clear()
        result = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, force=True
        )
        assert result.executed_steps == ["start", "first", "second"]
        assert len(helper_module.call_log) == 3

    def test_recipe_hash_change_invalidates_cache(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        helper_module.call_log.clear()

        modified = dag.model_copy(deep=True)
        modified.recipe.description = "now different"
        result = SequentialExecutor().execute(
            modified, inputs={"seed": 1}, output_dir=out
        )
        assert result.executed_steps == ["start", "first", "second"]
        assert result.skipped_steps == []

    def test_no_checkpoints_writes_no_files(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        result = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, no_checkpoints=True
        )
        assert result.outputs["second"]["out"] == 4
        assert not out.exists() or not any(out.iterdir())
        assert result.output_dir is None

    def test_checkpoint_load_preserves_echodata_objects(self, tmp_path, monkeypatch):
        fake_echodata_pkg = types.ModuleType("echopype.echodata")
        fake_echodata_module = types.ModuleType("echopype.echodata.echodata")
        fake_echodata_module.EchoData = EchoData
        monkeypatch.setitem(sys.modules, "echopype.echodata", fake_echodata_pkg)
        monkeypatch.setitem(sys.modules, "echopype.echodata.echodata", fake_echodata_module)

        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, "hash", preferred_format="netcdf")
        manager.save("open_raw", {"echodata": EchoData("vendor-specific")})
        meta = json.loads(_meta_path(out, "open_raw").read_text(encoding="utf-8"))

        loaded = manager.load("open_raw")

        assert meta["outputs"]["echodata"]["format"] == "echodata_netcdf"
        assert loaded["echodata"].__class__.__name__ == "EchoData"
        assert loaded["echodata"].payload == "vendor-specific"

    def test_echodata_zarr_checkpoint_round_trip(self, tmp_path, monkeypatch):
        """EchoData saved with zarr format reloads correctly."""
        fake_echodata_pkg = types.ModuleType("echopype.echodata")
        fake_echodata_module = types.ModuleType("echopype.echodata.echodata")
        fake_echodata_module.EchoData = EchoData
        monkeypatch.setitem(sys.modules, "echopype.echodata", fake_echodata_pkg)
        monkeypatch.setitem(sys.modules, "echopype.echodata.echodata", fake_echodata_module)
        # Stub echopype.open_converted to return EchoData loaded from dir
        fake_ep = types.ModuleType("echopype")
        fake_ep.open_converted = lambda path, **kwargs: EchoData.from_file(path)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "echopype", fake_ep)

        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, "hash")  # default = zarr
        manager.save("open_raw", {"echodata": EchoData("zarr-payload")})
        meta = json.loads(_meta_path(out, "open_raw").read_text(encoding="utf-8"))

        assert meta["outputs"]["echodata"]["format"] == "echodata_zarr"
        assert Path(meta["outputs"]["echodata"]["path"]).parent == out / ZARR_DATA_DIR
        assert EchoData.last_zarr_kwargs["zarr_format"] == 2
        assert manager.has_checkpoint("open_raw")

        loaded = manager.load("open_raw")
        assert loaded["echodata"].__class__.__name__ == "EchoData"
        assert loaded["echodata"].payload == "zarr-payload"

    def test_netcdf_checkpoint_still_loads_xarray_dataset(self, tmp_path):
        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, "hash", preferred_format="netcdf")
        ds = xr.Dataset({"value": ("x", [1, 2, 3])})
        manager.save("dataset_step", {"ds": ds})

        meta = json.loads(_meta_path(out, "dataset_step").read_text(encoding="utf-8"))
        assert meta["outputs"]["ds"]["format"] == "netcdf"
        assert Path(meta["outputs"]["ds"]["path"]).parent == out / OTHER_DATA_DIR

        loaded = manager.load("dataset_step")
        assert list(loaded["ds"]["value"].values) == [1, 2, 3]

    def test_zarr_checkpoint_round_trip_xarray_dataset(self, tmp_path):
        """xarray Dataset saved with zarr (default) reloads correctly."""
        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, "hash")  # default = zarr
        ds = xr.Dataset({"value": ("x", [10, 20, 30])})
        manager.save("sv_step", {"ds_Sv": ds})

        meta = json.loads(_meta_path(out, "sv_step").read_text(encoding="utf-8"))
        assert meta["outputs"]["ds_Sv"]["format"] == "zarr"
        store = Path(meta["outputs"]["ds_Sv"]["path"])
        assert store.parent == out / ZARR_DATA_DIR
        zgroup = json.loads((store / ".zgroup").read_text(encoding="utf-8"))
        assert zgroup["zarr_format"] == 2
        assert manager.has_checkpoint("sv_step")

        loaded = manager.load("sv_step")
        assert list(loaded["ds_Sv"]["value"].values) == [10, 20, 30]

    def test_zarr_checkpoint_round_trip_xarray_dataarray(self, tmp_path):
        """xarray DataArray saved with zarr (default) reloads as DataArray."""
        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, "hash")  # default = zarr
        da = xr.DataArray([1.0, 2.0, 3.0], dims=["x"], name="sig")
        manager.save("da_step", {"arr": da})

        meta = json.loads(_meta_path(out, "da_step").read_text(encoding="utf-8"))
        assert meta["outputs"]["arr"]["format"] == "zarr_da"
        store = Path(meta["outputs"]["arr"]["path"])
        assert store.parent == out / ZARR_DATA_DIR
        zgroup = json.loads((store / ".zgroup").read_text(encoding="utf-8"))
        assert zgroup["zarr_format"] == 2

        loaded = manager.load("da_step")
        import xarray as xr2
        assert isinstance(loaded["arr"], xr2.DataArray)
        assert list(loaded["arr"].values) == [1.0, 2.0, 3.0]

    def test_checkpoint_artifact_stem_uses_original_names(self):
        stem = _checkpoint_artifact_stem(
            "add_aux_features",
            "ds_ml_ready",
        )
        assert stem == "add_aux_features_ds_ml_ready"


# ---------------------------------------------------------------------------
# Step classification + clean
# ---------------------------------------------------------------------------


class TestCleanAndClassification:
    def test_classify_steps_partitions_dag(self):
        dag = _linear_inc_dag()
        terminal, intermediate = classify_steps(dag)
        assert terminal == {"second"}
        assert intermediate == {"start", "first"}

    def test_clean_intermediate_preserves_terminal(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        manager = CheckpointManager(out, compute_recipe_hash(dag))
        removed = manager.clean(dag)
        removed_names = {p.name for p in removed}
        assert "start__cache_meta.json" in removed_names
        assert "first__cache_meta.json" in removed_names
        assert "second__cache_meta.json" not in removed_names
        assert _meta_path(out, "second").exists()
        assert not _meta_path(out, "start").exists()

    def test_clean_all_removes_everything(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        manager = CheckpointManager(out, compute_recipe_hash(dag))
        manager.clean(dag, mode="all")
        assert not _meta_names(out)

    def test_clean_stale_only_removes_other_hashes(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        # Trick the manager into thinking we're on a different recipe hash.
        manager = CheckpointManager(out, "different-hash")
        removed = manager.clean(dag, mode="stale")
        assert len(removed) > 0
        # And running stale clean again should be a no-op.
        manager_same = CheckpointManager(out, compute_recipe_hash(dag))
        # Now files for current hash are gone, so stale clean finds nothing
        # whose hash matches the manager's own.
        assert manager_same.clean(dag, mode="stale") == []

    def test_clean_dry_run_does_not_remove(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        manager = CheckpointManager(out, compute_recipe_hash(dag))
        planned = manager.clean(dag, mode="all", dry_run=True)
        for path in planned:
            assert path.exists()


# ---------------------------------------------------------------------------
# Provenance sidecar
# ---------------------------------------------------------------------------


def test_provenance_sidecar_written(helper_module, tmp_path):
    from aa_recipe_manager.api import _write_provenance_sidecar
    from aa_recipe_manager.provenance.recorder import ProvenanceRecorder

    dag = _linear_inc_dag()
    SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=tmp_path / "ckpt"
    )
    provenance = ProvenanceRecorder.capture(dag)
    sidecar = tmp_path / "provenance.yaml"
    _write_provenance_sidecar(provenance, sidecar)
    assert sidecar.exists()
    content = sidecar.read_text(encoding="utf-8")
    assert "start" in content
    assert "first" in content
    assert "second" in content


def test_api_execute_rejects_no_checkpoints_with_checkpoint_steps():
    from aa_recipe_manager import api

    dag = _linear_inc_dag()
    with pytest.raises(ValueError, match="cannot be combined"):
        api.execute(
            dag.recipe,
            no_checkpoints=True,
            checkpoint_steps=["start"],
        )


def test_api_execute_rejects_checkpoint_options_without_output_dir():
    from aa_recipe_manager import api

    dag = _linear_inc_dag()
    with pytest.raises(ValueError, match="require output_dir"):
        api.execute(
            dag.recipe,
            checkpoint_mode="eager",
            output_dir=None,
        )


# ---------------------------------------------------------------------------
# Checkpoint policy (explicit / terminal / per-step / ad-hoc)
# ---------------------------------------------------------------------------


def _set_step_checkpoint(
    dag: PipelineDAG, step_id: str, value: str | None
) -> None:
    """Mutate a DAG's step in place to set its ``checkpoint`` field."""
    node = dag.nodes[step_id]
    node.step.checkpoint = value
    for step in dag.recipe.steps:
        if step.id == step_id:
            step.checkpoint = value


def _set_recipe_checkpoint_mode(dag: PipelineDAG, mode: str | None) -> None:
    dag.recipe.execution = ExecutionHints(checkpoint_mode=mode)


class TestCheckpointPolicy:
    def test_eager_mode_checkpoints_every_step(self):
        dag = _linear_inc_dag()
        policy = resolve_checkpoint_policy(dag)
        assert policy == {"start", "first", "second"}

    def test_explicit_mode_only_marked_steps(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        policy = resolve_checkpoint_policy(dag, mode="explicit")
        assert policy == {"first"}

    def test_terminal_mode_only_leaf_steps(self):
        dag = _linear_inc_dag()
        policy = resolve_checkpoint_policy(dag, mode="terminal")
        assert policy == {"second"}

    def test_none_mode_writes_nothing(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        # Even with a per-step "always" mark, "none" mode forces nothing? No -
        # per-step "always" beats mode. Verify that contract here.
        policy = resolve_checkpoint_policy(dag, mode="none")
        assert policy == {"first"}

    def test_per_step_never_overrides_eager(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "never")
        policy = resolve_checkpoint_policy(dag, mode="eager")
        assert policy == {"start", "second"}

    def test_ad_hoc_extra_step_ids(self):
        dag = _linear_inc_dag()
        policy = resolve_checkpoint_policy(
            dag, mode="explicit", extra_step_ids={"start"}
        )
        assert policy == {"start"}

    def test_ad_hoc_unknown_step_id_raises(self):
        dag = _linear_inc_dag()
        with pytest.raises(ValueError, match="unknown step id"):
            resolve_checkpoint_policy(
                dag, mode="explicit", extra_step_ids={"typo"}
            )

    def test_ad_hoc_sink_step_id_raises(self):
        spec = Spec(
            op="sink",
            description="sink",
            sink=True,
            inputs={"value": PortDeclaration(type="int")},
        )
        impl = Implementation(
            op="sink",
            key="default",
            callable_path=f"{_HELPER_MODULE_NAME}.sink_step",
            dependency=_dep(),
            output_map={},
        )
        node = DAGNode(
            step=Step(id="sink", op="sink", inputs={"value": "${inputs.seed}"}),
            spec=spec,
            implementation=impl,
        )
        dag = _make_dag([node], [])
        with pytest.raises(ValueError, match="sink or no-output"):
            resolve_checkpoint_policy(
                dag, mode="explicit", extra_step_ids={"sink"}
            )

    def test_recipe_level_mode_is_default(self):
        dag = _linear_inc_dag()
        _set_recipe_checkpoint_mode(dag, "terminal")
        policy = resolve_checkpoint_policy(dag)
        assert policy == {"second"}

    def test_invalid_mode_raises(self):
        dag = _linear_inc_dag()
        with pytest.raises(ValueError):
            resolve_checkpoint_policy(dag, mode="bogus")

    def test_explicit_checkpoint_steps_helper(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        _set_step_checkpoint(dag, "second", "never")
        assert explicit_checkpoint_steps(dag) == {"first"}


class TestExecutorCheckpointModes:
    def test_explicit_mode_only_writes_marked_step(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        meta_files = _meta_names(out)
        assert meta_files == {"first__cache_meta.json"}

    def test_terminal_mode_only_writes_leaf(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="terminal",
        )
        meta_files = _meta_names(out)
        assert meta_files == {"second__cache_meta.json"}

    def test_ad_hoc_checkpoint_steps_pin_resume_point(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
            checkpoint_steps=["start"],
        )
        meta_files = _meta_names(out)
        assert meta_files == {"start__cache_meta.json"}

    def test_explicit_mode_resume_from_marked_step(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        helper_module.call_log.clear()
        # Second run: only "first" has a checkpoint. "start" and "second" must
        # re-execute; "first" is loaded from cache.
        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        assert "first" in result.skipped_steps
        assert "start" in result.executed_steps
        assert "second" in result.executed_steps
        names = [c[0] for c in helper_module.call_log]
        assert "add_one" in names
        # Should have been called for start and second only (not first).
        assert len(names) == 2

    def test_recipe_level_mode_picked_up_when_no_override(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _set_recipe_checkpoint_mode(dag, "terminal")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out
        )
        meta_files = _meta_names(out)
        assert meta_files == {"second__cache_meta.json"}


class TestCleanRespectsExplicitMarks:
    def test_intermediate_clean_preserves_explicit_checkpoint(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        out = tmp_path / "ckpt"
        # Eager run writes all three; "first" is also explicitly marked.
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        manager = CheckpointManager(out, compute_recipe_hash(dag))
        manager.clean(dag, mode="intermediate")
        remaining = _meta_names(out)
        # "second" (terminal) and "first" (explicitly marked) both survive.
        assert remaining == {
            "first__cache_meta.json",
            "second__cache_meta.json",
        }

    def test_all_mode_still_removes_explicit_checkpoints(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(dag, inputs={"seed": 1}, output_dir=out)
        manager = CheckpointManager(out, compute_recipe_hash(dag))
        manager.clean(dag, mode="all")
        assert not _meta_names(out)
