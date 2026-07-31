# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Stage 9a: the Dask executor backend.

The threaded local cluster is the default and the bulk of these tests; one test
escalates a step to a worker *process* (guarded so it is skipped where a process
cluster cannot start). Each ``DaskExecutor().execute`` spins up and tears down
its own local cluster, so the suite keeps the Dask test count small.
"""

from __future__ import annotations

import logging
import os

import pytest

import _stage9_helpers as H

pytest.importorskip("dask.distributed")

# Keep the distributed cluster's INFO chatter out of the test output.
for _name in ("distributed", "distributed.scheduler", "distributed.worker",
              "distributed.nanny", "distributed.core"):
    logging.getLogger(_name).setLevel(logging.ERROR)

from aa_recipe_manager.executor import SequentialExecutor  # noqa: E402
from aa_recipe_manager.executor.dask_executor import DaskExecutor  # noqa: E402


def _dask(threads_per_worker=4, **kw):
    return DaskExecutor(threads_per_worker=threads_per_worker, **kw)


def test_map_collect_matches_sequential():
    dag_seq = H.build(H.map_collect_steps())
    dag_dask = H.build(H.map_collect_steps())
    seq = SequentialExecutor().execute(dag_seq)
    dsk = _dask().execute(dag_dask)
    assert seq.outputs["merge"]["total"] == dsk.outputs["merge"]["total"] == 63
    assert sorted(dsk.outputs["proc"]["out"]) == [11, 21, 31]


def test_branches_run_concurrently():
    # Three independent probe steps fan out of 'start'; on a threaded cluster
    # they overlap, so the recorded peak concurrency is >= 2.
    H.reset_overlap()
    steps = [
        H.step("start", "const7", out_ports={"v": H.INT},
               output_map={"v": "__return__"}),
    ]
    for name in ("a", "b", "c"):
        steps.append(
            H.step(name, "overlap_probe", inputs={"x": "${start.v}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"})
        )
    steps.append(
        H.step("peak", "const7", inputs={}, params={},
               out_ports={"v": H.INT}, output_map={"v": "__return__"},
               in_ports={})
    )
    # depends_on so 'peak' runs after the probes and reads the recorded max.
    steps[-1].depends_on = ["a", "b", "c"]
    _dask(threads_per_worker=3).execute(H.build(steps))
    assert H.max_overlap() >= 2


def test_per_instance_checkpoints_written_once_then_resumed(tmp_path):
    cache = str(tmp_path / "cache")
    first = _dask().execute(
        H.build(H.map_collect_steps()), user_cache_dir=cache, checkpoint_mode="eager"
    )
    assert first.outputs["merge"]["total"] == 63
    assert "proc" in first.executed_steps  # mapped instances computed + cached
    # Resume: the cached collector prunes the whole upstream, so nothing reruns.
    second = _dask().execute(
        H.build(H.map_collect_steps()), user_cache_dir=cache, checkpoint_mode="eager"
    )
    assert second.executed_steps == []
    assert second.outputs["merge"]["total"] == 63
    assert "proc" in second.pruned_steps


def test_custom_fan_in_carries_no_merge_assumption():
    # Collector 'concat' assembles a list, not a sum: collect is op-agnostic.
    steps = H.map_collect_steps(collector_callable="concat")
    # concat returns a list; declare the collector output accordingly.
    steps[-1].custom_spec.outputs = {"total": H.PortDeclaration(type="list")}
    dsk = _dask().execute(H.build(steps))
    assert sorted(dsk.outputs["merge"]["total"]) == [11, 21, 31]


def test_checkpointed_results_match_sequential(tmp_path):
    seq = SequentialExecutor().execute(
        H.build(H.diamond_steps()),
        user_cache_dir=str(tmp_path / "seq"),
        checkpoint_mode="eager",
    )
    dsk = _dask().execute(
        H.build(H.diamond_steps()),
        user_cache_dir=str(tmp_path / "dask"),
        checkpoint_mode="eager",
    )
    assert seq.outputs["combine"]["out"] == dsk.outputs["combine"]["out"] == 22
    assert set(seq.executed_steps) == set(dsk.executed_steps)


@pytest.mark.slow
def test_per_step_process_escalation(tmp_path, monkeypatch):
    # Make the helper module importable inside spawned worker processes.
    monkeypatch.setenv(
        "PYTHONPATH",
        os.path.dirname(__file__) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    from aa_recipe_manager.model.types import (
        InputDeclaration,
        StepExecutionHints,
    )

    # start (checkpointed) -> branchB=pid_of runs in a worker PROCESS.
    steps = [
        H.step("start", "addk", params={"x": "${inputs.seed}", "k": 0},
               out_ports={"v": H.INT}, output_map={"v": "__return__"},
               param_decls={
                   "x": H.ParamDeclaration(type="int"),
                   "k": H.ParamDeclaration(type="int"),
               }),
        H.step("branchB", "pid_of", inputs={"x": "${start.v}"},
               in_ports={"x": H.INT},
               out_ports={"pid": H.INT, "x": H.INT},
               output_map={"pid": "pid", "x": "x"},
               execution=StepExecutionHints(dask_config={"scheduler": "processes"})),
    ]
    dag = H.build(
        steps,
        inputs_decl={"seed": InputDeclaration(type="int", default=5)},
        input_values={"seed": 5},
    )
    try:
        result = DaskExecutor(n_workers=2, threads_per_worker=1).execute(
            dag, inputs={"seed": 5},
            user_cache_dir=str(tmp_path / "cache"), checkpoint_mode="eager",
        )
    except Exception as exc:  # a locked-down box may forbid spawning
        pytest.skip(f"process cluster unavailable: {exc}")
    assert result.outputs["branchB"]["x"] == 5
    # The step ran in a different process than the test.
    assert result.outputs["branchB"]["pid"] != os.getpid()


def test_process_escalation_rejects_uncheckpointed_heavy_input():
    # branchB wants a process but its upstream 'start' is not checkpointed and
    # carries a non-JSON-native value -> fail fast with a helpful message.
    from aa_recipe_manager.exceptions import PipelineExecutionError
    from aa_recipe_manager.model.types import StepExecutionHints

    class _Obj:
        pass

    # Register a callable that returns a heavy (non-native) object.
    H._HEAVY = _Obj  # type: ignore[attr-defined]
    setattr(H, "make_heavy", lambda: _Obj())

    steps = [
        H.step("start", "make_heavy", out_ports={"v": H.PortDeclaration(type="obj")},
               output_map={"v": "__return__"}),
        H.step("use", "pid_of", inputs={"x": "${start.v}"},
               in_ports={"x": H.PortDeclaration(type="obj")},
               out_ports={"pid": H.INT, "x": H.INT},
               output_map={"pid": "pid", "x": "x"},
               execution=StepExecutionHints(dask_config={"scheduler": "processes"})),
    ]
    # No user_cache_dir -> 'start' is not checkpointed -> heavy ValueRef -> reject.
    with pytest.raises(PipelineExecutionError, match="worker \\*process\\*"):
        _dask().execute(H.build(steps), no_checkpoints=True)


def test_every_mapped_instance_logs_reach_the_run_log(tmp_path):
    # Regression: instances ran as concurrent threads and each entered a global
    # ``redirect_stdout``, so all but one instance's output was lost. A 3-file
    # fan-out produced a single line in the run log.
    #
    # Whether output is *lost* depends on how the threads interleave, so the
    # deterministic guard for that race lives in test_log_capture.py; this test
    # covers the end-to-end wiring — labeling, ordering, and the chain's own
    # completion line — which is what makes a fan-out log readable at all.
    dag = H.build(H.map_collect_steps(mapped_callable="noisy"))
    result = _dask(threads_per_worker=3).execute(
        dag, outputs_dir=str(tmp_path / "outputs")
    )
    log = result.console_log
    for value in (10, 20, 30):
        assert f"noisy-start x={value}" in log
        assert f"noisy-end x={value}" in log
    # Each instance's block is fenced with the instance it came from, so an
    # interleaved fan-out stays attributable.
    for i in (1, 2, 3):
        assert f"proc [instance {i}/3]" in log
    # And the chain reports its own completion, like a plain step does.
    assert "--- proc: done (" in log
    assert "3 instance(s): 3 computed, 0 cached" in log


def test_dask_workers_is_the_concurrency_not_a_multiplier():
    # Regression: n_workers x threads_per_worker meant '--dask-workers 4' opened
    # 4 x CPU_COUNT slots (56 on a 14-core box). A slot holds a whole chain
    # instance in memory, so the overshoot is a memory hazard, not just untidy.
    from aa_recipe_manager.executor.engine.backends.dask import DaskBackend

    backend = DaskBackend(None, n_workers=3)
    client = backend._client_for("threads")
    try:
        assert backend._slot_count(client) == 3
    finally:
        backend.close()
