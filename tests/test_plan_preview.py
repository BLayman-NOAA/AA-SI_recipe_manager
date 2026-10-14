# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Plan preview: the predicted disposition of every step equals the actual one."""

from __future__ import annotations

import json
from pathlib import Path

import fsspec
import pytest

import _stage9_helpers as H
from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.exceptions import UpstreamNotCachedError
from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.executor import checkpoint as checkpoint_module
from aa_recipe_manager.model.types import InputDeclaration, ParamDeclaration
from aa_recipe_manager.preview import PLANNED_TO_DISPOSITION, plan_preview

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"
USER_ROOT = "memory://preview/user"
CURATOR_ROOT = "memory://preview/curator"
SURVEY_ROOT = "memory://preview/survey"


@pytest.fixture(autouse=True)
def clear_memory_fs():
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    yield fs
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]


def _mem_snapshot(fs, prefix: str) -> dict[str, bytes]:
    return {path: fs.cat_file(path) for path in sorted(fs.find(prefix))}


def _always(steps, *step_ids):
    return [
        s.model_copy(update={"checkpoint": "always"}) if s.id in step_ids else s
        for s in steps
    ]


def _linear_steps(factor: int = 2, k: int = 0):
    """start -> first (scale) -> second (inc) -> third (addk)."""
    return [
        H.step("start", "const7", out_ports={"v": H.INT},
               output_map={"v": "__return__"}),
        H.step("first", "scale", inputs={"x": "${start.v}"},
               params={"factor": factor}, in_ports={"x": H.INT},
               param_decls={"factor": ParamDeclaration(type="int")},
               out_ports={"out": H.INT}, output_map={"out": "__return__"}),
        H.step("second", "inc", inputs={"x": "${first.out}"},
               in_ports={"x": H.INT}, out_ports={"out": H.INT},
               output_map={"out": "__return__"}),
        H.step("third", "addk", inputs={"x": "${second.out}"},
               params={"k": k}, in_ports={"x": H.INT},
               param_decls={"k": ParamDeclaration(type="int")},
               out_ports={"out": H.INT}, output_map={"out": "__return__"}),
    ]


def _mapped_steps():
    """seg -> (proc -> proc2 mapped over seg) -> merge, seg and proc saved."""
    return _always(
        [
            H.step("seg", "make_list", out_ports={"items": H.LIST},
                   output_map={"items": "__return__"}),
            H.step("proc", "inc", inputs={"x": "${_item}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"}, map_over="${seg.items}"),
            H.step("proc2", "scale", inputs={"x": "${proc.out}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"}, map_over="${seg.items}"),
            H.step("merge", "collect_sum", inputs={"values": "${proc2.out}"},
                   in_ports={"values": H.MANY}, out_ports={"total": H.INT},
                   output_map={"total": "__return__"}, collect="${proc2.out}"),
        ],
        "seg",
        "proc",
    )


def _assert_prediction(preview, result) -> None:
    manifest = json.loads(Path(result.manifest_file).read_text(encoding="utf-8"))
    predicted = {s.step_id: PLANNED_TO_DISPOSITION[s.planned] for s in preview.steps}
    actual = {sid: step["disposition"] for sid, step in manifest["steps"].items()}
    assert predicted == actual


def test_prediction_equals_actual_default_goals(tmp_path):
    dag = H.build(_linear_steps())
    tiers = {"user_cache_dir": USER_ROOT, "survey_cache_dir": SURVEY_ROOT}
    SequentialExecutor().execute(
        H.build(_linear_steps()[:2]),
        user_cache_dir=CURATOR_ROOT,
        survey_cache_dir=SURVEY_ROOT,
        cache_write_tier="survey",
        checkpoint_mode="eager",
    )

    preview = plan_preview(dag, **tiers, outputs_dir=tmp_path / "o1")
    assert preview.step("first").planned == "load-survey"
    assert preview.step("first").held_by == ["survey"]
    assert preview.step("start").planned == "pruned"
    result = SequentialExecutor().execute(dag, outputs_dir=tmp_path / "o1", **tiers)
    _assert_prediction(preview, result)

    SequentialExecutor().execute(
        dag, outputs_dir=tmp_path / "o2", checkpoint_mode="eager", **tiers
    )
    preview = plan_preview(dag, **tiers)
    assert preview.step("third").planned == "load-user"
    result = SequentialExecutor().execute(dag, outputs_dir=tmp_path / "o3", **tiers)
    _assert_prediction(preview, result)


def test_prediction_equals_actual_for_targets_and_only(tmp_path):
    dag = H.build(H.diamond_steps())
    cache = tmp_path / "cache"

    cases = [
        {"targets": ["branchA"]},
        {"targets": ["combine"], "only": True},
        {"targets": ["combine"], "only": True, "force_steps": ["combine"]},
        {"targets": ["branchB"], "force_steps": ["branchB"]},
        {"force_steps": ["branchA"]},
    ]
    with pytest.raises(UpstreamNotCachedError):
        plan_preview(dag, user_cache_dir=cache, targets=["combine"], only=True)
    SequentialExecutor().execute(dag, user_cache_dir=cache, checkpoint_mode="eager")
    for case in cases:
        preview = plan_preview(dag, user_cache_dir=cache, **case)
        result = SequentialExecutor().execute(dag, user_cache_dir=cache, **case)
        _assert_prediction(preview, result)


def test_mapped_instances_predicted_from_cached_json_source(tmp_path):
    dag = H.build(_mapped_steps())
    cache = tmp_path / "cache"

    before = plan_preview(dag, user_cache_dir=cache)
    assert before.step("proc").planned == "unknown"
    assert before.step("proc").instances is None
    assert before.step("proc").note == "instances are known once seg has run"

    SequentialExecutor().execute(dag, user_cache_dir=cache)
    for limit, note in ((32, None), (1, "predicted from the cache listing")):
        preview = plan_preview(dag, user_cache_dir=cache, instance_probe_limit=limit)
        proc = preview.step("proc")
        assert (proc.planned, proc.instances) == ("load-user", 3)
        assert proc.instances_held == {"user": 3}
        assert proc.held_by == ["user"]
        assert proc.note == note
        proc2 = preview.step("proc2")
        assert (proc2.planned, proc2.instances, proc2.instances_held) == (
            "compute", 3, {"user": 0},
        )
        assert preview.step("seg").planned == "load-user"
    result = SequentialExecutor().execute(dag, user_cache_dir=cache)
    _assert_prediction(preview, result)


def test_partly_cached_instances_are_counted(tmp_path):
    dag = H.build(_mapped_steps())
    cache = tmp_path / "cache"
    SequentialExecutor().execute(dag, user_cache_dir=cache)
    proc_dir = cache / "proc"
    victim = sorted(proc_dir.iterdir())[0]
    (victim / "meta.json").unlink()

    preview = plan_preview(dag, user_cache_dir=cache)
    proc = preview.step("proc")
    assert (proc.planned, proc.instances_held) == ("compute", {"user": 2})
    assert proc.note == "2 of 3 instances cached"


def test_skipped_chain_members_are_predicted(tmp_path):
    steps = _always(
        [
            H.step("seg", "make_list", out_ports={"items": H.LIST},
                   output_map={"items": "__return__"}),
            H.step("fetch", "inc", inputs={"x": "${_item}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"}, map_over="${seg.items}"),
            H.step("window", "scale", inputs={"x": "${fetch.out}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"}, map_over="${seg.items}"),
            H.step("derive", "addk", inputs={"x": "${window.out}"},
                   in_ports={"x": H.INT}, out_ports={"out": H.INT},
                   output_map={"out": "__return__"}, map_over="${seg.items}"),
            H.step("total", "collect_sum", inputs={"values": "${derive.out}"},
                   in_ports={"values": H.MANY}, out_ports={"total": H.INT},
                   output_map={"total": "__return__"}, collect="${derive.out}"),
        ],
        "seg",
        "fetch",
        "derive",
    )
    dag = H.build(steps)
    cache = tmp_path / "cache"
    SequentialExecutor().execute(dag, user_cache_dir=cache)

    preview = plan_preview(dag, user_cache_dir=cache)
    assert preview.step("fetch").planned == "skipped"
    assert preview.step("window").planned == "skipped"
    assert preview.step("derive").planned == "load-user"
    result = SequentialExecutor().execute(dag, user_cache_dir=cache)
    _assert_prediction(preview, result)


def test_checkpoint_field_and_pinned(tmp_path):
    recipe = FIXTURES / "flat.yaml"
    cache = tmp_path / "cache"

    whole = api.plan_preview(recipe, user_cache_dir=cache)
    assert whole.step("scaled").checkpoint == "memory-only"
    assert not whole.step("scaled").pinned

    before = api.plan_preview(recipe, user_cache_dir=cache, targets=["bump"])
    bump = before.step("bump")
    assert (bump.checkpoint, bump.pinned) == ("will-save", True)
    assert before.step("start").checkpoint == "memory-only"
    assert before.step("scaled").planned == "untargeted"
    unpinned = api.plan_preview(
        recipe, user_cache_dir=cache, targets=["bump"], pin_targets=False
    )
    assert unpinned.step("bump").checkpoint == "memory-only"

    api.execute(recipe, user_cache_dir=cache, targets=["bump"])
    after = api.plan_preview(recipe, user_cache_dir=cache, targets=["bump"])
    bump = after.step("bump")
    assert (bump.checkpoint, bump.planned, bump.held_by) == (
        "saved", "load-user", ["user"],
    )


def test_survey_hits_lost_upstream_edit_only():
    tiers = {"user_cache_dir": USER_ROOT, "survey_cache_dir": SURVEY_ROOT}
    SequentialExecutor().execute(
        H.build(_linear_steps()[:3]),
        user_cache_dir=CURATOR_ROOT,
        survey_cache_dir=SURVEY_ROOT,
        cache_write_tier="survey",
        checkpoint_mode="eager",
    )
    before = plan_preview(H.build(_linear_steps()), **tiers)
    assert [s.step_id for s in before.steps if "survey" in s.held_by] == [
        "start", "first", "second",
    ]
    upstream = plan_preview(H.build(_linear_steps(factor=5)), **tiers)
    assert api.survey_hits_lost(before, upstream) == ["first", "second"]
    downstream = plan_preview(H.build(_linear_steps(k=4)), **tiers)
    assert api.survey_hits_lost(before, downstream) == []


def test_preview_writes_nothing(clear_memory_fs):
    tiers = {"user_cache_dir": USER_ROOT, "survey_cache_dir": SURVEY_ROOT}
    dag = H.build(_mapped_steps())
    SequentialExecutor().execute(dag, **tiers)
    snapshot = _mem_snapshot(clear_memory_fs, "preview")
    preview = plan_preview(dag, targets=["proc2"], **tiers)
    assert preview.step("proc").instances == 3
    assert _mem_snapshot(clear_memory_fs, "preview") == snapshot


def test_fingerprint_cache_is_used_and_hash_neutral(tmp_path, monkeypatch):
    folder = tmp_path / "raw"
    folder.mkdir()
    (folder / "a.raw").write_text("x")
    recipe = H.recipe(
        [
            H.step("read", "inc", inputs={"x": "${inputs.folder}"},
                   in_ports={"x": H.PortDeclaration(type="path")},
                   out_ports={"out": H.INT}, output_map={"out": "__return__"}),
        ],
        inputs={"folder": InputDeclaration(type="path", fingerprint_mode="size")},
    )
    inputs = {"folder": str(folder)}
    plain = api.step_hashes(recipe, inputs=inputs)

    calls = []
    real = checkpoint_module._path_fingerprint

    def counting(*args, **kwargs):
        calls.append(args[0])
        return real(*args, **kwargs)

    monkeypatch.setattr(checkpoint_module, "_path_fingerprint", counting)
    cache: dict = {}
    first = api.step_hashes(recipe, inputs=inputs, fingerprint_cache=cache)
    assert len(calls) == 1
    second = api.step_hashes(recipe, inputs=inputs, fingerprint_cache=cache)
    assert len(calls) == 1
    assert first["read"].step_hash == second["read"].step_hash
    assert first["read"].step_hash == plain["read"].step_hash


def test_cli_preview_text_and_json(cli_runner, tmp_path):
    recipe = str(FIXTURES / "flat.yaml")
    cache = str(tmp_path / "cache")
    text = cli_runner.invoke(
        main, ["preview", recipe, "--user-cache-dir", cache, "--to", "bump"]
    )
    assert text.exit_code == 0, text.output
    rows = [line.split()[0] for line in text.output.splitlines()[3:6]]
    assert rows == ["start", "bump", "scaled"]
    assert "will-save pinned" in text.output

    as_json = cli_runner.invoke(
        main, ["preview", recipe, "--user-cache-dir", cache, "--json"]
    )
    assert as_json.exit_code == 0, as_json.output
    payload = json.loads(as_json.output)
    assert [s["planned"] for s in payload["steps"]] == ["compute"] * 3

    only = cli_runner.invoke(main, ["preview", recipe, "--only"])
    assert only.exit_code == 1
    assert "--only needs at least one --to" in only.output

    missing = cli_runner.invoke(
        main, ["preview", recipe, "--user-cache-dir", cache, "--to", "scaled", "--only"]
    )
    assert missing.exit_code == 1
    assert "not cached: bump" in missing.output


def test_preview_with_no_cache_dir_reports_compute():
    preview = plan_preview(H.build(_mapped_steps()), targets=["proc2"])
    planned = {s.step_id: s.planned for s in preview.steps}
    assert planned == {
        "seg": "compute", "proc": "compute", "proc2": "compute",
        "merge": "untargeted",
    }
    assert preview.tiers == {}
    assert "no cache configured: every step computes" in preview.warnings
    assert all(s.checkpoint == "memory-only" for s in preview.steps)


def test_prediction_after_pinning_earlier_chain_members(tmp_path):
    recipe = FIXTURES / "mapped.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, targets=["seg"])
    api.execute(recipe, user_cache_dir=cache, targets=["proc"])

    preview = api.plan_preview(recipe, user_cache_dir=cache, targets=["proc2"])
    assert preview.step("proc").planned == "load-user"
    assert preview.step("proc").instances_held == {"user": 3}
    assert preview.step("proc2").planned == "compute"
    result = api.execute(recipe, user_cache_dir=cache, targets=["proc2"])
    _assert_prediction(preview, result)


def test_fingerprint_cache_keeps_no_unverified_result(tmp_path, monkeypatch):
    folder = tmp_path / "raw"
    folder.mkdir()
    recipe = H.recipe(
        [
            H.step("read", "inc", inputs={"x": "${inputs.folder}"},
                   in_ports={"x": H.PortDeclaration(type="path")},
                   out_ports={"out": H.INT}, output_map={"out": "__return__"}),
        ],
        inputs={"folder": InputDeclaration(type="path", fingerprint_mode="size")},
    )
    answers = [{"kind": "remote-unverified"}, {"kind": "dir", "entries": []}]
    calls = []

    def stub(*args, **kwargs):
        calls.append(args[0])
        return answers[len(calls) - 1]

    monkeypatch.setattr(checkpoint_module, "_path_fingerprint", stub)
    cache: dict = {}
    first = api.step_hashes(
        recipe, inputs={"folder": str(folder)}, fingerprint_cache=cache
    )
    second = api.step_hashes(
        recipe, inputs={"folder": str(folder)}, fingerprint_cache=cache
    )
    assert len(calls) == 2
    assert first["read"].step_hash != second["read"].step_hash
    assert list(cache.values()) == [{"kind": "dir", "entries": []}]


def test_prediction_with_pruned_members_after_a_goal_member(tmp_path):
    from test_run_targets import _goal_inside_chain_steps

    dag = H.build(_always(_goal_inside_chain_steps(), "seg"))
    cache = tmp_path / "cache"
    SequentialExecutor().execute(dag, user_cache_dir=cache)
    preview = plan_preview(dag, user_cache_dir=cache)
    assert preview.step("b").planned == "pruned"
    result = SequentialExecutor().execute(dag, user_cache_dir=cache)
    _assert_prediction(preview, result)


def test_preview_rejects_unknown_targets_like_run(tmp_path):
    from aa_recipe_manager.exceptions import UnknownStepError

    with pytest.raises(UnknownStepError, match="did you mean 'bump'"):
        api.plan_preview(
            FIXTURES / "flat.yaml", user_cache_dir=tmp_path, targets=["bumpp"]
        )


def test_held_steps_that_compute_say_why(tmp_path):
    recipe = FIXTURES / "flat.yaml"
    cache = tmp_path / "cache"
    api.execute(recipe, user_cache_dir=cache, checkpoint_mode="eager")
    preview = api.plan_preview(recipe, user_cache_dir=cache, force_steps=["bump"])
    bump, scaled = preview.step("bump"), preview.step("scaled")
    assert (bump.held_by, bump.planned) == (["user"], "compute")
    assert bump.note == "recomputes: forced by --force-step"
    assert scaled.note == "recomputes: an upstream step is forced"
    assert preview.step("start").planned == "load-user"
    assert "these steps are not saved" not in preview.format_text()
