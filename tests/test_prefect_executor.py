# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Stage 9b: the Prefect executor backend.

Skipped entirely when Prefect is not installed (it is an optional extra). Each
run executes inside a Prefect flow named after the recipe, with a task per step.
"""

from __future__ import annotations

import logging

import pytest

import _stage9_helpers as H

pytest.importorskip("prefect")

from aa_recipe_manager.executor import SequentialExecutor  # noqa: E402
from aa_recipe_manager.executor.prefect_executor import PrefectExecutor  # noqa: E402
from aa_recipe_manager.model.types import StepExecutionHints  # noqa: E402

for _name in ("prefect", "prefect.flow_runs", "prefect.task_runs"):
    logging.getLogger(_name).setLevel(logging.ERROR)


def test_map_collect_matches_sequential():
    seq = SequentialExecutor().execute(H.build(H.map_collect_steps()))
    pfx = PrefectExecutor().execute(H.build(H.map_collect_steps()))
    assert seq.outputs["merge"]["total"] == pfx.outputs["merge"]["total"] == 63
    assert sorted(pfx.outputs["proc"]["out"]) == [11, 21, 31]


def test_diamond_matches_sequential(tmp_path):
    pfx = PrefectExecutor().execute(
        H.build(H.diamond_steps()),
        user_cache_dir=str(tmp_path / "cache"),
        checkpoint_mode="eager",
    )
    assert pfx.outputs["combine"]["out"] == 22


def test_step_retries_are_honored():
    # 'proc' fails twice, then succeeds; a step-level retries=2 recovers it.
    H.reset_flaky(fail_until=2)
    steps = [
        H.step("seed", "const7", out_ports={"v": H.INT},
               output_map={"v": "__return__"}),
        H.step("proc", "flaky", inputs={"x": "${seed.v}"},
               in_ports={"x": H.INT}, out_ports={"out": H.INT},
               output_map={"out": "__return__"},
               execution=StepExecutionHints(
                   prefect_config={"retries": 2, "retry_delay_seconds": 0})),
    ]
    result = PrefectExecutor().execute(H.build(steps))
    assert result.outputs["proc"]["out"] == 107  # 7 + 100 on the 3rd attempt
    assert H.flaky_attempts() == 3
