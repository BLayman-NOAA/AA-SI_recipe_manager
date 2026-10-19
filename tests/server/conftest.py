# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Fixtures for the client server tests."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.config import ServerConfig
from aa_recipe_manager.server.runs import ACTIVE_STATUSES
from aa_recipe_manager.server.tail import read_events

FIXTURES = Path(__file__).parent.parent / "fixtures" / "client_recipes"
BASE_URL = "http://127.0.0.1:8765"
HEADERS = {"X-Recipe-Client": "1", "Origin": BASE_URL}


@pytest.fixture(autouse=True)
def no_user_run_config(monkeypatch, tmp_path):
    """Keep a run config in the working folder or home out of the tests."""
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "home")


@pytest.fixture
def config(tmp_path):
    return ServerConfig(
        state_dir=tmp_path / "state", recipe_roots=[FIXTURES.resolve()], port=8765
    )


@pytest.fixture
def client(config):
    return TestClient(create_app(config), base_url=BASE_URL)


@pytest.fixture
def headers():
    return dict(HEADERS)


def open_session(client: TestClient, name: str) -> dict:
    """Open a fixture recipe and return the session document."""
    path = (FIXTURES / name).resolve().as_posix()
    response = client.post("/api/sessions", json={"path": path}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


TESTS = Path(__file__).parent.parent
RUN_DEADLINE = 60.0


@pytest.fixture
def run_app(config, monkeypatch):
    """An app whose runs can import the fixture helpers; kills its runs after."""
    monkeypatch.setenv(
        "PYTHONPATH", str(TESTS) + os.pathsep + os.environ.get("PYTHONPATH", "")
    )
    app = create_app(config)
    yield app
    manager = app.state.runs
    for rid in list(manager._records):
        manager.kill(rid)
    for rid in list(manager._records):
        wait_until(lambda rid=rid: manager.get(rid)["status"] not in ACTIVE_STATUSES)
    manager.shutdown()


@pytest.fixture
def run_client(run_app):
    return TestClient(run_app, base_url=BASE_URL)


def wait_until(condition, timeout: float = RUN_DEADLINE, interval: float = 0.1):
    """Poll until ``condition()`` is truthy and return its value."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out after {timeout} s waiting.")
        time.sleep(interval)


def wait_for(client, rid: str, statuses, timeout: float = RUN_DEADLINE) -> dict:
    """Poll ``GET /api/runs/{rid}`` until its status is one of ``statuses``."""
    statuses = {statuses} if isinstance(statuses, str) else set(statuses)

    def reached():
        run = client.get(f"/api/runs/{rid}").json()
        return run if run["status"] in statuses else None

    return wait_until(reached, timeout)


def run_session(client, name: str, cache: Path, inputs=None, **settings) -> str:
    """Open a fixture recipe with its user cache under ``cache``; return the id."""
    sid = open_session(client, name)["id"]
    body = {"user_cache_dir": cache.as_posix(), **settings}
    response = client.put(f"/api/sessions/{sid}/config", json=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    if inputs:
        response = client.put(
            f"/api/sessions/{sid}/inputs", json={"values": inputs}, headers=HEADERS
        )
        assert response.status_code == 200, response.text
    return sid


def start(client, sid: str, **body) -> dict:
    """Start a run and return its record."""
    response = client.post(f"/api/sessions/{sid}/runs", json=body, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def run_events(config, run: dict) -> list[dict]:
    """The events a run's process wrote so far."""
    path = config.state_dir / "sessions" / run["session"] / "runs" / run["id"]
    return read_events(path / "events.jsonl")


def wait_for_event(config, run: dict, kind: str, step: str | None = None, **kw):
    """Wait until a run has written an event of ``kind`` (for ``step``)."""

    def found():
        for event in run_events(config, run):
            if event["kind"] == kind and (
                step is None or event["data"].get("step") == step
            ):
                return event
        return None

    return wait_until(found, **kw)
