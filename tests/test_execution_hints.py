# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Stage 9c: per-step dask_config / prefect_config merging (FR-15.6)."""

from __future__ import annotations

import warnings

import pytest

import _stage9_helpers as H
from aa_recipe_manager.executor.engine.hints import (
    resolve_dask_config,
    resolve_prefect_config,
    resolve_unit_dask_config,
)
from aa_recipe_manager.executor.engine.schedule import build_unit_graph
from aa_recipe_manager.model.types import ExecutionHints, StepExecutionHints


def _unit_for(graph, first_member):
    return next(u for u in graph.units.values() if u.first == first_member)


def test_step_overrides_pipeline_on_conflicting_key():
    # Pipeline default retries=1; the step overrides to retries=3 and adds a key.
    steps = H.diamond_steps()
    steps[1].execution = StepExecutionHints(
        prefect_config={"retries": 3, "timeout_seconds": 30}
    )
    dag = H.build(
        steps,
        execution=ExecutionHints(prefect_config={"retries": 1, "tags": ["a"]}),
    )
    merged = resolve_prefect_config(dag, dag.nodes["branchA"])
    assert merged == {"retries": 3, "tags": ["a"], "timeout_seconds": 30}


def test_step_without_block_inherits_pipeline():
    steps = H.diamond_steps()
    dag = H.build(steps, execution=ExecutionHints(dask_config={"scheduler": "threads"}))
    assert resolve_dask_config(dag, dag.nodes["branchB"]) == {"scheduler": "threads"}


def test_absent_everywhere_is_empty():
    dag = H.build(H.diamond_steps())
    assert resolve_dask_config(dag, dag.nodes["start"]) == {}
    assert resolve_prefect_config(dag, dag.nodes["start"]) == {}


def test_chain_merges_members_and_warns_on_conflict():
    # Two members of one mapped chain disagree on the scheduler -> warning.
    seg = H.step("seg", "make_list", out_ports={"items": H.LIST},
                 output_map={"items": "__return__"})
    m1 = H.step("read", "inc", inputs={"x": "${_item}"}, in_ports={"x": H.INT},
                out_ports={"out": H.INT}, output_map={"out": "__return__"},
                map_over="${seg.items}",
                execution=StepExecutionHints(dask_config={"scheduler": "threads"}))
    m2 = H.step("proc", "inc", inputs={"x": "${read.out}"}, in_ports={"x": H.INT},
                out_ports={"out": H.INT}, output_map={"out": "__return__"},
                map_over="${seg.items}",
                execution=StepExecutionHints(dask_config={"scheduler": "processes"}))
    dag = H.build([seg, m1, m2])
    graph = build_unit_graph(dag)
    unit = _unit_for(graph, "read")
    with pytest.warns(RuntimeWarning, match="conflicting execution config"):
        merged = resolve_unit_dask_config(dag, unit)
    # Later member wins.
    assert merged == {"scheduler": "processes"}


def test_inline_backend_ignores_execution_blocks():
    # A step-level dask/prefect block must not affect the sequential run.
    from aa_recipe_manager.executor import SequentialExecutor

    steps = H.diamond_steps()
    steps[1].execution = StepExecutionHints(dask_config={"scheduler": "processes"})
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # no warnings expected from inline
        result = SequentialExecutor().execute(H.build(steps))
    assert result.outputs["combine"]["out"] == 22  # (7+1)+(7*2)
