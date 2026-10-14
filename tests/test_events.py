# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Run events: order, content, log capture, sinks and the CLI stream."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import _stage9_helpers as H
from aa_recipe_manager import api
from aa_recipe_manager.cli import _CLIProgress, main
from aa_recipe_manager.events import (
    EVENT_KINDS,
    CallbackEventSink,
    JsonlEventSink,
    MemoryEventSink,
    ProgressEventSink,
    RunEvent,
)
from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.model.types import ParamDeclaration

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"


def _one_step(callable_name: str, step_id: str = "x"):
    return H.build(
        [
            H.step(
                step_id,
                callable_name,
                params={"x": 3},
                param_decls={"x": ParamDeclaration(type="int")},
                out_ports={"out": H.INT},
                output_map={"out": "__return__"},
            )
        ]
    )


def _step_events(sink: MemoryEventSink, step_id: str) -> list[RunEvent]:
    return [e for e in sink.events if e.data.get("step") == step_id]


def test_event_order_for_a_small_recipe(tmp_path):
    sink = MemoryEventSink()
    result = api.execute(FIXTURES / "flat.yaml", user_cache_dir=tmp_path, events=sink)
    kinds = [e.kind for e in sink.events]
    assert kinds[:2] == ["run_started", "plan"]
    assert kinds[-1] == "run_finished"
    assert all(kind in EVENT_KINDS for kind in kinds)
    assert [e.seq for e in sink.events] == list(range(1, len(sink.events) + 1))
    assert all(e.run_id == result.run_id for e in sink.events)

    started = sink.of("run_started")[0].data
    assert started["recipe"] == "client_flat"
    assert started["recipe_path"] == str(FIXTURES / "flat.yaml")
    assert started["mode"] == "all"
    assert started["executor"] == "inline"
    assert started["step_count"] == started["total_steps"] == 3

    # The one end per start guarantee of a run that completes.
    for event in sink.of("step_started"):
        later = [
            e for e in sink.events
            if e.seq > event.seq
            and e.kind in ("step_finished", "step_failed")
            and e.data["step"] == event.data["step"]
        ]
        assert len(later) == 1
    finished = sink.of("run_finished")[0].data
    assert finished["status"] == "completed"
    assert finished["executed"] == ["start", "bump", "scaled"]
    assert finished["manifest"] == str(result.manifest_file)


def test_dispositions_in_events_equal_manifest(tmp_path):
    for _ in range(2):
        sink = MemoryEventSink()
        result = api.execute(
            FIXTURES / "flat.yaml",
            user_cache_dir=tmp_path,
            checkpoint_mode="eager",
            events=sink,
        )
        manifest = json.loads(Path(result.manifest_file).read_text(encoding="utf-8"))
        from_events = {
            e.data["step"]: e.data["disposition"] for e in sink.of("step_finished")
        }
        from_manifest = {
            sid: step["disposition"] for sid, step in manifest["steps"].items()
        }
        assert from_events == from_manifest
    assert set(from_events.values()) == {"hit-user-cache", "pruned"}
    plan = sink.of("plan")[0].data["steps"]
    assert plan["scaled"] == {
        "hash": result.step_dispositions["scaled"].step_hash,
        "planned": "load",
        "tier": "user",
    }


def test_mapped_step_emits_one_instance_finished_per_instance(tmp_path):
    sink = MemoryEventSink()
    api.execute(FIXTURES / "mapped.yaml", user_cache_dir=tmp_path, events=sink)
    instances = sink.of("instance_finished")
    assert [e.data["instance"] for e in instances] == [0, 1, 2]
    assert all(e.data["step"] == "proc" for e in instances)
    assert all(e.data["count"] == 3 for e in instances)
    assert instances[0].data["members"] == {"proc": "computed", "proc2": "computed"}

    started = [e for e in sink.of("step_started") if e.data["step"] == "proc"][0]
    assert started.data["instances"] == 3
    assert started.data["chain"] == ["proc", "proc2"]
    finished = [e for e in sink.of("step_finished") if e.data["step"] == "proc"][0]
    assert finished.data["instances"] == {
        "count": 3, "computed": 3, "cached": 0, "skipped": 0,
    }
    assert len(finished.data["instance_seconds"]) == 3


def test_log_text_is_tagged_with_its_step(tmp_path):
    sink = MemoryEventSink()
    result = SequentialExecutor().execute(
        _one_step("noisy"), user_cache_dir=tmp_path / "cache", events=sink
    )
    logs = [e for e in sink.of("log") if e.data["step"] == "x"]
    text = "".join(e.data["text"] for e in logs)
    assert "noisy-start x=3" in text
    assert all(e.data["buffered"] is False for e in logs)
    assert all(e.data["stream"] == "stdout" for e in logs)
    assert "noisy-start x=3" in Path(result.log_file).read_text(encoding="utf-8")


def test_log_lines_are_batched_per_write_and_flushed_at_step_end(tmp_path):
    sink = MemoryEventSink()
    SequentialExecutor().execute(
        _one_step("partial_lines"), user_cache_dir=tmp_path / "cache", events=sink
    )
    logs = [e for e in sink.of("log") if e.data["step"] == "x"]
    texts = [e.data["text"] for e in logs]
    assert "".join(texts) == "first line\nsecond line\ntail"
    assert texts[-1] == "tail"
    assert all(text.endswith("\n") for text in texts[:-1])
    finished = [e for e in sink.of("step_finished") if e.data["step"] == "x"][0]
    assert logs[-1].seq < finished.seq


def test_step_failed_carries_traceback_and_run_finished_failed(tmp_path):
    sink = MemoryEventSink()
    with pytest.raises(PipelineExecutionError):
        SequentialExecutor().execute(
            _one_step("boom"), user_cache_dir=tmp_path / "cache", events=sink
        )
    failed = sink.of("step_failed")[0].data
    assert failed["step"] == "x"
    assert failed["error_type"] == "PipelineExecutionError"
    assert "boom" in failed["traceback"]
    assert failed["seconds"] >= 0.0
    finished = sink.events[-1]
    assert finished.kind == "run_finished"
    assert finished.data["status"] == "failed"
    assert finished.data["error_type"] == "PipelineExecutionError"
    assert "boom" in finished.data["message"]


def test_backend_failure_still_ends_with_run_finished(tmp_path):
    class _NoBackend(SequentialExecutor):
        def _make_backend(self):
            raise ImportError("backend unavailable")

    sink = MemoryEventSink()
    with pytest.raises(ImportError):
        _NoBackend().execute(
            _one_step("inc"), user_cache_dir=tmp_path / "cache", events=sink
        )
    finished = sink.events[-1]
    assert finished.kind == "run_finished"
    assert (finished.data["status"], finished.data["error_type"]) == (
        "failed", "ImportError",
    )


def test_unknown_target_fails_before_anything_is_written(tmp_path):
    from aa_recipe_manager.exceptions import UnknownStepError

    sink = MemoryEventSink()
    with pytest.raises(UnknownStepError):
        SequentialExecutor().execute(
            _one_step("inc"), user_cache_dir=tmp_path / "cache",
            targets=["nope"], events=sink,
        )
    assert [e.kind for e in sink.events] == ["run_finished"]
    assert not (tmp_path / "outputs").exists()


def test_mapped_failure_names_the_chain(tmp_path):
    sink = MemoryEventSink()
    with pytest.raises(PipelineExecutionError):
        SequentialExecutor().execute(
            H.build(H.map_collect_steps(mapped_callable="boom")),
            user_cache_dir=tmp_path / "cache",
            events=sink,
        )
    failed = sink.of("step_failed")[0].data
    assert (failed["step"], failed["chain"]) == ("proc", ["proc"])


def test_blockers_agree_across_run_log_plan_event_and_preview(tmp_path):
    recipe = FIXTURES / "flat.yaml"
    cache = tmp_path / "cache"
    sink = MemoryEventSink()
    result = api.execute(
        recipe, user_cache_dir=cache, checkpoint_steps=["bump"], events=sink
    )
    preview = api.plan_preview(
        recipe, user_cache_dir=tmp_path / "fresh", checkpoint_steps=["bump"]
    )
    assert sink.of("plan")[0].data["blockers"] == ["start"]
    assert preview.blockers == ["start"]
    assert (
        "these steps are not saved, so later runs start before them: start"
        in result.logs
    )


def test_a_sink_that_prints_is_not_tapped_again(tmp_path):
    seen: list[RunEvent] = []

    def printing(event: RunEvent) -> None:
        seen.append(event)
        if event.kind == "log":
            print(f"sink saw {event.data['text']!r}")

    SequentialExecutor().execute(
        _one_step("noisy"),
        user_cache_dir=tmp_path / "cache",
        events=CallbackEventSink(printing),
    )
    logs = [e for e in seen if e.kind == "log"]
    assert logs
    assert not any("sink saw" in e.data["text"] for e in logs)
    assert seen[-1].kind == "run_finished"


def test_argument_error_still_ends_with_run_finished():
    sink = MemoryEventSink()
    with pytest.raises(ValueError):
        SequentialExecutor().execute(
            _one_step("inc"), log_destination="nowhere", events=sink
        )
    assert [e.kind for e in sink.events] == ["run_finished"]
    assert sink.events[0].data["status"] == "failed"


def test_jsonl_sink_round_trips(tmp_path):
    path = tmp_path / "events.jsonl"
    path.write_text('{"earlier": true}\n', encoding="utf-8")
    memory = MemoryEventSink()

    def both(event: RunEvent) -> None:
        memory.emit(event)
        jsonl.emit(event)

    with JsonlEventSink(path) as jsonl:
        api.execute(
            FIXTURES / "flat.yaml",
            user_cache_dir=tmp_path / "cache",
            events=CallbackEventSink(both),
        )
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"earlier": true}'
    read = [RunEvent.from_dict(json.loads(line)) for line in lines[1:]]
    assert [(e.seq, e.kind) for e in read] == [(e.seq, e.kind) for e in memory.events]
    assert read[-1].to_dict() == json.loads(json.dumps(memory.events[-1].to_dict()))


def test_progress_event_sink_prints_the_cli_lines(tmp_path, capsys):
    sink = MemoryEventSink()
    api.execute(
        FIXTURES / "mapped.yaml",
        user_cache_dir=tmp_path,
        progress=_CLIProgress(),
        events=sink,
    )
    direct = capsys.readouterr().out.splitlines()
    replay = ProgressEventSink(_CLIProgress())
    for event in sink.events:
        replay.emit(event)
    assert capsys.readouterr().out.splitlines() == direct
    assert direct[0] == "[1/4] seg ... start"


def test_sink_failure_does_not_fail_the_run(tmp_path):
    def broken(event: RunEvent) -> None:
        if event.kind == "plan":
            raise OSError("disk full")

    result = api.execute(
        FIXTURES / "flat.yaml",
        user_cache_dir=tmp_path,
        events=CallbackEventSink(broken),
    )
    assert result.executed_steps == ["start", "bump", "scaled"]
    assert "warning: event sink failed: OSError('disk full')" in result.logs


_CONSOLE_RECIPE = """
recipe:
  name: console_note
  version: "1.0"
  schema_version: "1"
steps:
  - id: start
    op: custom
    custom_spec:
      description: Return the constant 7.
      callable_path: _stage9_helpers.const7
      outputs:
        v: {type: int}
      output_map: {v: __return__}
      dependency: {name: pytest, version: ">=7.0", source: pypi}
  - id: note
    op: custom
    inputs:
      x: ${start.v}
    custom_spec:
      description: Print to the terminal.
      callable_path: _stage9_helpers.console_note
      inputs:
        x: {type: int}
      outputs:
        out: {type: int}
      output_map: {out: __return__}
      dependency: {name: pytest, version: ">=7.0", source: pypi}
"""


def test_cli_events_to_stdout_is_pure_jsonl(cli_runner, tmp_path):
    ran = cli_runner.invoke(
        main,
        [
            "run", str(FIXTURES / "flat.yaml"),
            "--user-cache-dir", str(tmp_path / "cache"),
            "--to", "bump",
            "--events", "-",
        ],
    )
    assert ran.exit_code == 0, ran.output
    events = [json.loads(line) for line in ran.stdout.splitlines()]
    assert events[0]["kind"] == "run_started"
    assert events[0]["data"]["targets"] == ["bump"]
    assert events[-1]["kind"] == "run_finished"
    assert "[1/2] start ... start" in ran.stderr
    assert "Outside the target: 1 step(s)" in ran.stderr

    recipe = tmp_path / "console.yaml"
    recipe.write_text(_CONSOLE_RECIPE, encoding="utf-8")
    ran = cli_runner.invoke(
        main,
        ["run", str(recipe), "--user-cache-dir", str(tmp_path / "c2"), "--events", "-"],
    )
    assert ran.exit_code == 0, ran.output
    for line in ran.stdout.splitlines():
        json.loads(line)
    assert "console-note x=7" in ran.stderr


def test_cli_events_to_file(cli_runner, tmp_path):
    path = tmp_path / "run.jsonl"
    ran = cli_runner.invoke(
        main,
        [
            "run", str(FIXTURES / "flat.yaml"),
            "--user-cache-dir", str(tmp_path / "cache"),
            "--events", str(path),
        ],
    )
    assert ran.exit_code == 0, ran.output
    assert "[1/3] start ... start" in ran.stdout
    kinds = [json.loads(line)["kind"] for line in path.read_text().splitlines()]
    assert kinds[0] == "run_started" and kinds[-1] == "run_finished"


@pytest.mark.slow
def test_process_worker_logs_arrive_buffered(tmp_path, monkeypatch):
    pytest.importorskip("dask.distributed")
    monkeypatch.setenv(
        "PYTHONPATH",
        os.path.dirname(__file__) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    from aa_recipe_manager.executor.dask_executor import DaskExecutor

    sink = MemoryEventSink()
    try:
        DaskExecutor("processes", n_workers=1, threads_per_worker=1).execute(
            _one_step("noisy"), user_cache_dir=tmp_path / "cache", events=sink
        )
    except Exception as exc:  # a locked-down box may forbid spawning
        pytest.skip(f"process cluster unavailable: {exc}")
    logs = [e for e in sink.of("log") if e.data["step"] == "x"]
    assert logs and all(e.data["buffered"] is True for e in logs)
    assert "noisy-start x=3" in logs[0].data["text"]


def test_dask_thread_logs_stream_live(tmp_path):
    pytest.importorskip("dask.distributed")
    from aa_recipe_manager.executor.dask_executor import DaskExecutor

    sink = MemoryEventSink()
    DaskExecutor("threads", n_workers=1, threads_per_worker=2).execute(
        _one_step("noisy"), user_cache_dir=tmp_path / "cache", events=sink
    )
    logs = [e for e in sink.of("log") if e.data["step"] == "x"]
    assert logs and all(e.data["buffered"] is False for e in logs)
    assert "noisy-start x=3" in "".join(e.data["text"] for e in logs)


def test_untargeted_steps_have_no_step_events_but_appear_in_plan_and_run_finished(
    tmp_path,
):
    sink = MemoryEventSink()
    api.execute(
        FIXTURES / "flat.yaml", user_cache_dir=tmp_path, targets=["bump"], events=sink
    )
    assert _step_events(sink, "scaled") == []
    assert sink.of("plan")[0].data["steps"]["scaled"]["planned"] == "untargeted"
    assert sink.of("run_finished")[0].data["untargeted"] == ["scaled"]
    started = sink.of("run_started")[0].data
    assert (started["mode"], started["step_count"], started["total_steps"]) == (
        "to", 2, 3,
    )
    assert [e.data["index"] for e in sink.of("step_started")] == [1, 2]
    assert all(e.data["total"] == 2 for e in sink.of("step_started"))


def test_a_failed_save_inside_a_chain_names_the_member_and_instance(
    tmp_path, monkeypatch
):
    from aa_recipe_manager.executor.tiered import TieredCheckpointStore

    real = TieredCheckpointStore.save

    def failing(self, step_id, outputs, **kwargs):
        if step_id == "proc2":
            raise OSError("disk full")
        return real(self, step_id, outputs, **kwargs)

    monkeypatch.setattr(TieredCheckpointStore, "save", failing)
    sink = MemoryEventSink()
    with pytest.raises(OSError):
        api.execute(
            FIXTURES / "mapped.yaml",
            user_cache_dir=tmp_path / "cache",
            checkpoint_mode="eager",
            events=sink,
        )
    failed = sink.of("step_failed")[0].data
    assert (failed["step"], failed["instance"], failed["item"]) == ("proc2", 0, 10)
    assert failed["error_type"] == "OSError"


def test_cli_names_the_member_a_raw_error_came_from(
    cli_runner, tmp_path, monkeypatch
):
    from aa_recipe_manager.executor.tiered import TieredCheckpointStore

    def failing(self, step_id, outputs, **kwargs):
        raise FileNotFoundError("no such file: x.partial")

    monkeypatch.setattr(TieredCheckpointStore, "save", failing)
    ran = cli_runner.invoke(
        main,
        ["run", str(FIXTURES / "mapped.yaml"), "--user-cache-dir",
         str(tmp_path / "cache"), "--to", "proc"],
    )
    assert ran.exit_code == 1
    assert "Step 'proc' failed (instance 0, 10):" in ran.output
