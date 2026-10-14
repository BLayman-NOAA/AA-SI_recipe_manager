# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Requests from anything but the local client are refused."""

from __future__ import annotations

from dataclasses import replace

from fastapi.testclient import TestClient

from aa_recipe_manager.server.app import create_app

from .conftest import BASE_URL, FIXTURES

OPEN_BODY = {"path": (FIXTURES / "flat.yaml").resolve().as_posix()}


def test_foreign_host_is_refused(config):
    client = TestClient(create_app(config), base_url="http://evil:8765")
    response = client.get("/api/health")
    assert response.status_code == 403
    assert response.json()["error"] == "forbidden_host"


def test_wrong_port_is_refused(config):
    client = TestClient(create_app(config), base_url="http://127.0.0.1:9999")
    assert client.get("/api/health").status_code == 403


def test_loopback_names_are_accepted(config):
    for base in ("http://localhost:8765", BASE_URL):
        client = TestClient(create_app(config), base_url=base)
        assert client.get("/api/health").status_code == 200, base


def test_post_without_client_header_is_refused(client):
    response = client.post("/api/sessions", json=OPEN_BODY)
    assert response.status_code == 403
    assert response.json()["error"] == "missing_client_header"


def test_post_from_foreign_origin_is_refused(client):
    headers = {"X-Recipe-Client": "1", "Origin": "http://evil.example"}
    response = client.post("/api/sessions", json=OPEN_BODY, headers=headers)
    assert response.status_code == 403
    assert response.json()["error"] == "foreign_origin"


def test_post_without_origin_is_accepted(client):
    response = client.post(
        "/api/sessions", json=OPEN_BODY, headers={"X-Recipe-Client": "1"}
    )
    assert response.status_code == 201


def test_cors_preflight_is_not_granted(client):
    response = client.options(
        "/api/sessions",
        headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "x-recipe-client",
        },
    )
    assert response.status_code != 200
    assert "access-control-allow-origin" not in response.headers


def test_vite_origin_only_in_dev(config):
    headers = {"X-Recipe-Client": "1", "Origin": "http://localhost:5173"}
    client = TestClient(create_app(config), base_url=BASE_URL)
    response = client.post("/api/sessions", json=OPEN_BODY, headers=headers)
    assert response.status_code == 403

    dev_client = TestClient(create_app(replace(config, dev=True)), base_url=BASE_URL)
    response = dev_client.post("/api/sessions", json=OPEN_BODY, headers=headers)
    assert response.status_code == 201
