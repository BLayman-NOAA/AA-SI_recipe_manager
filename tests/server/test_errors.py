# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Exceptions become one JSON error body."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from aa_recipe_manager.exceptions import (
    RecipeValidationError,
    UpstreamNotCachedError,
)
from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.errors import (
    ActiveRunExists,
    InvalidRecipe,
    RunNotActive,
    RunNotFound,
    StepNotFound,
    translate_error,
)

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


@pytest.mark.parametrize(
    ("exc", "status"),
    [
        (RunNotFound("No run 'x'."), 404),
        (ActiveRunExists("Run x of this session has not finished."), 409),
        (RunNotActive("Run x has already completed."), 409),
    ],
)
def test_run_errors(exc, status):
    code, body = translate_error(exc)
    assert code == status
    assert body["error"] == type(exc).__name__
    assert body["details"] is None


def test_upstream_not_cached_is_409_with_the_missing_steps():
    status, body = translate_error(UpstreamNotCachedError(["c"], ["b", "a"]))
    assert status == 409
    assert body["error"] == "UpstreamNotCachedError"
    assert body["details"] == {"targets": ["c"], "missing": ["a", "b"]}


def test_invalid_recipe_is_422_with_its_problems():
    problems = [{"severity": "error", "message": "needed is missing", "step": "s"}]
    status, body = translate_error(InvalidRecipe("The recipe has errors.", problems))
    assert status == 422
    assert body["error"] == "InvalidRecipe"
    assert body["details"] == problems


def test_step_not_found_is_404_naming_the_step():
    status, body = translate_error(StepNotFound("unknown step id(s): 'x'", "x"))
    assert status == 404
    assert body["error"] == "StepNotFound"
    assert body["step_id"] == "x"
