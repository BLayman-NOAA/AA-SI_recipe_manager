# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the Stage 6 direct pipeline execution path."""

from __future__ import annotations

import importlib
import json
import os
import shutil
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
    compute_step_hashes,
)
from aa_recipe_manager.model.types import (
    DAGEdge,
    DAGNode,
    Dependency,
    ExecutionHints,
    Implementation,
    InputDeclaration,
    ParamDeclaration,
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


def _iter_meta(root: Path) -> list[dict[str, Any]]:
    """All parsed sidecars under a content-addressed cache root."""
    if not root.exists():
        return []
    return [
        json.loads(p.read_text(encoding="utf-8"))
        for p in sorted(root.glob("*/*/meta.json"))
    ]


def _meta_for_step(root: Path, step_id: str) -> dict[str, Any] | None:
    """Sidecar dict for a step id (scans entry dirs — test convenience)."""
    for meta in _iter_meta(root):
        if meta.get("step_id") == step_id:
            return meta
    return None


def _artifact_path(root: Path, meta: dict[str, Any], out_name: str) -> Path:
    """Absolute path of a sidecar artifact entry (relative to its hash dir)."""
    from aa_recipe_manager.executor import entry_dir_parts

    step_dir, key = entry_dir_parts(meta["step_id"], meta["step_hash"])
    return root / step_dir / key / meta["outputs"][out_name]["path"]


def _meta_names(root: Path) -> set[str]:
    """Step ids that have sidecars under the content-addressed root."""
    return {meta["step_id"] for meta in _iter_meta(root)}


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

    def sink_with_label(value: int, label: str = "default") -> None:
        _record("sink_step", value=value, label=label)
        return None

    def _write_artifact() -> None:
        """Write a fake image under <outputs>/images and record it on the
        execution context's artifact_sink, mimicking render_figure."""
        from pathlib import Path

        from aa_recipe_manager.executor.runtime_context import (
            get_execution_context,
        )

        ctx = get_execution_context()
        artifacts_dir = getattr(ctx, "artifacts_dir", None)
        if artifacts_dir is None:
            return
        step_id = getattr(ctx, "step_id", None) or "artifact"
        rel = f"images/{step_id}.png"
        target = Path(str(artifacts_dir)) / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"fake image bytes")
        sink = getattr(ctx, "artifact_sink", None)
        if sink is not None:
            sink.append(rel)

    def plotting_sink(value: int) -> None:
        """Sink that writes an artifact file (like an echogram plot)."""
        _record("plotting_sink", value=value)
        _write_artifact()
        return None

    def data_with_artifact(x: int) -> int:
        """Non-sink step that returns data *and* writes a side-effect artifact."""
        _record("data_with_artifact", x=x)
        _write_artifact()
        return x + 100

    def boom(value: int) -> int:
        _record("boom", value=value)
        raise RuntimeError("kaboom")

    class _Container:
        def __init__(self, value: int) -> None:
            self.value = value

    def make_container(value: int) -> _Container:
        _record("make_container", value=value)
        return _Container(value)

    def path_probe(raw_input_folder: str) -> str:
        _record("path_probe", raw_input_folder=raw_input_folder)
        return raw_input_folder

    module.add_one = add_one  # type: ignore[attr-defined]
    module.multiply = multiply  # type: ignore[attr-defined]
    module.make_pair = make_pair  # type: ignore[attr-defined]
    module.make_dict = make_dict  # type: ignore[attr-defined]
    module.fan_in_sum = fan_in_sum  # type: ignore[attr-defined]
    module.renamed_arg = renamed_arg  # type: ignore[attr-defined]
    module.sink_step = sink_step  # type: ignore[attr-defined]
    module.sink_with_label = sink_with_label  # type: ignore[attr-defined]
    module.plotting_sink = plotting_sink  # type: ignore[attr-defined]
    module.data_with_artifact = data_with_artifact  # type: ignore[attr-defined]
    module.boom = boom  # type: ignore[attr-defined]
    module.make_container = make_container  # type: ignore[attr-defined]
    module.path_probe = path_probe  # type: ignore[attr-defined]
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


def _linear_multiply_dag(factor: int = 2) -> PipelineDAG:
    """``start(add_one) -> first(multiply) -> scale(multiply)`` chain.

    The two ``multiply`` steps carry a ``factor`` param so a test can edit a
    single step's parameter (as a user editing e.g. ``min_samples`` would) and
    observe per-step cache invalidation.
    """
    add_spec = Spec(
        op="add_one",
        description="add one",
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    add_impl = Implementation(
        op="add_one",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.add_one",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    mul_spec = Spec(
        op="multiply",
        description="multiply by factor",
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    mul_impl = Implementation(
        op="multiply",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.multiply",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    start = DAGNode(
        step=Step(id="start", op="add_one", inputs={"x": "${inputs.seed}"}),
        spec=add_spec,
        implementation=add_impl,
    )
    first = DAGNode(
        step=Step(
            id="first",
            op="multiply",
            inputs={"x": "${start.out}"},
            params={"factor": 2},
        ),
        spec=mul_spec,
        implementation=mul_impl,
        resolved_params={"factor": 2},
    )
    scale = DAGNode(
        step=Step(
            id="scale",
            op="multiply",
            inputs={"x": "${first.out}"},
            params={"factor": factor},
        ),
        spec=mul_spec,
        implementation=mul_impl,
        resolved_params={"factor": factor},
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
            target_step_id="scale",
            target_input="x",
        ),
    ]
    return _make_dag([start, first, scale], edges)


def _sink_after_chain_dag() -> PipelineDAG:
    """``start(add_one) -> first(add_one) -> report(sink)`` chain.

    The terminal ``report`` step is a sink with a ``label`` param so tests can
    verify marker-based skipping, ``regenerate`` forcing, and per-step
    invalidation when only the sink's param changes.
    """
    add_spec = Spec(
        op="add_one",
        description="add one",
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    add_impl = Implementation(
        op="add_one",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.add_one",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    sink_spec = Spec(
        op="report",
        description="side-effect report",
        sink=True,
        inputs={"value": PortDeclaration(type="int")},
        params={"label": ParamDeclaration(type="str", required=False)},
    )
    sink_impl = Implementation(
        op="report",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.sink_with_label",
        dependency=_dep(),
    )
    start = DAGNode(
        step=Step(id="start", op="add_one", inputs={"x": "${inputs.seed}"}),
        spec=add_spec,
        implementation=add_impl,
    )
    first = DAGNode(
        step=Step(id="first", op="add_one", inputs={"x": "${start.out}"}),
        spec=add_spec,
        implementation=add_impl,
    )
    report = DAGNode(
        step=Step(
            id="report",
            op="report",
            inputs={"value": "${first.out}"},
            params={"label": "base"},
        ),
        spec=sink_spec,
        implementation=sink_impl,
        resolved_params={"label": "base"},
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
            target_step_id="report",
            target_input="value",
        ),
    ]
    return _make_dag([start, first, report], edges)


def _regen_pipeline_dag(
    *, sink_regen: str | None = None, data_regen: str | None = None
) -> PipelineDAG:
    """``compute(data_with_artifact) -> plot(plotting_sink, sink)``.

    Both steps write an ``images/<step_id>.png`` artifact. ``compute`` is a
    non-sink data step (regenerating it recomputes) and ``plot`` is a sink
    (regenerating it only re-renders). Each step's ``regenerate`` attribute is
    configurable so tests can exercise every regeneration mode.
    """
    compute_spec = Spec(
        op="data_with_artifact",
        description="data step that also writes an artifact",
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    compute_impl = Implementation(
        op="data_with_artifact",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.data_with_artifact",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    plot_spec = Spec(
        op="plotting_sink",
        description="sink that writes an artifact",
        sink=True,
        inputs={"value": PortDeclaration(type="int")},
    )
    plot_impl = Implementation(
        op="plotting_sink",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.plotting_sink",
        dependency=_dep(),
    )
    compute = DAGNode(
        step=Step(
            id="compute",
            op="data_with_artifact",
            inputs={"x": "${inputs.seed}"},
            regenerate=data_regen,
        ),
        spec=compute_spec,
        implementation=compute_impl,
    )
    plot = DAGNode(
        step=Step(
            id="plot",
            op="plotting_sink",
            inputs={"value": "${compute.out}"},
            regenerate=sink_regen,
        ),
        spec=plot_spec,
        implementation=plot_impl,
    )
    edges = [
        DAGEdge(
            source_step_id="compute",
            source_output="out",
            target_step_id="plot",
            target_input="value",
        ),
    ]
    return _make_dag([compute, plot], edges)


def _path_input_dag(path_value: str) -> PipelineDAG:
    spec = Spec(
        op="path_probe",
        description="fingerprint a path input",
        outputs={"out": PortDeclaration(type="str")},
        params={"raw_input_folder": ParamDeclaration(type="path")},
    )
    impl = Implementation(
        op="path_probe",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.path_probe",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    node = DAGNode(
        step=Step(
            id="probe",
            op="path_probe",
            params={"raw_input_folder": "${inputs.raw_dir}"},
        ),
        spec=spec,
        implementation=impl,
        resolved_params={"raw_input_folder": path_value},
    )
    recipe = Recipe(
        name="path_probe_pipeline",
        version="1.0.0",
        inputs={"raw_dir": InputDeclaration(type="path", fingerprint_contents=True)},
        steps=[node.step],
        schema_version="1",
    )
    return PipelineDAG(
        recipe=recipe,
        nodes={"probe": node},
        edges=[],
        topological_order=["probe"],
    )


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
        assert result.pruned_steps == []

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

    def test_unchanged_sink_skipped_on_second_run(self, helper_module, tmp_path):
        dag = _sink_after_chain_dag()
        out = tmp_path / "ckpt"
        first = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert "report" in first.executed_steps
        helper_module.call_log.clear()

        second = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        # The sink's side-effect marker matches, so it is skipped this run.
        assert "report" in second.skipped_steps
        assert second.executed_steps == []
        assert helper_module.call_log == []

    def test_regenerate_sinks_forces_sink_rerun(self, helper_module, tmp_path):
        dag = _sink_after_chain_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        helper_module.call_log.clear()

        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            regenerate="sinks",
            checkpoint_mode="eager",
        )
        # Upstream data steps still load from cache; only the sink re-runs.
        assert result.executed_steps == ["report"]
        assert "start" in result.pruned_steps
        assert "first" in result.skipped_steps
        assert [name for name, _ in helper_module.call_log] == ["sink_step"]

    def test_editing_sink_param_reruns_only_sink(self, helper_module, tmp_path):
        dag = _sink_after_chain_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        helper_module.call_log.clear()

        modified = dag.model_copy(deep=True)
        modified.nodes["report"].step.params["label"] = "changed"
        modified.nodes["report"].resolved_params["label"] = "changed"

        result = SequentialExecutor().execute(
            modified,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="eager",
        )
        assert result.executed_steps == ["report"]
        assert "start" in result.pruned_steps
        assert "first" in result.skipped_steps

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

    def test_checkpointed_step_is_reloaded_before_downstream_use(
        self, helper_module, monkeypatch, tmp_path
    ):
        dag = _linear_inc_dag()
        saved: dict[str, dict[str, Any]] = {}

        def fake_save(
            _self, step_id: str, outputs: dict[str, Any], *, artifacts=None
        ) -> None:
            saved[step_id] = dict(outputs)

        def fake_load(_self, step_id: str) -> dict[str, Any]:
            outputs = dict(saved[step_id])
            if step_id == "first":
                outputs["out"] = 40
            return outputs

        monkeypatch.setattr(CheckpointManager, "save", fake_save)
        monkeypatch.setattr(CheckpointManager, "load", fake_load)

        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 0},
            output_dir=tmp_path / "ckpt",
            checkpoint_mode="eager",
        )

        assert result.outputs["first"]["out"] == 40
        assert result.outputs["second"]["out"] == 41
        assert [entry[1] for entry in helper_module.call_log] == [
            {"x": 0},
            {"x": 1},
            {"x": 40},
        ]

    def test_cleanup_temp_dir_retries_transient_windows_lock(
        self, monkeypatch, tmp_path
    ):
        temp_dir = tmp_path / "exe_temp"
        locked_file = temp_dir / "data" / "sample.nc"
        locked_file.parent.mkdir(parents=True)
        locked_file.write_text("payload", encoding="utf-8")

        real_rmtree = shutil.rmtree
        attempts: list[Path] = []
        delays: list[float] = []
        gc_calls: list[int] = []

        def fake_rmtree(path, onerror=None):
            attempts.append(Path(path))
            if len(attempts) < 3:
                exc = PermissionError(
                    32,
                    "The process cannot access the file because it is being used by another process",
                    str(locked_file),
                )
                exc.winerror = 32
                raise exc
            real_rmtree(path, onerror=onerror)

        monkeypatch.setattr(
            "aa_recipe_manager.executor.sequential.shutil.rmtree", fake_rmtree
        )
        monkeypatch.setattr(
            "aa_recipe_manager.executor.sequential.time.sleep",
            lambda delay: delays.append(delay),
        )
        monkeypatch.setattr(
            "aa_recipe_manager.executor.sequential.gc.collect",
            lambda: gc_calls.append(1),
        )

        SequentialExecutor._cleanup_temp_dir(temp_dir)

        assert len(attempts) == 3
        assert attempts == [temp_dir, temp_dir, temp_dir]
        assert delays == [0.25, 0.5]
        assert len(gc_calls) == 2
        assert not temp_dir.exists()


# ---------------------------------------------------------------------------
# Artifact regeneration (regenerate attribute + --regenerate mode)
# ---------------------------------------------------------------------------


class TestRegenerate:
    @staticmethod
    def _images_dir(tmp_path: Path) -> Path:
        # outputs_dir defaults to a sibling of output_dir named "outputs".
        return tmp_path / "outputs" / "images"

    def test_sink_if_missing_skips_when_present_regenerates_when_gone(
        self, helper_module, tmp_path
    ):
        dag = _regen_pipeline_dag(sink_regen="if-missing")
        out = tmp_path / "ckpt"
        img = self._images_dir(tmp_path) / "plot.png"

        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert img.exists()
        helper_module.call_log.clear()

        # Image present -> the sink's marker hit stands (skipped).
        second = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert "plot" in second.skipped_steps
        assert helper_module.call_log == []
        helper_module.call_log.clear()

        # Delete the image -> the sink regenerates it; upstream loads from cache.
        img.unlink()
        third = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert third.executed_steps == ["plot"]
        assert "compute" in third.skipped_steps
        assert img.exists()
        assert [n for n, _ in helper_module.call_log] == ["plotting_sink"]

    def test_data_step_if_missing_recomputes_when_artifact_gone(
        self, helper_module, tmp_path
    ):
        dag = _regen_pipeline_dag(data_regen="if-missing")
        out = tmp_path / "ckpt"
        compute_img = self._images_dir(tmp_path) / "compute.png"

        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert compute_img.exists()
        helper_module.call_log.clear()

        # Artifact present -> nothing re-runs (plot marker hit prunes compute).
        second = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert "compute" not in second.executed_steps
        assert helper_module.call_log == []
        helper_module.call_log.clear()

        # Delete compute's artifact -> the data step recomputes (fires even
        # though its only consumer, the sink, is a cache hit).
        compute_img.unlink()
        third = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert "compute" in third.executed_steps
        assert compute_img.exists()
        assert [n for n, _ in helper_module.call_log] == ["data_with_artifact"]

    def test_regenerate_all_forces_sink_and_data_step(
        self, helper_module, tmp_path
    ):
        dag = _regen_pipeline_dag()  # no per-step attributes
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        helper_module.call_log.clear()

        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            regenerate="all",
            checkpoint_mode="eager",
        )
        names = [n for n, _ in helper_module.call_log]
        assert "data_with_artifact" in names  # data step recomputed
        assert "plotting_sink" in names       # sink re-rendered
        assert "compute" in result.executed_steps
        assert "plot" in result.executed_steps

    def test_regenerate_off_skips_even_when_artifacts_missing(
        self, helper_module, tmp_path
    ):
        dag = _regen_pipeline_dag(sink_regen="if-missing", data_regen="if-missing")
        out = tmp_path / "ckpt"
        images = self._images_dir(tmp_path)
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        (images / "plot.png").unlink()
        (images / "compute.png").unlink()
        helper_module.call_log.clear()

        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            regenerate="off",
            checkpoint_mode="eager",
        )
        # 'off' overrides the per-step if-missing: nothing regenerates.
        assert helper_module.call_log == []
        assert "compute" not in result.executed_steps
        assert "plot" not in result.executed_steps

    def test_invalid_regenerate_mode_raises(self, helper_module, tmp_path):
        dag = _regen_pipeline_dag()
        with pytest.raises(ValueError, match="regenerate must be one of"):
            SequentialExecutor().execute(
                dag,
                inputs={"seed": 1},
                output_dir=tmp_path / "ckpt",
                regenerate="bogus",
            )


# ---------------------------------------------------------------------------
# Checkpointing
# ---------------------------------------------------------------------------


class TestCheckpointing:
    def test_skips_steps_on_second_run(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        executor = SequentialExecutor()
        first = executor.execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert first.executed_steps == ["start", "first", "second"]
        assert first.skipped_steps == []
        helper_module.call_log.clear()

        second = executor.execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert second.pruned_steps == ["start", "first"]
        assert second.skipped_steps == ["second"]
        assert second.executed_steps == []
        assert helper_module.call_log == []
        assert second.outputs["second"]["out"] == 4

    def test_force_re_executes_all_steps(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        helper_module.call_log.clear()
        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            force=True,
            checkpoint_mode="eager",
        )
        assert result.executed_steps == ["start", "first", "second"]
        assert len(helper_module.call_log) == 3

    def test_recipe_description_change_preserves_cache(self, helper_module, tmp_path):
        # Per-step hashing: an edit to recipe-level metadata that does not
        # affect any step (e.g. the description) must NOT invalidate caches.
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        helper_module.call_log.clear()

        modified = dag.model_copy(deep=True)
        modified.recipe.description = "now different"
        result = SequentialExecutor().execute(
            modified,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="eager",
        )
        assert result.pruned_steps == ["start", "first"]
        assert result.skipped_steps == ["second"]
        assert result.executed_steps == []
        assert helper_module.call_log == []

    def test_editing_late_step_reuses_upstream_cache(self, helper_module, tmp_path):
        # The core caching fix: editing a parameter on the LAST step must only
        # re-run that step (and any descendants), reusing upstream checkpoints.
        dag = _linear_multiply_dag(factor=2)
        out = tmp_path / "ckpt"
        first_run = SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        assert first_run.executed_steps == ["start", "first", "scale"]
        helper_module.call_log.clear()

        # Edit only the last step's param, exactly like changing min_samples.
        modified = dag.model_copy(deep=True)
        modified.nodes["scale"].step.params["factor"] = 3
        modified.nodes["scale"].resolved_params["factor"] = 3

        result = SequentialExecutor().execute(
            modified,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="eager",
        )
        assert result.pruned_steps == ["start"]
        assert result.skipped_steps == ["first"]
        assert result.executed_steps == ["scale"]
        assert [name for name, _ in helper_module.call_log] == ["multiply"]
        # start(1)->2, first 2*2=4, scale 4*3 = 12
        assert result.outputs["scale"]["out"] == 12

    def test_editing_upstream_step_invalidates_descendants(
        self, helper_module, tmp_path
    ):
        # Editing an early step must invalidate that step and everything
        # downstream of it.
        dag = _linear_multiply_dag(factor=2)
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        helper_module.call_log.clear()

        modified = dag.model_copy(deep=True)
        modified.nodes["first"].step.params["factor"] = 5
        modified.nodes["first"].resolved_params["factor"] = 5

        result = SequentialExecutor().execute(
            modified,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="eager",
        )
        assert result.skipped_steps == ["start"]
        assert result.executed_steps == ["first", "scale"]

    def test_prunes_uncached_upstream_before_checkpoint_frontier(
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

        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        assert result.pruned_steps == ["start"]
        assert result.skipped_steps == ["first"]
        assert result.executed_steps == ["second"]
        assert [call[0] for call in helper_module.call_log] == ["add_one"]

    def test_unchanged_late_step_still_runs_uncached_parent_when_needed(
        self, helper_module, tmp_path
    ):
        dag = _linear_multiply_dag(factor=2)
        _set_step_checkpoint(dag, "start", "always")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        helper_module.call_log.clear()

        modified = dag.model_copy(deep=True)
        modified.nodes["scale"].step.params["factor"] = 3
        modified.nodes["scale"].resolved_params["factor"] = 3

        result = SequentialExecutor().execute(
            modified,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        assert result.pruned_steps == []
        assert result.skipped_steps == ["start"]
        assert result.executed_steps == ["first", "scale"]
        assert [call[0] for call in helper_module.call_log] == [
            "multiply",
            "multiply",
        ]
        assert any(
            "resume frontier limited by uncheckpointed step(s): first" in log
            for log in result.logs
        )

    def test_directory_input_hash_changes_when_file_set_changes(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        dag = _path_input_dag(str(raw_dir))

        original = compute_step_hashes(dag, {"raw_dir": str(raw_dir)})
        (raw_dir / "a.raw").write_text("alpha", encoding="utf-8")
        changed = compute_step_hashes(dag, {"raw_dir": str(raw_dir)})

        assert original["probe"] != changed["probe"]

    def test_directory_input_hash_changes_when_entry_mtime_changes(self, tmp_path):
        raw_dir = tmp_path / "raw"
        raw_dir.mkdir()
        raw_file = raw_dir / "a.raw"
        raw_file.write_text("alpha", encoding="utf-8")
        dag = _path_input_dag(str(raw_dir))

        original = compute_step_hashes(dag, {"raw_dir": str(raw_dir)})
        entry_stat = raw_file.stat()
        next_mtime = entry_stat.st_mtime_ns + 1_000_000_000
        os.utime(raw_file, ns=(next_mtime, next_mtime))
        changed = compute_step_hashes(dag, {"raw_dir": str(raw_dir)})

        assert original["probe"] != changed["probe"]


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
        manager = CheckpointManager(out, {"open_raw": "hash"}, preferred_format="netcdf")
        manager.save("open_raw", {"echodata": EchoData("vendor-specific")})
        meta = _meta_for_step(out, "open_raw")

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
        manager = CheckpointManager(out, {"open_raw": "hash"})  # default = zarr
        manager.save("open_raw", {"echodata": EchoData("zarr-payload")})
        meta = _meta_for_step(out, "open_raw")

        assert meta["outputs"]["echodata"]["format"] == "echodata_zarr"
        # Meta paths are stored relative to the entry's <hash> dir and start
        # with the writing run's run_id segment: <run_id>/<category>/<file>.
        entry_path = Path(meta["outputs"]["echodata"]["path"])
        assert entry_path.parent.name == ZARR_DATA_DIR
        assert entry_path.parts[0] == meta["run_id"]
        assert _artifact_path(out, meta, "echodata").exists()
        assert EchoData.last_zarr_kwargs["zarr_format"] == 2
        assert manager.has_checkpoint("open_raw")

        loaded = manager.load("open_raw")
        assert loaded["echodata"].__class__.__name__ == "EchoData"
        assert loaded["echodata"].payload == "zarr-payload"

    def test_netcdf_checkpoint_still_loads_xarray_dataset(self, tmp_path):
        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, {"dataset_step": "hash"}, preferred_format="netcdf")
        ds = xr.Dataset({"value": ("x", [1, 2, 3])})
        manager.save("dataset_step", {"ds": ds})

        meta = _meta_for_step(out, "dataset_step")
        assert meta["outputs"]["ds"]["format"] == "netcdf"
        assert Path(meta["outputs"]["ds"]["path"]).parent.name == OTHER_DATA_DIR
        assert _artifact_path(out, meta, "ds").exists()

        loaded = manager.load("dataset_step")
        assert list(loaded["ds"]["value"].values) == [1, 2, 3]

    def test_zarr_checkpoint_round_trip_xarray_dataset(self, tmp_path):
        """xarray Dataset saved with zarr (default) reloads correctly."""
        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, {"sv_step": "hash"})  # default = zarr
        ds = xr.Dataset({"value": ("x", [10, 20, 30])})
        manager.save("sv_step", {"ds_Sv": ds})

        meta = _meta_for_step(out, "sv_step")
        assert meta["outputs"]["ds_Sv"]["format"] == "zarr"
        assert Path(meta["outputs"]["ds_Sv"]["path"]).parent.name == ZARR_DATA_DIR
        store = _artifact_path(out, meta, "ds_Sv")
        zgroup = json.loads((store / ".zgroup").read_text(encoding="utf-8"))
        assert zgroup["zarr_format"] == 2
        assert manager.has_checkpoint("sv_step")

        loaded = manager.load("sv_step")
        assert list(loaded["ds_Sv"]["value"].values) == [10, 20, 30]

    def test_zarr_checkpoint_round_trip_xarray_dataarray(self, tmp_path):
        """xarray DataArray saved with zarr (default) reloads as DataArray."""
        out = tmp_path / "ckpt"
        manager = CheckpointManager(out, {"da_step": "hash"})  # default = zarr
        da = xr.DataArray([1.0, 2.0, 3.0], dims=["x"], name="sig")
        manager.save("da_step", {"arr": da})

        meta = _meta_for_step(out, "da_step")
        assert meta["outputs"]["arr"]["format"] == "zarr_da"
        assert Path(meta["outputs"]["arr"]["path"]).parent.name == ZARR_DATA_DIR
        store = _artifact_path(out, meta, "arr")
        zgroup = json.loads((store / ".zgroup").read_text(encoding="utf-8"))
        assert zgroup["zarr_format"] == 2

        loaded = manager.load("da_step")
        import xarray as xr2
        assert isinstance(loaded["arr"], xr2.DataArray)
        assert list(loaded["arr"].values) == [1.0, 2.0, 3.0]

    def test_checkpoint_artifact_stem_is_output_name(self):
        # The enclosing <step_id>/<hash>/<run_id>/ path identifies the step, so
        # the artifact filename is just the output name (keeps paths short).
        assert _checkpoint_artifact_stem("ds_ml_ready") == "ds_ml_ready"


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
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        from aa_recipe_manager.executor import entry_dir_parts

        manager = CheckpointManager(out, compute_step_hashes(dag))
        removed = manager.clean(dag)
        # Removal unit is the whole entry dir for each intermediate step
        # (addressed by the truncated-hash key; hashes as written by the run,
        # i.e. with its inputs).
        hashes = compute_step_hashes(dag, {"seed": 1})
        removed_names = {p.name for p in removed}
        assert entry_dir_parts("start", hashes["start"])[1] in removed_names
        assert entry_dir_parts("first", hashes["first"])[1] in removed_names
        assert entry_dir_parts("second", hashes["second"])[1] not in removed_names
        assert _meta_names(out) == {"second"}

    def test_clean_all_removes_everything(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        manager = CheckpointManager(out, compute_step_hashes(dag))
        manager.clean(dag, mode="all")
        assert not _meta_names(out)

    def test_clean_stale_only_removes_other_hashes(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        # Trick the manager into thinking we're on a different step hash.
        manager = CheckpointManager(out, {s: "different-hash" for s in compute_step_hashes(dag)})
        removed = manager.clean(dag, mode="stale")
        assert len(removed) > 0
        # And running stale clean again should be a no-op.
        manager_same = CheckpointManager(out, compute_step_hashes(dag))
        # Now files for current hash are gone, so stale clean finds nothing
        # whose hash matches the manager's own.
        assert manager_same.clean(dag, mode="stale") == []

    def test_clean_dry_run_does_not_remove(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        manager = CheckpointManager(out, compute_step_hashes(dag))
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
    assert "recipe_hash" in content
    assert "resolved_dependencies" in content
    assert "resolved_steps" not in content


def test_api_execute_writes_provenance_to_outputs_dir(helper_module, tmp_path):
    """provenance.yaml is written to outputs/provenance/ alongside logs."""
    from unittest.mock import patch

    from aa_recipe_manager import api

    dag = _linear_inc_dag()
    with patch("aa_recipe_manager.api._load_dag", return_value=dag):
        result = api.execute(
            dag.recipe,
            inputs={"seed": 1},
            output_dir=str(tmp_path / "ckpt"),
        )
    assert result.outputs_dir is not None
    prov_path = result.outputs_dir / "provenance" / "provenance.yaml"
    assert prov_path.exists(), f"provenance.yaml not found at {prov_path}"
    content = prov_path.read_text(encoding="utf-8")
    assert "recipe_hash" in content
    assert "python_version_number" in content
    assert "resolved_dependencies" in content
    # inputs supplied at runtime are recorded
    assert "seed" in content
    # resolved_steps is excluded from the YAML sidecar
    assert "resolved_steps" not in content



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
    def test_default_mode_only_checkpoints_marked_steps(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        policy = resolve_checkpoint_policy(dag)
        assert policy == {"first"}

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

    def test_explicit_checkpoint_steps_helper_includes_save(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        _set_step_checkpoint(dag, "start", "save")
        _set_step_checkpoint(dag, "second", "never")
        assert explicit_checkpoint_steps(dag) == {"first", "start"}

    def test_save_checkpoints_under_explicit_mode(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "save")
        policy = resolve_checkpoint_policy(dag, mode="explicit")
        assert policy == {"first"}

    def test_save_respects_none_mode(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        _set_step_checkpoint(dag, "start", "save")
        # "always" forces save under none, but "save" respects it
        policy = resolve_checkpoint_policy(dag, mode="none")
        assert policy == {"first"}

    def test_save_under_eager_mode(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "save")
        policy = resolve_checkpoint_policy(dag, mode="eager")
        # Under eager, everything gets checkpointed including "save"
        assert policy == {"start", "first", "second"}

    def test_save_under_terminal_mode_only_if_terminal(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "save")  # intermediate step
        _set_step_checkpoint(dag, "second", "save")  # terminal step
        policy = resolve_checkpoint_policy(dag, mode="terminal")
        # Only terminal steps get checkpointed, so only "second"
        assert policy == {"second"}

    def test_mixed_always_save_never(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "start", "always")
        _set_step_checkpoint(dag, "first", "save")
        _set_step_checkpoint(dag, "second", "never")
        policy = resolve_checkpoint_policy(dag, mode="explicit")
        # "always" and "save" are included, "never" blocks second
        assert policy == {"start", "first"}

    def test_save_with_none_mode_and_ad_hoc_pins(self):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "start", "save")
        policy = resolve_checkpoint_policy(
            dag, mode="none", extra_step_ids={"first"}
        )
        # Ad-hoc pin forces "first", "save" is excluded under none
        assert policy == {"first"}


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
        assert meta_files == {"first"}

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
        assert meta_files == {"second"}

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
        assert meta_files == {"start"}

    def test_save_writes_under_explicit_mode(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "save")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        meta_files = _meta_names(out)
        assert meta_files == {"first"}

    def test_save_respects_none_mode_integration(self, helper_module, tmp_path):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "save")
        _set_step_checkpoint(dag, "start", "always")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="none",
        )
        meta_files = _meta_names(out)
        # "always" forces save, "save" respects none
        assert meta_files == {"start"}

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
        # Second run: "first" is the checkpoint frontier. "start" is pruned,
        # "first" is loaded from cache, and only "second" executes.
        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=out,
            checkpoint_mode="explicit",
        )
        assert "first" in result.skipped_steps
        assert "start" in result.pruned_steps
        assert "second" in result.executed_steps
        names = [c[0] for c in helper_module.call_log]
        assert names == ["add_one"]

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
        assert meta_files == {"second"}


class TestCleanRespectsExplicitMarks:
    def test_intermediate_clean_preserves_explicit_checkpoint(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        out = tmp_path / "ckpt"
        # Eager run writes all three; "first" is also explicitly marked.
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        manager = CheckpointManager(out, compute_step_hashes(dag))
        manager.clean(dag, mode="intermediate")
        remaining = _meta_names(out)
        # "second" (terminal) and "first" (explicitly marked) both survive.
        assert remaining == {"first", "second"}

    def test_all_mode_still_removes_explicit_checkpoints(
        self, helper_module, tmp_path
    ):
        dag = _linear_inc_dag()
        _set_step_checkpoint(dag, "first", "always")
        out = tmp_path / "ckpt"
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
        )
        manager = CheckpointManager(out, compute_step_hashes(dag))
        manager.clean(dag, mode="all")
        assert not _meta_names(out)
