# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Stage 9 engine internals: unit graph, wavefront, closures, inline backend."""

from __future__ import annotations

import _stage9_helpers as H
from aa_recipe_manager.executor.engine.backends.inline import InlineBackend
from aa_recipe_manager.executor.engine.schedule import (
    WavefrontScheduler,
    build_unit_graph,
)
from aa_recipe_manager.executor.engine.tasks import (
    CheckpointRef,
    TaskClosure,
    ValueRef,
)

# --- unit graph -------------------------------------------------------------


def test_unit_graph_collapses_chain_and_wires_edges():
    dag = H.build(H.map_collect_steps())
    graph = build_unit_graph(dag)
    # seg (step), proc (chain), merge (step) -> 3 units.
    assert len(graph.units) == 3
    chain = next(u for u in graph.units.values() if u.is_chain)
    assert chain.member_ids == ("proc",)
    assert chain.map_source == "${seg.items}"
    # merge depends on the proc chain; the chain depends on seg.
    merge_unit = graph.unit_of_step("merge")
    proc_unit = graph.unit_of_step("proc")
    seg_unit = graph.unit_of_step("seg")
    assert proc_unit.unit_id in graph.deps[merge_unit.unit_id]
    assert seg_unit.unit_id in graph.deps[proc_unit.unit_id]


def test_multi_step_chain_is_one_unit():
    seg = H.step("seg", "make_list", out_ports={"items": H.LIST},
                 output_map={"items": "__return__"})
    m1 = H.step("read", "inc", inputs={"x": "${_item}"}, in_ports={"x": H.INT},
                out_ports={"out": H.INT}, output_map={"out": "__return__"},
                map_over="${seg.items}")
    m2 = H.step("proc", "inc", inputs={"x": "${read.out}"}, in_ports={"x": H.INT},
                out_ports={"out": H.INT}, output_map={"out": "__return__"},
                map_over="${seg.items}")
    graph = build_unit_graph(H.build([seg, m1, m2]))
    chains = [u for u in graph.units.values() if u.is_chain]
    assert len(chains) == 1
    assert chains[0].member_ids == ("read", "proc")


# --- wavefront readiness ----------------------------------------------------


def test_wavefront_orders_topologically_and_unblocks():
    dag = H.build(H.diamond_steps())
    graph = build_unit_graph(dag)
    sched = WavefrontScheduler(graph)

    ready = sched.ready()
    assert [u.first for u in ready] == ["start"]  # only start has no deps
    sched.mark_done(graph.unit_of_step("start"))

    # Both branches become ready at once (independent).
    ready = {u.first for u in sched.ready()}
    assert ready == {"branchA", "branchB"}
    for name in ("branchA", "branchB"):
        sched.mark_done(graph.unit_of_step(name))

    assert [u.first for u in sched.ready()] == ["combine"]
    sched.mark_done(graph.unit_of_step("combine"))
    assert sched.all_done()


# --- closures ---------------------------------------------------------------


class _FakeStore:
    """Minimal store double: only the given step ids report as checkpointed."""

    def __init__(self, checkpointed: set[str]):
        self._ck = checkpointed

    def has_checkpoint(self, step_id, *, instance_hash=None):
        return step_id in self._ck


def test_closure_prefers_checkpoint_ref_for_cached_upstream_cross_process():
    from aa_recipe_manager.executor.engine.runner import PipelineRunner

    # combine reads branchA.out (checkpointed) and branchB.out (not).
    dag = H.build(H.diamond_steps())
    runner = PipelineRunner(_ProcessishBackend())
    runner._dag = dag
    runner._backend = _ProcessishBackend()
    runner._checkpoints = _FakeStore({"branchA"})
    from aa_recipe_manager.executor.invocation import RuntimeContext

    rt = RuntimeContext()
    rt.record("branchA", {"out": 8})
    rt.record("branchB", {"out": 14})
    runner._runtime = rt

    closure = runner._build_closure(["combine"])
    refs = closure.refs
    assert isinstance(refs[("branchA", "out")], CheckpointRef)
    assert isinstance(refs[("branchB", "out")], ValueRef)


def test_closure_uses_value_refs_in_process():
    from aa_recipe_manager.executor.engine.runner import PipelineRunner
    from aa_recipe_manager.executor.invocation import RuntimeContext

    dag = H.build(H.diamond_steps())
    runner = PipelineRunner(InlineBackend())
    runner._dag = dag
    runner._checkpoints = _FakeStore({"branchA", "branchB"})
    rt = RuntimeContext()
    rt.record("branchA", {"out": 8})
    rt.record("branchB", {"out": 14})
    runner._runtime = rt
    closure = runner._build_closure(["combine"])
    # In-process: everything by value even when checkpointed.
    assert all(isinstance(r, ValueRef) for r in closure.refs.values())


def test_heavy_value_ref_detection():
    small = TaskClosure(refs={("a", "x"): ValueRef("a", "x", "path/to/file.raw")})
    assert small.heavy_value_ref_steps() == []

    class _Big:  # non-JSON-native stand-in for a Dataset / EchoData
        pass

    heavy = TaskClosure(refs={("b", "y"): ValueRef("b", "y", _Big())})
    assert heavy.heavy_value_ref_steps() == ["b"]


class _ProcessishBackend:
    """A backend that declares itself cross-process for closure selection."""

    in_process = False
    max_concurrency = 2


def test_collector_and_worker_saves_both_record_save_seconds(tmp_path):
    # Regression: save_seconds was only instrumented in the worker task paths
    # (run_step_task / run_chain_instance). A collector runs on the client via
    # _run_client_step, so a collector whose elapsed was almost entirely its
    # checkpoint upload (combine_raw: 264s) still reported save_seconds=0.0.
    from aa_recipe_manager.executor import SequentialExecutor

    dag = H.build(H.map_collect_steps())
    result = SequentialExecutor().execute(
        dag,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        checkpoint_mode="eager",
    )
    for sid in ("seg", "merge"):  # worker-path step and client-path collector
        rec = result.step_dispositions[sid]
        assert rec.disposition == "computed"
        assert rec.save_seconds > 0, f"{sid} save time not recorded"
        assert rec.save_seconds <= rec.elapsed_seconds
