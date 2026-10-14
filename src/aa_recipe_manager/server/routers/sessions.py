# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Sessions: open a recipe, read its files, set inputs and run settings."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from aa_recipe_manager.server.models import (
    FileTextResponse,
    OpenSessionRequest,
    SessionResponse,
    SessionSummary,
    SetInputsRequest,
    SetRunConfigRequest,
)
from aa_recipe_manager.server.routers import ERROR_RESPONSES
from aa_recipe_manager.server.sessions import SessionStore

router = APIRouter(prefix="/sessions", tags=["Sessions"], responses=ERROR_RESPONSES)


def store(request: Request) -> SessionStore:
    return request.app.state.sessions


@router.get("", response_model=list[SessionSummary])
def list_sessions(request: Request):
    """Saved sessions, newest first."""
    return store(request).list()


@router.post("", response_model=SessionResponse, status_code=201)
def open_session(body: OpenSessionRequest, request: Request):
    """Open a recipe file under a recipe root in a new session."""
    return store(request).create(body.path)


@router.get("/{sid}", response_model=SessionResponse)
def get_session(sid: str, request: Request):
    """A session with its graph, inputs, run settings and validation."""
    return store(request).get(sid)


@router.delete("/{sid}", status_code=204, response_class=Response)
def close_session(sid: str, request: Request):
    """Close a session and delete its folder. The recipe files are untouched."""
    store(request).delete(sid)
    return Response(status_code=204)


@router.get("/{sid}/files/{path:path}", response_model=FileTextResponse)
def file_text(sid: str, path: str, request: Request):
    """The text of one of the session's recipe files, by its listed path."""
    return {"path": path, "text": store(request).file_text(sid, path)}


@router.put("/{sid}/inputs", response_model=SessionResponse)
def set_inputs(sid: str, body: SetInputsRequest, request: Request):
    """Replace the input values the session sets."""
    return store(request).set_inputs(sid, body.values)


@router.put("/{sid}/config", response_model=SessionResponse)
def set_run_config(sid: str, body: SetRunConfigRequest, request: Request):
    """Replace the run settings the session overrides."""
    return store(request).set_run_config(sid, body.model_dump())
