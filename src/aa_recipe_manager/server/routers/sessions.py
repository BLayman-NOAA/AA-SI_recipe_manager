# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Sessions: open a recipe, read its files, set inputs and run settings.

Also where a session's step outputs are saved, for the echogram pane.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response

from aa_recipe_manager import api
from aa_recipe_manager.server.errors import ActiveRunExists, StepNotFound
from aa_recipe_manager.server.models import (
    FileTextResponse,
    OpenSessionRequest,
    SessionResponse,
    SessionSummary,
    SetInputsRequest,
    SetRunConfigRequest,
    StepArtifactsResponse,
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
    """Close a session and delete its folder. The recipe files are untouched.

    A session with a run that has not finished is refused; cancel it first.
    """
    running = request.app.state.runs.active_run(sid)
    if running is not None:
        raise ActiveRunExists(
            f"Session {sid!r} has an unfinished run {running}; cancel it first."
        )
    store(request).delete(sid)
    request.app.state.runs.forget_session(sid)
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


@router.get("/{sid}/steps/{step}/artifacts", response_model=StepArtifactsResponse)
def step_artifacts(
    sid: str,
    step: str,
    request: Request,
    output: str | None = None,
    var: str | None = None,
    start: str | None = None,
    end: str | None = None,
    resolve: bool = True,
):
    """Where a step's saved outputs are, and what of them the echogram pane draws.

    ``output`` and ``var`` choose what to draw, and ``start`` and ``end`` (ISO
    times or nanoseconds) limit a mapped step to a time window. With
    ``resolve=false`` only the artifacts are listed: nothing is read or
    mounted, and ``echogram.resolution`` is null.
    """
    arguments = store(request).cache_args(sid)
    outputs_dir = arguments.pop("outputs_dir")
    try:
        found = api.step_artifacts(step_id=step, **arguments).to_dict()
    except api.UnknownStepError as error:
        raise StepNotFound(str(error), step) from error
    service = request.app.state.echogram
    if not resolve:
        found["echogram"] = {
            "available": service.available,
            "reason": service.reason,
            "resolution": None,
        }
        return found
    service.allow(
        [arguments["user_cache_dir"], arguments["survey_cache_dir"], outputs_dir]
    )
    found["echogram"] = service.resolve(
        found,
        output=output,
        var=var,
        start=start,
        end=end,
        storage_options=arguments["storage_options"],
    )
    return found
