# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for global storage_options reaching ops and remote-input fingerprinting.

``memory://`` stands in for ``gs://`` (a credential-free non-local fsspec
filesystem), matching the convention in ``test_storage.py``.
"""

from __future__ import annotations

import json
import sys
import types

import fsspec
import fsspec.core

from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.executor.checkpoint import (
    _path_fingerprint,
    _remote_path_fingerprint,
    compute_step_hashes,
)
from aa_recipe_manager.executor.runtime_context import (
    execution_context,
    get_execution_context,
)
from aa_recipe_manager.model.types import (
    DAGNode,
    Implementation,
    InputDeclaration,
    PipelineDAG,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)

from test_executor import _dep, _make_dag

_OBSERVE_MODULE = "ar_storage_options_observe_helper"


def _install_observe_module() -> types.ModuleType:
    """Register a callable that records the ambient execution storage_options."""
    module = sys.modules.get(_OBSERVE_MODULE)
    if module is None:
        module = types.ModuleType(_OBSERVE_MODULE)

        def observe(x):
            ctx = get_execution_context()
            module.observed.append(getattr(ctx, "storage_options", "MISSING"))  # type: ignore[attr-defined]
            return x

        module.observe = observe  # type: ignore[attr-defined]
        sys.modules[_OBSERVE_MODULE] = module
    module.observed = []  # type: ignore[attr-defined]
    return module


def _observe_dag() -> PipelineDAG:
    spec = Spec(
        op="observe",
        description="",
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    impl = Implementation(
        op="observe",
        key="d",
        callable_path=f"{_OBSERVE_MODULE}.observe",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    node = DAGNode(
        step=Step(id="only", op="observe", inputs={"x": "${inputs.seed}"}),
        spec=spec,
        implementation=impl,
    )
    return _make_dag([node], [])


def _remote_fingerprint_dag() -> PipelineDAG:
    """Single step referencing a path input declared with fingerprint_mode."""
    spec = Spec(
        op="observe",
        description="",
        inputs={"x": PortDeclaration(type="path")},
        outputs={"out": PortDeclaration(type="int")},
    )
    impl = Implementation(
        op="observe",
        key="d",
        callable_path=f"{_OBSERVE_MODULE}.observe",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    node = DAGNode(
        step=Step(id="only", op="observe", inputs={"x": "${inputs.raw_dir}"}),
        spec=spec,
        implementation=impl,
    )
    recipe = Recipe(
        name="remote_fp",
        version="1.0.0",
        schema_version="1",
        inputs={
            "raw_dir": InputDeclaration(
                type="path", fingerprint_mode="auto", default="memory://bkt/raw"
            )
        },
        steps=[node.step],
    )
    return PipelineDAG(
        recipe=recipe,
        nodes={node.step.id: node},
        edges=[],
        topological_order=[node.step.id],
    )


# --- ExecutionContext.storage_options -------------------------------------


def test_execution_context_storage_options_default_none():
    assert get_execution_context().storage_options is None


def test_execution_context_publishes_and_resets():
    assert get_execution_context().storage_options is None
    with execution_context(storage_options={"token": "x"}) as ctx:
        assert ctx.storage_options == {"token": "x"}
        assert get_execution_context().storage_options == {"token": "x"}
    assert get_execution_context().storage_options is None


def test_execution_context_empty_dict_normalized_to_none():
    with execution_context(storage_options={}) as ctx:
        assert ctx.storage_options is None


# --- executor publishes storage_options to ops ----------------------------


def test_executor_publishes_storage_options_to_ops():
    module = _install_observe_module()
    SequentialExecutor().execute(
        _observe_dag(),
        inputs={"seed": 1},
        no_checkpoints=True,
        storage_options={"marker": 1},
    )
    assert module.observed == [{"marker": 1}]


def test_executor_defaults_storage_options_to_none():
    module = _install_observe_module()
    SequentialExecutor().execute(
        _observe_dag(), inputs={"seed": 1}, no_checkpoints=True
    )
    assert module.observed == [None]


# --- remote fingerprint threading -----------------------------------------


def test_remote_path_fingerprint_threads_storage_options(monkeypatch):
    captured: dict = {}
    real = fsspec.core.url_to_fs

    def fake(path, **kwargs):
        captured["kwargs"] = kwargs
        return real(path)

    monkeypatch.setattr(fsspec.core, "url_to_fs", fake)
    _remote_path_fingerprint("memory://bkt/x", storage_options={"probe": 1})
    assert captured["kwargs"] == {"probe": 1}


def test_storage_options_absent_from_fingerprint():
    fs = fsspec.filesystem("memory")
    fs.pipe_file("/fpdir/f.raw", b"x")
    without = _remote_path_fingerprint("memory://fpdir")
    with_opts = _remote_path_fingerprint("memory://fpdir", storage_options={"token": "secret"})
    assert without == with_opts
    assert "token" not in json.dumps(without)


def test_path_fingerprint_forwards_options(monkeypatch):
    captured: dict = {}
    real = fsspec.core.url_to_fs
    monkeypatch.setattr(
        fsspec.core,
        "url_to_fs",
        lambda path, **kwargs: (captured.setdefault("kwargs", kwargs), real(path))[1],
    )
    _path_fingerprint("memory://bkt/y", storage_options={"probe": 2})
    assert captured["kwargs"] == {"probe": 2}


# --- compute_step_hashes --------------------------------------------------


def test_compute_step_hashes_local_stable_regardless_of_options():
    dag = _remote_fingerprint_dag()
    # A local input path: storage_options must not change the resulting hash.
    inputs = {"raw_dir": "./some/local/dir"}
    assert compute_step_hashes(dag, inputs) == compute_step_hashes(
        dag, inputs, storage_options={"token": "x"}
    )


def test_compute_step_hashes_backward_compatible_positional():
    dag = _remote_fingerprint_dag()
    # Two-positional-arg call (no storage_options) still works.
    hashes = compute_step_hashes(dag, {"raw_dir": "./local"})
    assert set(hashes) == {"only"}


def test_compute_step_hashes_threads_options_for_remote_input(monkeypatch):
    captured: list = []
    real = fsspec.core.url_to_fs
    monkeypatch.setattr(
        fsspec.core,
        "url_to_fs",
        lambda path, **kwargs: (captured.append(kwargs), real(path))[1],
    )
    dag = _remote_fingerprint_dag()
    compute_step_hashes(
        dag, {"raw_dir": "memory://bkt/raw"}, storage_options={"probe": 7}
    )
    assert {"probe": 7} in captured
