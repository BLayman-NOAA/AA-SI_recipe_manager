# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The OpenAPI export the web client's types are generated from."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aa_recipe_manager.server.openapi import main, openapi_text

COMMITTED = Path(__file__).parents[2] / "web" / "src" / "api" / "openapi.json"
PATHS = {
    "/api/health",
    "/api/recipes",
    "/api/ops",
    "/api/ops/{op}",
    "/api/sessions",
    "/api/sessions/{sid}",
    "/api/sessions/{sid}/files/{path}",
    "/api/sessions/{sid}/inputs",
    "/api/sessions/{sid}/config",
}


def test_export_is_json_with_every_route(tmp_path):
    out = tmp_path / "openapi.json"
    main([str(out)])
    document = json.loads(out.read_text(encoding="utf-8"))
    assert set(document["paths"]) == PATHS
    assert "SessionResponse" in document["components"]["schemas"]
    assert "ErrorResponse" in document["components"]["schemas"]
    assert out.read_text(encoding="utf-8") == openapi_text()


@pytest.mark.skipif(
    not COMMITTED.is_file(), reason="web/src/api/openapi.json is not committed yet"
)
def test_committed_export_is_current():
    assert COMMITTED.read_text(encoding="utf-8") == openapi_text(), (
        "The API changed. Run python -m aa_recipe_manager.server.openapi "
        "web/src/api/openapi.json and npm run gen:api, then commit both."
    )
