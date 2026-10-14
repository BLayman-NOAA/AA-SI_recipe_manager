# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Cancelling a run between units and between mapped instances.

That a write killed part way leaves no entry ``has_checkpoint`` accepts is
covered by ``tests/test_concurrent_cache.py::
test_sidecar_last_makes_partial_write_invisible_across_managers``: the sidecar
is the commit point and is written last.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _stage9_helpers as H
from aa_recipe_manager.cli import main
from aa_recipe_manager.events import CancelFileWatcher, CancelToken, MemoryEventSink
from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.executor.checkpoint import (
    CheckpointManager,
    compute_step_hashes,
)

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"


@pytest.fixture
def token():
    token = CancelToken()
    H.CANCEL_HOOK["token"] = token
    H.reset_counts()
    yield token
    H.CANCEL_HOOK.clear()


def _linear():
    return H.build(
        [
            H.step("start", "const7", out_ports={"v": H.INT},
                   output_map={"v": "__return__"}),
            H.step("trip", "trip_cancel", inputs={"x": "${start.v}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"}),
            H.step("after", "inc", inputs={"x": "${trip.out}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"}),
        ]
    )


def _assert_stopped_after_one_instance(result, cache: Path) -> None:
    assert H.call_count() == 1
    assert result.status == "cancelled"
    assert "proc" not in result.step_dispositions
    assert "merge" not in result.step_dispositions
    assert len(list((cache / "proc").glob("*/meta.json"))) == 1


def test_cancel_between_steps_keeps_finished_checkpoints(tmp_path, token):
    dag = _linear()
    cache = tmp_path / "cache"
    sink = MemoryEventSink()
    result = SequentialExecutor().execute(
        dag, user_cache_dir=cache, checkpoint_mode="eager", cancel=token, events=sink
    )
    assert result.executed_steps == ["start", "trip"]
    assert "after" not in result.step_dispositions
    assert result.status == "cancelled"
    assert "cancelled: 1 unit(s) not finished" in result.logs

    manager = CheckpointManager(cache, compute_step_hashes(dag))
    assert manager.has_checkpoint("start")
    assert manager.has_checkpoint("trip")
    assert not manager.has_checkpoint("after")

    manifest = json.loads(Path(result.manifest_file).read_text(encoding="utf-8"))
    assert manifest["status"] == "cancelled"
    assert sink.events[-1].kind == "run_finished"
    assert sink.events[-1].data["status"] == "cancelled"
    assert sink.of("warning")[-1].data["message"].startswith("cancelled:")


def test_cancel_during_mapped_step_stops_before_next_instance(tmp_path, token):
    dag = H.build(H.map_collect_steps(mapped_callable="trip_cancel"))
    cache = tmp_path / "cache"
    result = SequentialExecutor().execute(
        dag, user_cache_dir=cache, checkpoint_mode="eager", cancel=token
    )
    _assert_stopped_after_one_instance(result, cache)


def test_cancel_before_start_runs_nothing(tmp_path, token):
    token.cancel("early")
    sink = MemoryEventSink()
    result = SequentialExecutor().execute(
        _linear(), user_cache_dir=tmp_path / "cache", cancel=token, events=sink
    )
    assert result.executed_steps == []
    assert result.status == "cancelled"
    kinds = [e.kind for e in sink.events]
    assert kinds[:2] == ["run_started", "plan"]
    assert kinds[-1] == "run_finished"


def test_cancel_file_watcher_sets_token(tmp_path):
    path = tmp_path / "stop"
    token = CancelToken()
    with CancelFileWatcher(path, token, interval=0.02):
        assert not token.is_set()
        path.write_text("")
        assert token.wait(2)
    assert "stop" in token.reason


def test_cancel_file_present_at_start_cancels_at_once(tmp_path):
    path = tmp_path / "stop"
    path.write_text("")
    token = CancelToken()
    watcher = CancelFileWatcher(path, token, interval=60)
    watcher.start()
    assert token.is_set()
    watcher.stop()


def test_cli_cancel_file(cli_runner, tmp_path):
    stop = tmp_path / "stop"
    stop.write_text("")
    ran = cli_runner.invoke(
        main,
        [
            "run", str(FIXTURES / "flat.yaml"),
            "--user-cache-dir", str(tmp_path / "cache"),
            "--cancel-file", str(stop),
        ],
    )
    assert ran.exit_code == 2, ran.output
    assert "Run cancelled: 0 step(s) finished before the stop." in ran.output


def test_dask_threads_cancel_matches_sequential(tmp_path, token):
    pytest.importorskip("dask.distributed")
    from aa_recipe_manager.executor.dask_executor import DaskExecutor

    dag = H.build(H.map_collect_steps(mapped_callable="trip_cancel"))
    cache = tmp_path / "cache"
    result = DaskExecutor("threads", n_workers=1, threads_per_worker=1).execute(
        dag, user_cache_dir=cache, checkpoint_mode="eager", cancel=token
    )
    _assert_stopped_after_one_instance(result, cache)
