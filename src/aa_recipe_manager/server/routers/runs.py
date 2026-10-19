# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Runs: preview, start, follow, read the log of, cancel and stop a session's runs."""

from __future__ import annotations

import json

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import StreamingResponse

from aa_recipe_manager import api
from aa_recipe_manager.server import tail
from aa_recipe_manager.server.models import (
    LogResponse,
    PreviewResponse,
    RunResponse,
    StartRunRequest,
)
from aa_recipe_manager.server.routers import ERROR_RESPONSES
from aa_recipe_manager.server.runs import ACTIVE_STATUSES, RunManager

router = APIRouter(tags=["Runs"], responses=ERROR_RESPONSES)

#: Seconds without an event before the event stream sends a comment.
KEEPALIVE_SECONDS = 15.0


def runs(request: Request) -> RunManager:
    return request.app.state.runs


@router.post("/sessions/{sid}/runs", response_model=RunResponse, status_code=201)
def start_run(sid: str, body: StartRunRequest, request: Request):
    """Start a run of the session in a new process."""
    return runs(request).start(
        sid,
        mode=body.mode,
        targets=body.targets,
        force=body.force,
        force_steps=body.force_steps,
    )


@router.get("/sessions/{sid}/runs", response_model=list[RunResponse])
def list_runs(sid: str, request: Request):
    """The session's runs, newest first."""
    return runs(request).list(sid)


@router.get("/sessions/{sid}/preview", response_model=PreviewResponse)
def preview(
    sid: str,
    request: Request,
    targets: list[str] = Query([]),
    only: bool = False,
    force: bool = False,
    force_steps: list[str] = Query([]),
):
    """What a run with these arguments would do, step by step, without running."""
    arguments = request.app.state.sessions.preview_args(
        sid, targets=targets, only=only, force=force, force_steps=force_steps
    )
    return api.plan_preview(**arguments).to_dict()


@router.get("/runs/{rid}", response_model=RunResponse)
def get_run(rid: str, request: Request):
    """One run's record."""
    return runs(request).get(rid)


@router.get(
    "/runs/{rid}/events",
    response_class=StreamingResponse,
    responses={
        200: {
            "description": "Server sent events: ``id`` is the event's seq and "
            "``data`` the event as JSON. The stream ends after ``run_finished``.",
            "content": {"text/event-stream": {"schema": {"type": "string"}}},
        },
        204: {"description": "The run has ended and no event follows ``after``."},
    },
)
async def run_events(rid: str, request: Request, after: int = 0):
    """The run's events from ``after`` (or ``Last-Event-ID``) on, live.

    A run that has ended with nothing after ``after`` answers 204, which tells
    a browser ``EventSource`` to stop reconnecting.
    """
    manager = runs(request)
    path = manager.events_path(rid)
    after = int(request.headers.get("last-event-id") or after or 0)
    record = manager.get(rid)
    if record["status"] not in ACTIVE_STATUSES and after >= record["last_seq"]:
        return Response(status_code=204)

    async def stop() -> bool:
        if await request.is_disconnected():
            return True
        return manager.get(rid)["status"] not in ACTIVE_STATUSES

    async def stream():
        async for event in tail.follow(
            path, after=after, stop=stop, keepalive=KEEPALIVE_SECONDS
        ):
            if event is None:
                yield ": keep-alive\n\n"
            else:
                yield f"id: {event['seq']}\ndata: {json.dumps(event)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/runs/{rid}/log", response_model=LogResponse)
def run_log(
    rid: str, request: Request, step: str | None = None, stream: str | None = None
):
    """The run's captured output and tracebacks, optionally for one step or stream."""
    return runs(request).log(rid, step=step, stream=stream)


@router.post("/runs/{rid}/cancel", response_model=RunResponse)
def cancel_run(rid: str, request: Request):
    """Ask the run to stop; its process tree is killed after the grace period."""
    return runs(request).cancel(rid)


@router.post("/runs/{rid}/stop", response_model=RunResponse)
def stop_run(rid: str, request: Request):
    """Stop the run now: kill its process tree without waiting for the running step."""
    return runs(request).stop(rid)
