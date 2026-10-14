# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Health endpoint and the client page at the root."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aa_recipe_manager import __version__
from aa_recipe_manager.server import app as app_module
from aa_recipe_manager.server.app import create_app

from .conftest import BASE_URL


def test_health(client, config):
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["version"] == __version__
    assert body["dev"] is False
    assert body["state_dir"] == config.state_dir.as_posix()
    assert body["docs_url"] == config.docs_url
    assert body["docs_url"].endswith("/")


def test_root_explains_that_the_client_is_not_built(config, monkeypatch, tmp_path):
    monkeypatch.setattr(app_module, "STATIC_DIR", tmp_path / "no_static")
    client = TestClient(create_app(config), base_url=BASE_URL)
    response = client.get("/")
    assert response.status_code == 200
    assert "not built" in response.json()["message"]


def test_root_serves_the_built_client(config, monkeypatch, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html>client</html>", encoding="utf-8")
    monkeypatch.setattr(app_module, "STATIC_DIR", static)
    client = TestClient(create_app(config), base_url=BASE_URL)
    response = client.get("/")
    assert response.status_code == 200
    assert "client" in response.text
    assert client.get("/api/health").json()["status"] == "ok"
