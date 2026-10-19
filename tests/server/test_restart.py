# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Runs left active by a server that stopped are picked up on start."""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict

import psutil
from fastapi.testclient import TestClient

from aa_recipe_manager.server import runs as runs_module
from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.runs import RunRecord
from aa_recipe_manager.server.tail import read_events

from .conftest import BASE_URL, open_session, wait_until


def session_id(config) -> str:
    return open_session(TestClient(create_app(config), base_url=BASE_URL), "flat.yaml")[
        "id"
    ]


def ended(client, rid: str) -> dict | None:
    run = client.get(f"/api/runs/{rid}").json()
    return None if run["status"] == "running" else run


def write_run(config, sid: str, rid: str, pid: int, created: float, **fields):
    folder = config.state_dir / "sessions" / sid / "runs" / rid
    folder.mkdir(parents=True)
    record = RunRecord(
        id=rid,
        session=sid,
        created="2026-10-14T10:00:00.000Z",
        mode="all",
        targets=[],
        force=False,
        force_steps=[],
        executor=None,
        outputs_dir=None,
        log=(folder / "run.log").as_posix(),
        started="2026-10-14T10:00:00.000Z",
        status="running",
        pid=pid,
        pid_created=created,
        last_seq=1,
        **fields,
    )
    (folder / "run.json").write_text(json.dumps(asdict(record)), encoding="utf-8")
    event = {"seq": 1, "time": 0.0, "kind": "run_started", "run_id": "x", "data": {}}
    (folder / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    return folder


def test_dead_run_is_interrupted(config):
    sid = session_id(config)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    folder = write_run(config, sid, "20261014-100000-aaaaaa", gone.pid, 0.0)

    with TestClient(create_app(config), base_url=BASE_URL) as client:
        run = client.get("/api/runs/20261014-100000-aaaaaa").json()
        assert run["status"] == "interrupted"
        assert run["finished"] is not None
        assert run["last_seq"] == 2
        listed = client.get(f"/api/sessions/{sid}/runs").json()
        assert [item["status"] for item in listed] == ["interrupted"]

    events = read_events(folder / "events.jsonl")
    assert [event["seq"] for event in events] == [1, 2]
    assert events[-1]["kind"] == "run_finished"
    assert events[-1]["data"]["status"] == "interrupted"
    assert events[-1]["data"]["synthetic"] is True
    saved = json.loads((folder / "run.json").read_text(encoding="utf-8"))
    assert saved["status"] == "interrupted"


def finished_event(seq: int, status: str, manifest: str | None) -> dict:
    return {
        "seq": seq,
        "time": 1.0,
        "kind": "run_finished",
        "run_id": "x",
        "data": {
            "status": status,
            "seconds": 1.0,
            "manifest": manifest,
            "executed": ["a", "b"],
            "skipped": [],
            "pruned": [],
            "untargeted": ["c"],
            "error_type": None,
            "message": None,
        },
    }


def test_dead_run_that_finished_keeps_its_outcome(config, tmp_path):
    sid = session_id(config)
    gone = subprocess.Popen([sys.executable, "-c", "pass"])
    gone.wait()
    rid = "20261014-100000-dddddd"
    folder = write_run(config, sid, rid, gone.pid, 0.0)
    manifest = tmp_path / "outputs" / "manifest.json"
    manifest.parent.mkdir()
    manifest.write_text('{"status": "completed"}', encoding="utf-8")
    with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(finished_event(2, "completed", str(manifest))) + "\n")

    with TestClient(create_app(config), base_url=BASE_URL) as client:
        run = client.get(f"/api/runs/{rid}").json()
    assert run["status"] == "completed"
    assert run["exit_code"] is None
    assert run["summary"]["executed"] == 2
    assert run["summary"]["untargeted"] == 1
    assert run["last_seq"] == 2
    assert run["manifest"] == (folder / "manifest.json").as_posix()
    assert (folder / "manifest.json").read_text(encoding="utf-8") == (
        manifest.read_text(encoding="utf-8")
    )
    events = read_events(folder / "events.jsonl")
    assert [event["seq"] for event in events] == [1, 2]
    assert "synthetic" not in events[-1]["data"]


def test_unreadable_live_pid_is_treated_as_gone(config, monkeypatch):
    sid = session_id(config)
    rid = "20261014-100000-eeeeee"
    folder = write_run(config, sid, rid, 12345, 0.0)

    def denied(pid):
        raise psutil.AccessDenied(pid)

    monkeypatch.setattr(runs_module.procs, "is_alive", lambda pid, created: True)
    monkeypatch.setattr(runs_module.psutil, "Process", denied)
    with TestClient(create_app(config), base_url=BASE_URL) as client:
        assert client.get(f"/api/runs/{rid}").json()["status"] == "interrupted"
    assert read_events(folder / "events.jsonl")[-1]["data"]["synthetic"] is True


def test_live_run_is_followed_until_it_ends(config):
    sid = session_id(config)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        created = psutil.Process(process.pid).create_time()
        rid = "20261014-100000-bbbbbb"
        folder = write_run(config, sid, rid, process.pid, created)
        with TestClient(create_app(config), base_url=BASE_URL) as client:
            assert client.get(f"/api/runs/{rid}").json()["status"] == "running"
            response = client.delete(
                f"/api/sessions/{sid}",
                headers={"X-Recipe-Client": "1", "Origin": BASE_URL},
            )
            assert response.status_code == 409

            process.kill()
            process.wait()
            run = wait_until(lambda: ended(client, rid), timeout=20)
            assert run["status"] == "interrupted"
            assert run["exit_code"] is None
    finally:
        process.kill()
        process.wait()
    events = read_events(folder / "events.jsonl")
    assert events[-1]["data"]["status"] == "interrupted"


def test_recovered_run_already_finished_keeps_its_status(config):
    sid = session_id(config)
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        created = psutil.Process(process.pid).create_time()
        rid = "20261014-100000-cccccc"
        folder = write_run(config, sid, rid, process.pid, created)
        finished = {
            "seq": 2,
            "time": 1.0,
            "kind": "run_finished",
            "run_id": "x",
            "data": {
                "status": "completed",
                "seconds": 1.0,
                "manifest": None,
                "executed": ["a", "b"],
                "skipped": [],
                "pruned": [],
                "untargeted": [],
                "error_type": None,
                "message": None,
            },
        }
        with open(folder / "events.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(finished) + "\n")
        with TestClient(create_app(config), base_url=BASE_URL) as client:
            process.kill()
            process.wait()
            run = wait_until(lambda: ended(client, rid), timeout=20)
            assert run["status"] == "completed"
            assert run["summary"]["executed"] == 2
    finally:
        process.kill()
        process.wait()
