# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Fixtures for the client server tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.config import ServerConfig

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
