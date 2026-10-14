# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Run targets: run to a step, run a step only, and force a step."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import _stage9_helpers as H
from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.exceptions import (
    PipelineExecutionError,
    UpstreamNotCachedError,
)
from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.executor.checkpoint import CheckpointManager, plan_execution

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"


def _diamond(**changes):
    steps = H.diamond_steps()
    if changes:
        steps = [
            s.model_copy(update=changes[s.id]) if s.id in changes else s
            for s in steps
        ]
    return H.build(steps)


def _dispositions(result) -> dict[str, str]:
    return {sid: rec.disposition for sid, rec in result.step_dispositions.items()}


def _manifest(result) -> dict:
    return json.loads(Path(result.manifest_file).read_text(encoding="utf-8"))


def _hashes(path: Path) -> dict[str, str]:
    return {sid: h.step_hash for sid, h in api.step_hashes(path).items()}


def _instance_entries(cache: Path, step_id: str) -> int:
    return len(list((cache / step_id).glob("*/meta.json")))


def test_target_runs_ancestry_only(tmp_path):
    result = SequentialExecutor().execute(
        _diamond(), user_cache_dir=tmp_path / "cache", targets=["branchA"]
    )
    assert result.executed_steps == ["start", "branchA"]
    dispositions = _dispositions(result)
    assert dispositions["branchB"] == "untargeted"
    assert dispositions["combine"] == "untargeted"
    assert sorted(result.untargeted_steps) == ["branchB", "combine"]
    assert result.mode == "to"

    manifest = _manifest(result)
    assert manifest["mode"] == "to"
    assert manifest["targets"] == ["branchA"]
    assert manifest["force_steps"] == []
    assert manifest["steps"]["branchB"]["disposition"] == "untargeted"
    assert manifest["steps"]["combine"]["disposition"] == "untargeted"


def test_whole_run_manifest_reports_mode_all(tmp_path):
    result = SequentialExecutor().execute(_diamond(), user_cache_dir=tmp_path / "c")
    manifest = _manifest(result)
    assert (manifest["mode"], manifest["targets"]) == ("all", [])
    assert result.untargeted_steps == []


def test_cached_ancestor_stops_the_walk(tmp_path):
    cache = tmp_path / "cache"
    SequentialExecutor().execute(_diamond(), user_cache_dir=cache, targets=["start"])
    result = SequentialExecutor().execute(
        _diamond(), user_cache_dir=cache, targets=["branchA"]
    )
    assert _dispositions(result)["start"] == "hit-user-cache"
    assert result.executed_steps == ["branchA"]


def test_cached_target_loads(tmp_path):
    cache = tmp_path / "cache"
    SequentialExecutor().execute(_diamond(), user_cache_dir=cache, targets=["branchA"])
    result = SequentialExecutor().execute(
        _diamond(), user_cache_dir=cache, targets=["branchA"]
    )
    assert result.executed_steps == []
    assert _dispositions(result)["branchA"] == "hit-user-cache"
    assert _dispositions(result)["start"] == "pruned"


def test_only_succeeds_with_cached_parents_and_names_missing_ones(tmp_path):
    cache = tmp_path / "cache"
    SequentialExecutor().execute(
        _diamond(), user_cache_dir=cache, checkpoint_mode="eager"
    )

    cached = SequentialExecutor().execute(
        _diamond(), user_cache_dir=cache, targets=["combine"], only=True
    )
    assert cached.executed_steps == []
    assert _dispositions(cached)["combine"] == "hit-user-cache"
    assert cached.mode == "only"

    forced = SequentialExecutor().execute(
        _diamond(),
        user_cache_dir=cache,
        targets=["combine"],
        only=True,
        force_steps=["combine"],
    )
    assert forced.executed_steps == ["combine"]
    assert _dispositions(forced)["branchA"] == "hit-user-cache"
    assert _dispositions(forced)["start"] == "pruned"

    with pytest.raises(UpstreamNotCachedError) as info:
        SequentialExecutor().execute(
            _diamond(), user_cache_dir=tmp_path / "fresh", targets=["combine"],
            only=True,
        )
    assert info.value.missing == ["branchA", "branchB"]
    assert "run to the target instead" in str(info.value)


def test_only_without_a_cache_names_the_parents():
    with pytest.raises(UpstreamNotCachedError) as info:
        SequentialExecutor().execute(_diamond(), targets=["combine"], only=True)
    assert info.value.missing == ["branchA", "branchB"]


def test_force_steps_recompute_with_their_consumers(tmp_path):
    cache = tmp_path / "cache"
    SequentialExecutor().execute(
        _diamond(), user_cache_dir=cache, checkpoint_mode="eager"
    )
    result = SequentialExecutor().execute(
        _diamond(), user_cache_dir=cache, force_steps=["branchB"]
    )
    # A cached consumer of a forced step holds the old result, so it
    # recomputes too.
    assert result.executed_steps == ["branchB", "combine"]
    dispositions = _dispositions(result)
    assert dispositions["combine"] == "computed"
    assert dispositions["start"] == "hit-user-cache"
    assert dispositions["branchA"] == "hit-user-cache"
    assert _manifest(result)["force_steps"] == ["branchB"]


def test_target_is_pinned_under_explicit_mode_and_hashes_unchanged(tmp_path):
    recipe = FIXTURES / "flat.yaml"
    before = _hashes(recipe)

    cache = tmp_path / "cache"
    result = api.execute(recipe, user_cache_dir=cache, targets=["bump"])
    assert result.executed_steps == ["start", "bump"]
    assert CheckpointManager(cache, before).has_checkpoint("bump")
    assert not CheckpointManager(cache, before).has_checkpoint("start")
    assert _hashes(recipe) == before

    unpinned = tmp_path / "unpinned"
    api.execute(recipe, user_cache_dir=unpinned, targets=["bump"], pin_targets=False)
    assert not CheckpointManager(unpinned, before).has_checkpoint("bump")


def test_target_with_checkpoint_never_warns_and_saves_nothing(tmp_path):
    dag = _diamond(branchA={"checkpoint": "never"})
    cache = tmp_path / "cache"
    result = SequentialExecutor().execute(
        dag, user_cache_dir=cache, targets=["branchA"]
    )
    assert (
        "warning: target branchA has checkpoint: never and will not be saved"
        in result.logs
    )
    assert not (cache / "branchA").exists()


def test_targets_without_checkpoints_warn():
    result = SequentialExecutor().execute(_diamond(), targets=["branchA"])
    assert "warning: targets will not be saved: checkpoints are disabled" in (
        result.logs
    )
    assert result.executed_steps == ["start", "branchA"]


def test_chain_head_middle_and_collector_targets(tmp_path):
    recipe = FIXTURES / "mapped.yaml"

    head = api.execute(recipe, user_cache_dir=tmp_path / "a", targets=["proc"])
    assert head.executed_steps == ["seg", "proc"]
    assert sorted(head.untargeted_steps) == ["merge", "proc2"]
    assert _instance_entries(tmp_path / "a", "proc") == 3
    assert _manifest(head)["steps"]["proc"]["instances"]["count"] == 3

    middle = api.execute(recipe, user_cache_dir=tmp_path / "b", targets=["proc2"])
    assert middle.executed_steps == ["seg", "proc", "proc2"]
    assert middle.untargeted_steps == ["merge"]
    assert _instance_entries(tmp_path / "b", "proc2") == 3
    assert _manifest(middle)["steps"]["proc2"]["instances"]["count"] == 3

    collector = api.execute(recipe, user_cache_dir=tmp_path / "c", targets=["merge"])
    assert collector.executed_steps == ["seg", "proc", "proc2", "merge"]
    assert collector.untargeted_steps == []
    assert collector.outputs["merge"]["total"] == 2 * (11 + 21 + 31)
    assert _manifest(collector)["steps"]["proc2"]["instances"]["count"] == 3


def test_only_on_mapped_target_checks_instances_at_run_time(tmp_path):
    recipe = FIXTURES / "mapped.yaml"

    warm = tmp_path / "warm"
    api.execute(recipe, user_cache_dir=warm, targets=["seg"])
    api.execute(recipe, user_cache_dir=warm, targets=["proc"])
    first = api.execute(recipe, user_cache_dir=warm, targets=["proc2"], only=True)
    assert _dispositions(first)["seg"] == "hit-user-cache"
    assert _dispositions(first)["proc"] == "hit-user-cache"
    assert _dispositions(first)["proc2"] == "computed"

    again = api.execute(recipe, user_cache_dir=warm, targets=["proc2"], only=True)
    assert _dispositions(again)["proc2"] == "hit-user-cache"
    assert _dispositions(again)["proc"] == "skipped"

    # The map source is cached but no instance of proc is.
    cold = tmp_path / "cold"
    api.execute(recipe, user_cache_dir=cold, targets=["seg"])
    with pytest.raises(PipelineExecutionError) as info:
        api.execute(recipe, user_cache_dir=cold, targets=["proc2"], only=True)
    assert info.value.step_id == "proc"
    assert isinstance(info.value.original, UpstreamNotCachedError)


def test_later_target_in_a_chain_loads_the_pinned_earlier_one(tmp_path):
    recipe = FIXTURES / "mapped.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, targets=["proc"])
    result = api.execute(recipe, user_cache_dir=cache, targets=["proc2"])
    assert result.executed_steps == ["seg", "proc2"]
    assert _dispositions(result)["proc"] == "hit-user-cache"


def test_run_only_on_a_pinned_chain_member(tmp_path):
    recipe = FIXTURES / "mapped.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, targets=["seg"])
    api.execute(recipe, user_cache_dir=cache, targets=["proc"])
    result = api.execute(recipe, user_cache_dir=cache, targets=["proc"], only=True)
    assert _dispositions(result)["proc"] == "hit-user-cache"
    assert result.executed_steps == []


def test_instances_saved_under_eager_are_hits_under_explicit(tmp_path):
    recipe = FIXTURES / "mapped.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, checkpoint_mode="eager")
    result = api.execute(
        recipe, user_cache_dir=cache, targets=["proc2"], pin_targets=False
    )
    assert _dispositions(result)["proc2"] == "hit-user-cache"
    assert _dispositions(result)["proc"] == "skipped"
    assert result.executed_steps == []


def _count_instance_probes(monkeypatch) -> list[str]:
    probes: list[str] = []
    real = CheckpointManager.has_checkpoint

    def counting(self, step_id, *, instance_hash=None):
        if instance_hash is not None:
            probes.append(step_id)
        return real(self, step_id, instance_hash=instance_hash)

    monkeypatch.setattr(CheckpointManager, "has_checkpoint", counting)
    return probes


def test_instance_probes_stop_at_the_last_cached_member(tmp_path, monkeypatch):
    recipe = FIXTURES / "mapped.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, checkpoint_mode="eager")
    probes = _count_instance_probes(monkeypatch)
    api.execute(recipe, user_cache_dir=cache, targets=["proc2"])
    assert probes == ["proc2"] * 3


def test_instance_keys_absent_from_the_listing_are_not_probed(
    tmp_path, monkeypatch
):
    recipe = FIXTURES / "mapped.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, targets=["seg"])
    probes = _count_instance_probes(monkeypatch)
    result = api.execute(recipe, user_cache_dir=cache, targets=["proc2"])
    assert probes == []
    assert result.executed_steps == ["proc", "proc2"]


def test_second_run_to_a_chain_member_loads_no_instances(tmp_path, monkeypatch):
    from aa_recipe_manager.executor.tiered import TieredCheckpointStore

    recipe = FIXTURES / "mapped.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, targets=["proc"])
    loads: list[str] = []
    real = TieredCheckpointStore.load

    def counting(self, step_id, *, instance_hash=None):
        if instance_hash is not None:
            loads.append(step_id)
        return real(self, step_id, instance_hash=instance_hash)

    monkeypatch.setattr(TieredCheckpointStore, "load", counting)
    result = api.execute(recipe, user_cache_dir=cache, targets=["proc"])
    assert _dispositions(result)["proc"] == "hit-user-cache"
    assert loads == []


def test_truncated_chain_after_a_disposer_needs_a_checkpoint(tmp_path):
    from aa_recipe_manager.model.types import PortDeclaration

    steps = [
        H.step("seg", "make_list", out_ports={"items": H.LIST},
               output_map={"items": "__return__"}),
        H.step("scratch", "write_scratch", inputs={"x": "${_item}"},
               in_ports={"x": H.INT},
               out_ports={"path": PortDeclaration(type="path", disposable=True)},
               output_map={"path": "__return__"}, map_over="${seg.items}"),
        H.step("value", "read_scratch", inputs={"path": "${scratch.path}"},
               in_ports={"path": PortDeclaration(type="path")},
               out_ports={"out": PortDeclaration(type="object")},
               output_map={"out": "__return__"}, map_over="${seg.items}"),
        H.step("count", "inc", inputs={"x": "${value.out}"},
               in_ports={"x": PortDeclaration(type="object")},
               out_ports={"out": H.INT},
               output_map={"out": "__return__"}, map_over="${seg.items}"),
    ]
    steps = [
        s.model_copy(update={"checkpoint": "never"}) if s.id == "scratch" else s
        for s in steps
    ]
    dag = H.build(steps)
    cache = tmp_path / "cache"
    with pytest.raises(PipelineExecutionError, match="lazy graph"):
        SequentialExecutor().execute(
            dag, user_cache_dir=cache, targets=["value"], pin_targets=False
        )
    result = SequentialExecutor().execute(
        dag, user_cache_dir=cache, targets=["value"]
    )
    assert result.outputs["value"]["out"] == [10, 20, 30]
    whole = SequentialExecutor().execute(dag, user_cache_dir=tmp_path / "whole")
    assert whole.outputs["count"]["out"] == [11, 21, 31]


def test_dask_matches_sequential_for_targets(tmp_path):
    pytest.importorskip("dask.distributed")
    recipe = FIXTURES / "mapped.yaml"
    sequential = api.execute(recipe, user_cache_dir=tmp_path / "s", targets=["proc2"])
    dask = api.execute(
        recipe,
        user_cache_dir=tmp_path / "d",
        targets=["proc2"],
        executor="dask",
        executor_options={"scheduler": "threads", "threads_per_worker": 2},
    )
    assert _dispositions(dask) == _dispositions(sequential)
    assert dask.outputs["proc2"]["out"] == sequential.outputs["proc2"]["out"]


def test_planner_rejects_unknown_targets():
    dag = _diamond()
    with pytest.raises(ValueError, match="unknown step id"):
        plan_execution(dag, None, targets=["nope"])
    with pytest.raises(ValueError, match="unknown step id"):
        plan_execution(dag, None, force_steps=["nope"])
    with pytest.raises(ValueError, match="at least one target"):
        plan_execution(dag, None, only=True)


def test_planner_without_targets_is_unchanged():
    plan = plan_execution(_diamond(), None)
    assert plan.must_run == {"start", "branchA", "branchB", "combine"}
    assert plan.untargeted == set()
    assert plan.targets == ()


def test_cli_run_to_and_only(cli_runner, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parent))
    recipe = str(FIXTURES / "flat.yaml")
    cache = str(tmp_path / "cache")

    ran = cli_runner.invoke(
        main, ["run", recipe, "--user-cache-dir", cache, "--to", "bump"]
    )
    assert ran.exit_code == 0, ran.output
    assert "Targets: bump (run to here)" in ran.output
    assert "Outside the target: 1 step(s)" in ran.output
    assert "[1/2] start ... start" in ran.output

    no_target = cli_runner.invoke(main, ["run", recipe, "--only"])
    assert no_target.exit_code == 1
    assert "--only needs at least one --to" in no_target.output

    empty = str(tmp_path / "empty")
    missing = cli_runner.invoke(
        main, ["run", recipe, "--user-cache-dir", empty, "--to", "scaled", "--only"]
    )
    assert missing.exit_code == 1
    assert "not cached: bump" in missing.output
    assert "Traceback" not in missing.output


def _goal_inside_chain_steps():
    """side, seg, then a chain a -> leaf, b where leaf is a goal and b reads side.

    final collects b and is checkpointed, so once it is cached a whole run
    prunes b and side while the leaf member, a goal, still runs.
    """
    return [
        H.step("side", "const7", out_ports={"v": H.INT},
               output_map={"v": "__return__"}),
        H.step("seg", "make_list", out_ports={"items": H.LIST},
               output_map={"items": "__return__"}),
        H.step("a", "inc", inputs={"x": "${_item}"}, in_ports={"x": H.INT},
               out_ports={"out": H.INT}, output_map={"out": "__return__"},
               map_over="${seg.items}"),
        H.step("leaf", "inc", inputs={"x": "${a.out}"}, in_ports={"x": H.INT},
               out_ports={"out": H.INT}, output_map={"out": "__return__"},
               map_over="${seg.items}"),
        H.step("b", "add", inputs={"a": "${a.out}", "b": "${side.v}"},
               in_ports={"a": H.INT, "b": H.INT}, out_ports={"out": H.INT},
               output_map={"out": "__return__"}, map_over="${seg.items}"),
        H.step("final", "collect_sum", inputs={"values": "${b.out}"},
               in_ports={"values": H.MANY}, out_ports={"total": H.INT},
               output_map={"total": "__return__"}, collect="${b.out}")
        .model_copy(update={"checkpoint": "always"}),
    ]


def test_pruned_chain_members_after_a_goal_member_do_not_run(tmp_path):
    dag = H.build(_goal_inside_chain_steps())
    cache = tmp_path / "cache"
    SequentialExecutor().execute(dag, user_cache_dir=cache)
    result = SequentialExecutor().execute(dag, user_cache_dir=cache)
    dispositions = _dispositions(result)
    assert dispositions["final"] == "hit-user-cache"
    assert dispositions["b"] == "pruned"
    assert dispositions["side"] == "pruned"
    assert result.executed_steps == ["seg", "a", "leaf"]


def test_untargeted_readers_do_not_keep_chain_members_needed(tmp_path):
    steps = [
        H.step("seg", "make_list", out_ports={"items": H.LIST},
               output_map={"items": "__return__"}),
        H.step("proc", "inc", inputs={"x": "${_item}"}, in_ports={"x": H.INT},
               out_ports={"out": H.INT}, output_map={"out": "__return__"},
               map_over="${seg.items}"),
        H.step("proc2", "scale", inputs={"x": "${proc.out}"},
               in_ports={"x": H.INT}, out_ports={"out": H.INT},
               output_map={"out": "__return__"}, map_over="${seg.items}"),
        H.step("gather", "collect_sum", inputs={"values": "${proc.out}"},
               in_ports={"values": H.MANY}, out_ports={"total": H.INT},
               output_map={"total": "__return__"}, collect="${proc.out}"),
    ]
    dag = H.build(steps)
    cache = tmp_path / "cache"
    SequentialExecutor().execute(dag, user_cache_dir=cache, targets=["seg"])
    SequentialExecutor().execute(dag, user_cache_dir=cache, targets=["proc2"])
    result = SequentialExecutor().execute(
        dag, user_cache_dir=cache, targets=["proc2"]
    )
    dispositions = _dispositions(result)
    assert dispositions["gather"] == "untargeted"
    assert dispositions["proc2"] == "hit-user-cache"
    assert dispositions["proc"] == "skipped"
    assert result.executed_steps == []


def test_forced_step_refreshes_a_cached_target(tmp_path):
    recipe = FIXTURES / "flat.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, checkpoint_mode="eager")
    result = api.execute(
        recipe, user_cache_dir=cache, targets=["scaled"], force_steps=["bump"]
    )
    assert result.executed_steps == ["bump", "scaled"]
    assert _dispositions(result)["start"] == "hit-user-cache"


def test_refused_run_only_writes_nothing(tmp_path):
    from aa_recipe_manager.events import MemoryEventSink

    sink = MemoryEventSink()
    with pytest.raises(UpstreamNotCachedError):
        SequentialExecutor().execute(
            _diamond(), user_cache_dir=tmp_path / "cache", targets=["combine"],
            only=True, events=sink,
        )
    assert [e.kind for e in sink.events] == ["run_finished"]
    assert not (tmp_path / "outputs").exists()


def test_refused_curated_run_publishes_no_provenance(tmp_path):
    with pytest.raises(UpstreamNotCachedError):
        SequentialExecutor().execute(
            _diamond(),
            user_cache_dir=tmp_path / "user",
            survey_cache_dir=tmp_path / "survey",
            cache_write_tier="survey",
            targets=["combine"],
            only=True,
        )
    assert not (tmp_path / "survey").exists()


def test_unknown_ids_suggest_close_matches():
    from aa_recipe_manager.exceptions import UnknownStepError

    with pytest.raises(UnknownStepError) as info:
        plan_execution(_diamond(), None, targets=["branchC"])
    message = str(info.value)
    assert "'branchC' (did you mean" in message
    assert "'branchA'" in message and "'branchB'" in message
    assert "valid ids" not in message and "\n" not in message
    with pytest.raises(UnknownStepError, match="valid ids"):
        plan_execution(_diamond(), None, targets=["zzz"])


def test_cli_prints_pin_warnings(cli_runner, tmp_path):
    ran = cli_runner.invoke(
        main,
        ["run", str(FIXTURES / "flat.yaml"), "--no-checkpoints", "--to", "bump",
         "--outputs-dir", str(tmp_path / "out")],
    )
    assert ran.exit_code == 0, ran.output
    assert "Warning: targets will not be saved: checkpoints are disabled" in (
        ran.output
    )
