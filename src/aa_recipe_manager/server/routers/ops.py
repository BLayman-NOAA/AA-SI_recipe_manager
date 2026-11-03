# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The op catalog, as the documentation site's ``ops.json``, and the recipe schema."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, FastAPI, HTTPException, Request

from aa_recipe_manager import api
from aa_recipe_manager.server.routers import ERROR_RESPONSES

router = APIRouter(prefix="/ops", tags=["Ops"], responses=ERROR_RESPONSES)
schema_router = APIRouter(prefix="/schema", tags=["Ops"], responses=ERROR_RESPONSES)


def catalog(app: FastAPI) -> dict[str, Any]:
    """The op catalog, built on first use and kept for the server's life."""
    if app.state.ops is None:
        app.state.ops = api.op_catalog()
    return app.state.ops


@router.get("", response_model=dict[str, Any])
def list_ops(request: Request):
    """Every registered op: ``schema``, ``counts``, ``categories`` and ``ops``."""
    return catalog(request.app)


@router.get("/{op}", response_model=dict[str, Any])
def get_op(op: str, request: Request):
    """One op's catalog entry."""
    for entry in catalog(request.app)["ops"]:
        if entry["op"] == op:
            return entry
    raise HTTPException(status_code=404, detail=f"No op named {op!r}.")


@schema_router.get("/recipe", response_model=dict[str, Any])
def recipe_schema():
    """The JSON Schema of a recipe file."""
    return api.export_schema()
