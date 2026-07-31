# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Stage 9: the same recipe yields identical results under every backend (FR-15.5).

Runs one map/collect recipe and one branch (diamond) recipe under the
sequential, Dask (threads and processes), and — when installed — Prefect
backends, asserting identical outputs and cache dispositions.
"""

from __future__ import annotations

import importlib.util
import logging

import pytest

import _stage9_helpers as H
from aa_recipe_manager.executor import resolve_executor

# Top-level names only: find_spec imports the parent of a dotted name, so
# probing "dask.distributed" raises rather than returning None when dask is
# absent, which would break collection of this whole module.
_HAS_DASK = importlib.util.find_spec("dask") is not None
_HAS_PREFECT = importlib.util.find_spec("prefect") is not None

for _name in ("distributed", "prefect"):
    logging.getLogger(_name).setLevel(logging.ERROR)


def _executors():
    cases = [("sequential", {})]
    if _HAS_DASK:
        cases.append(("dask", {"scheduler": "threads", "threads_per_worker": 4}))
    if _HAS_PREFECT:
        cases.append(("prefect", {}))
    return cases


_CASES = _executors()
_IDS = [c[0] for c in _CASES]


@pytest.fixture(params=_CASES, ids=_IDS)
def executor(request):
    name, options = request.param
    return name, resolve_executor(name, **options)


@pytest.mark.parametrize(
    ("name", "missing", "extra"),
    [("dask", "dask", "aa-recipe-manager[dask]"),
     ("prefect", "prefect", "aa-recipe-manager[prefect]")],
)
def test_missing_backend_names_its_install_command(monkeypatch, name, missing, extra):
    """A missing optional backend must surface the install hint, not a raw
    ModuleNotFoundError from probing a dotted submodule."""
    real_find_spec = importlib.util.find_spec

    def fake_find_spec(module, *args, **kwargs):
        # Mimic the real contract: find_spec imports the parent of a dotted
        # name, so an absent package raises there rather than returning None.
        if module.startswith(f"{missing}."):
            raise ModuleNotFoundError(f"No module named {missing!r}")
        if module == missing:
            return None
        return real_find_spec(module, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)
    with pytest.raises(ImportError) as excinfo:
        resolve_executor(name)
    assert extra in str(excinfo.value)


def test_map_collect_outputs_identical(executor):
    name, impl = executor
    result = impl.execute(H.build(H.map_collect_steps()))
    assert result.outputs["merge"]["total"] == 63, name
    assert sorted(result.outputs["proc"]["out"]) == [11, 21, 31], name


def test_diamond_outputs_and_dispositions_identical(executor, tmp_path):
    name, impl = executor
    result = impl.execute(
        H.build(H.diamond_steps()),
        user_cache_dir=str(tmp_path / name),
        checkpoint_mode="eager",
    )
    assert result.outputs["combine"]["out"] == 22, name
    # Every backend computes every step once (cold cache) and checkpoints it.
    assert set(result.executed_steps) == {
        "start", "branchA", "branchB", "combine"
    }, name
    dispositions = {
        sid: rec.disposition for sid, rec in result.step_dispositions.items()
    }
    assert all(d == "computed" for d in dispositions.values()), (name, dispositions)

    # Every step is checkpointed (eager) with no remaining consumer by the
    # time the run ends, so every backend evicts every step's output the
    # same way — the eviction path lives in the shared PipelineRunner, not
    # per-backend code, so this must hold identically across backends.
    from aa_recipe_manager.executor.lazy_outputs import LazyStepOutputs
    from aa_recipe_manager.executor.refs import CheckpointRef

    for step_id, port in (
        ("start", "v"), ("branchA", "out"), ("branchB", "out"), ("combine", "out")
    ):
        outputs = result.outputs[step_id]
        assert isinstance(outputs, LazyStepOutputs), (name, step_id)
        assert isinstance(outputs.raw(port), CheckpointRef), (name, step_id)
    assert result.outputs["start"]["v"] == 7, name
    assert result.outputs["branchA"]["out"] == 8, name
    assert result.outputs["branchB"]["out"] == 14, name


def test_custom_fan_in_identical(executor):
    name, impl = executor
    steps = H.map_collect_steps(collector_callable="concat")
    steps[-1].custom_spec.outputs = {"total": H.PortDeclaration(type="list")}
    result = impl.execute(H.build(steps))
    assert sorted(result.outputs["merge"]["total"]) == [11, 21, 31], name
