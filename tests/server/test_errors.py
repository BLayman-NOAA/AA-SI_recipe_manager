# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Exceptions become one JSON error body."""

from __future__ import annotations

from fastapi.testclient import TestClient

from aa_recipe_manager.exceptions import RecipeValidationError
from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.errors import translate_error

from .conftest import BASE_URL


def test_validation_error_lists_its_errors():
    status, body = translate_error(RecipeValidationError(["a", "b"]))
    assert status == 422
    assert body["error"] == "RecipeValidationError"
    assert body["details"] == ["a", "b"]


def test_unexpected_error_is_500_with_traceback(config):
    app = create_app(config)

    def broken():
        raise RuntimeError("boom")

    app.state.sessions.list = broken
    client = TestClient(app, base_url=BASE_URL, raise_server_exceptions=False)
    response = client.get("/api/sessions")
    assert response.status_code == 500
    body = response.json()
    assert (body["error"], body["message"]) == ("RuntimeError", "boom")
    assert "Traceback" in body["details"]


def test_unknown_route_uses_the_error_body(client):
    response = client.get("/api/nothing")
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
