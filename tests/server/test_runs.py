# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Starting, following, cancelling and killing runs."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import psutil
import pytest
from fastapi.testclient import TestClient

from aa_recipe_manager.server import procs
from aa_recipe_manager.server import runs as runs_module
from aa_recipe_manager.server.runs import RunManager

from .conftest import (
    BASE_URL,
    FIXTURES,
    HEADERS,
    run_events,
    run_session,
    start,
    wait_for,
    wait_for_event,
)

ENDED = {"completed", "failed", "cancelled", "interrupted"}
INVALID_STEP = """\
recipe: {name: needs_param, version: '1.0', schema_version: '1'}
steps:
  - id: lonely
    op: custom
    custom_spec:
      description: Return the constant 7.
      callable_path: _stage9_helpers.const7
      params:
        needed: {type: int}
      dependency: {name: pytest, version: ">=7.0", source: pypi}
"""


def dispositions(events: list[dict]) -> dict[str, str]:
    return {
        event["data"]["step"]: event["data"]["disposition"]
        for event in events
        if event["kind"] == "step_finished"
    }


def test_start_run_flat_completes(run_client, config, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    run = start(run_client, sid)
    assert run["status"] == "starting"
    assert run["pid"] and run["mode"] == "all"
    assert run["schema"] == 1
    assert run["cwd"] == FIXTURES.resolve().as_posix()

    done = wait_for(run_client, run["id"], ENDED)
    assert done["status"] == "completed"
    assert done["exit_code"] == 0
    assert done["summary"]["executed"] == 3
    run_dir = config.state_dir / "sessions" / sid / "runs" / run["id"]
    assert Path(done["manifest"]) == run_dir / "manifest.json"
    assert json.loads(Path(done["manifest"]).read_text())["status"] == "completed"
    assert done["outputs_dir"] == (run_dir / "outputs").as_posix()
    job = json.loads((run_dir / "job.json").read_text(encoding="utf-8"))
    assert job["schema"] == 1 and job["files"].keys() == {"flat.yaml"}

    events = run_events(config, done)
    assert events[0]["kind"] == "run_started"
    assert events[-1]["kind"] == "run_finished"
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert done["last_seq"] == len(events)

    # A run of everything saves nothing (no step is a checkpoint); a run to
    # the last step pins it, so the run after that loads it.
    second = start(run_client, sid, mode="to", targets=["scaled"])
    assert wait_for(run_client, second["id"], ENDED)["status"] == "completed"
    third = start(run_client, sid, mode="to", targets=["scaled"])
    third = wait_for(run_client, third["id"], ENDED)
    assert dispositions(run_events(config, third))["scaled"] == "hit-user-cache"
    assert third["summary"]["executed"] == 0

    listed = run_client.get(f"/api/sessions/{sid}/runs").json()
    assert [item["id"] for item in listed] == [third["id"], second["id"], run["id"]]
    assert run_client.get(f"/api/runs/{run['id']}").json()["status"] == "completed"


def test_run_to_target_and_only(run_client, config, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    response = run_client.post(
        f"/api/sessions/{sid}/runs",
        json={"mode": "only", "targets": ["scaled"]},
        headers=HEADERS,
    )
    assert response.status_code == 409
    body = response.json()
    assert body["error"] == "UpstreamNotCachedError"
    assert body["details"] == {"targets": ["scaled"], "missing": ["bump"]}
    assert not (config.state_dir / "sessions" / sid / "runs").exists()

    to = start(run_client, sid, mode="to", targets=["bump"])
    to = wait_for(run_client, to["id"], ENDED)
    assert to["status"] == "completed"
    assert to["summary"]["untargeted"] == 1
    assert to["summary"]["executed"] == 2

    only = start(run_client, sid, mode="only", targets=["scaled"])
    only = wait_for(run_client, only["id"], ENDED)
    assert only["status"] == "completed"
    assert only["summary"]["executed"] == 1
    assert dispositions(run_events(config, only))["bump"] == "hit-user-cache"


def test_start_refusals(run_app, config, tmp_path):
    client = TestClient(run_app, base_url=BASE_URL)
    run_app.state.runs.config = replace(config, cancel_grace_seconds=1)
    sid = run_session(client, "slow.yaml", tmp_path / "cache", {"seconds": 30})
    url = f"/api/sessions/{sid}/runs"

    response = client.post(
        url, json={"mode": "to", "targets": ["nope"]}, headers=HEADERS
    )
    assert response.status_code == 400
    assert "after" in response.json()["message"]
    response = client.post(url, json={"force_steps": ["nope"]}, headers=HEADERS)
    assert response.status_code == 400
    response = client.post(url, json={"targets": ["slow"]}, headers=HEADERS)
    assert response.status_code == 400
    assert "mode 'to'" in response.json()["message"]
    response = client.post(url, json={"mode": "only"}, headers=HEADERS)
    assert response.status_code == 400
    response = client.post(url, json={"mode": "sideways"}, headers=HEADERS)
    assert response.status_code == 422

    run = start(client, sid)
    response = client.post(url, json={}, headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"] == "ActiveRunExists"
    assert client.post(f"/api/runs/{run['id']}/cancel", headers=HEADERS).is_success
    assert wait_for(client, run["id"], ENDED)["status"] == "cancelled"
    response = client.post(f"/api/runs/{run['id']}/cancel", headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"] == "RunNotActive"

    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    (root / "invalid.yaml").write_text(INVALID_STEP, encoding="utf-8")
    response = client.post(
        "/api/sessions",
        json={"path": (root / "invalid.yaml").as_posix()},
        headers=HEADERS,
    )
    invalid = response.json()
    assert invalid["validation"]["valid"] is False
    response = client.post(
        f"/api/sessions/{invalid['id']}/runs", json={}, headers=HEADERS
    )
    assert response.status_code == 422
    assert response.json()["error"] == "InvalidRecipe"
    assert response.json()["details"] == invalid["validation"]["problems"]


def test_unknown_run_and_session(run_client, headers):
    assert run_client.get("/api/runs/20260101-000000-abcdef").status_code == 404
    assert run_client.get("/api/runs/../../x").status_code == 404
    for action in ("cancel", "stop"):
        response = run_client.post(
            f"/api/runs/20260101-000000-abcdef/{action}", headers=headers
        )
        assert response.status_code == 404
        assert response.json()["error"] == "RunNotFound"
    assert (
        run_client.get("/api/sessions/20260101-000000-abcdef/runs").status_code == 404
    )


def test_a_watcher_error_does_not_block_the_session(
    run_client, config, tmp_path, monkeypatch
):
    def broken(self, record, manifest):
        raise OSError("disk full")

    monkeypatch.setattr(RunManager, "_copy_manifest", broken)
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    run = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    assert run["status"] == "interrupted"
    assert "disk full" in run["summary"]["message"]
    last = run_events(config, run)[-1]
    assert last["data"]["synthetic"] is True

    again = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    assert again["status"] == "interrupted"


def test_a_manifest_that_cannot_be_copied_is_skipped(
    run_client, config, tmp_path, monkeypatch
):
    def locked(source, target):
        raise PermissionError(13, "locked by a scanner", str(source))

    monkeypatch.setattr(runs_module.shutil, "copyfile", locked)
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    run = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    assert run["status"] == "completed"
    assert run["manifest"] is None
    events = run_events(config, run)
    assert [event["kind"] for event in events].count("run_finished") == 1
    assert "synthetic" not in events[-1]["data"]


def test_failed_run_exit_code_and_log(run_client, tmp_path):
    sid = run_session(run_client, "failing.yaml", tmp_path / "cache")
    run = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    assert run["status"] == "failed"
    assert run["exit_code"] == 1
    assert run["summary"]["failed_step"] == "blow"
    assert run["summary"]["executed"] == 1
    assert "boom" in run["summary"]["message"]

    log = run_client.get(f"/api/runs/{run['id']}/log", params={"step": "blow"}).json()
    assert log["run_id"] == run["id"] and log["step"] == "blow"
    tracebacks = [entry for entry in log["entries"] if entry["stream"] == "traceback"]
    assert len(tracebacks) == 1
    assert "RuntimeError: boom" in tracebacks[0]["text"]
    assert log["truncated"] is False
    only_tracebacks = run_client.get(
        f"/api/runs/{run['id']}/log", params={"stream": "traceback"}
    ).json()
    assert [entry["step"] for entry in only_tracebacks["entries"]] == ["blow"]


def test_cancel_by_file(run_client, config, tmp_path):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache", {"seconds": 5})
    run = start(run_client, sid)
    wait_for_event(config, run, "step_started", "slow")
    assert wait_for(run_client, run["id"], "running")["status"] == "running"

    response = run_client.post(f"/api/runs/{run['id']}/cancel", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["cancel_requested_at"] is not None

    done = wait_for(run_client, run["id"], ENDED)
    assert done["status"] == "cancelled"
    assert done["exit_code"] == 2
    assert done["killed"] is False
    events = run_events(config, done)
    assert events[-1]["kind"] == "run_finished"
    assert events[-1]["data"]["status"] == "cancelled"
    assert "synthetic" not in events[-1]["data"]
    manifest = json.loads(Path(done["manifest"]).read_text(encoding="utf-8"))
    assert manifest["steps"]["start"]["disposition"] == "computed"
    assert "after" not in dispositions(events)


def test_kill_leaves_server_and_session_usable(run_app, run_client, config, tmp_path):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache", {"seconds": 60})
    run = start(run_client, sid)
    wait_for_event(config, run, "step_started", "slow")

    run_app.state.runs.kill(run["id"])
    done = wait_for(run_client, run["id"], ENDED, timeout=20)
    assert done["status"] == "cancelled"
    assert done["killed"] is True
    assert not procs.is_alive(run["pid"], run["pid_created"])
    last = run_events(config, done)[-1]
    assert last["kind"] == "run_finished"
    assert last["data"]["synthetic"] is True
    assert last["data"]["message"] == "The run was stopped by the server."
    assert last["seq"] == done["last_seq"]
    # The step that finished before the kill is counted.
    assert last["data"]["executed"] == ["start"]
    assert done["summary"]["executed"] == 1

    assert run_client.get("/api/health").status_code == 200
    assert run_client.get(f"/api/sessions/{sid}").status_code == 200
    response = run_client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"seconds": 0}}, headers=HEADERS
    )
    assert response.status_code == 200
    again = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    assert again["status"] == "completed"


def test_process_killed_from_outside_is_failed(run_client, config, tmp_path):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache", {"seconds": 60})
    run = start(run_client, sid)
    wait_for_event(config, run, "step_started", "slow")

    psutil.Process(run["pid"]).kill()
    done = wait_for(run_client, run["id"], ENDED, timeout=20)
    assert done["status"] == "failed"
    assert done["killed"] is False
    message = done["summary"]["message"]
    assert message.startswith("The run process stopped unexpectedly (exit code ")
    assert f"exit code {done['exit_code']})" in message
    assert "killed outside the client" in message
    assert done["summary"]["executed"] == 1
    assert run_events(config, done)[-1]["data"]["synthetic"] is True


def test_stop_now_kills_the_running_step(run_client, config, tmp_path):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache", {"seconds": 60})
    run = start(run_client, sid)
    wait_for_event(config, run, "step_started", "slow")
    # A cancel waits for the step; stop now does not.
    assert run_client.post(f"/api/runs/{run['id']}/cancel", headers=HEADERS).is_success

    response = run_client.post(f"/api/runs/{run['id']}/stop", headers=HEADERS)
    assert response.status_code == 200
    assert response.json()["stop_requested_at"] is not None
    assert response.json()["killed"] is True
    done = wait_for(run_client, run["id"], ENDED, timeout=10)
    assert done["status"] == "cancelled"
    assert done["killed"] is True
    assert not procs.is_alive(run["pid"], run["pid_created"])
    events = run_events(config, done)
    last = events[-1]
    assert last["data"]["synthetic"] is True
    assert last["data"]["message"] == "The run was stopped immediately while slow was running."
    assert done["summary"]["message"] == last["data"]["message"]
    assert dispositions(events) == {"start": "computed"}

    response = run_client.post(f"/api/runs/{run['id']}/stop", headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"] == "RunNotActive"


def test_grace_kill_after_cancel(config, tmp_path, run_app):
    run_app.state.runs.config = replace(config, cancel_grace_seconds=1)
    client = TestClient(run_app, base_url=BASE_URL)
    sid = run_session(client, "slow.yaml", tmp_path / "cache", {"seconds": 60})
    run = start(client, sid)
    wait_for_event(config, run, "step_started", "slow")

    assert client.post(f"/api/runs/{run['id']}/cancel", headers=HEADERS).is_success
    done = wait_for(client, run["id"], ENDED, timeout=20)
    assert done["status"] == "cancelled"
    assert done["killed"] is True


def test_delete_session_with_active_run_is_409(run_app, run_client, config, tmp_path):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache", {"seconds": 60})
    run = start(run_client, sid)
    response = run_client.delete(f"/api/sessions/{sid}", headers=HEADERS)
    assert response.status_code == 409
    assert response.json()["error"] == "ActiveRunExists"

    run_app.state.runs.kill(run["id"])
    wait_for(run_client, run["id"], ENDED, timeout=20)
    assert run_client.delete(f"/api/sessions/{sid}", headers=HEADERS).status_code == 204

    # The deleted session's runs are gone too.
    assert run_client.get(f"/api/runs/{run['id']}").status_code == 404
    assert run_client.get(f"/api/runs/{run['id']}/events").status_code == 404
    assert run_client.get(f"/api/runs/{run['id']}/log").status_code == 404
    assert run_app.state.runs.active_run(sid) is None


def test_relative_outputs_dir_is_honoured(run_client, tmp_path):
    root = tmp_path / "recipes"
    root.mkdir()
    run_client.app.state.config.recipe_roots.append(root)
    source = Path(__file__).parents[1] / "fixtures" / "client_recipes" / "flat.yaml"
    (root / "flat.yaml").write_text(
        source.read_text(encoding="utf-8"), encoding="utf-8"
    )
    response = run_client.post(
        "/api/sessions", json={"path": (root / "flat.yaml").as_posix()}, headers=HEADERS
    )
    sid = response.json()["id"]
    body = {"user_cache_dir": "./cache", "outputs_dir": "./out"}
    run_client.put(f"/api/sessions/{sid}/config", json=body, headers=HEADERS)
    run = start(run_client, sid, mode="to", targets=["scaled"])
    run = wait_for(run_client, run["id"], ENDED)
    assert run["status"] == "completed"
    assert run["outputs_dir"] == "./out"
    assert (root / "out" / "manifest.json").is_file()
    assert (root / "cache").is_dir()
    assert Path(run["manifest"]).parent.name == run["id"]


@pytest.mark.slow
def test_kill_dask_run_leaves_no_workers(run_app, run_client, config, tmp_path):
    pytest.importorskip("dask.distributed")
    sid = run_session(
        run_client,
        "slow.yaml",
        tmp_path / "cache",
        {"seconds": 60},
        executor="dask",
        dask_scheduler="processes",
        dask_workers=2,
    )
    run = start(run_client, sid)
    try:
        wait_for_event(config, run, "step_started", "slow", timeout=90)
    except AssertionError:
        pytest.skip("process cluster unavailable")
    root = psutil.Process(run["pid"])
    tree = root.children(recursive=True)
    assert len(tree) >= 2

    run_app.state.runs.kill(run["id"])
    psutil.wait_procs(tree, timeout=15)
    assert not [p for p in [root, *tree] if procs.running(p)]
    done = wait_for(run_client, run["id"], ENDED, timeout=20)
    assert done["status"] == "cancelled" and done["killed"] is True
