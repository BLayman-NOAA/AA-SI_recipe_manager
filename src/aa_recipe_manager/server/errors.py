# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Map exceptions to HTTP responses with one JSON error body."""

from __future__ import annotations

import re
import traceback
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    ImplementationNotFoundError,
    RecipeParseError,
    RecipeValidationError,
    SpecNotFoundError,
)

if TYPE_CHECKING:
    from fastapi import FastAPI, Request


class SessionNotFound(LookupError):
    """No session has the requested id."""


class RecipeNotFound(LookupError):
    """The recipe file to open does not exist."""


class PathNotAllowed(Exception):
    """A path lies outside the folders the server may read."""


_STEP = re.compile(r"^Step '([^']+)'")
NOT_FOUND = (SessionNotFound, RecipeNotFound, FileNotFoundError)
#: Recipe problems found while reading it, which the client can show.
UNPROCESSABLE = (
    RecipeParseError,
    SpecNotFoundError,
    ImplementationNotFoundError,
    AmbiguousImplementationError,
)


def translate_error(exc: Exception) -> tuple[int, dict[str, Any]]:
    """Return the status code and body the client sees for an exception.

    The body always has ``error`` (a short code, usually the exception class
    name), ``message``, ``details`` and ``step_id``.
    """
    if isinstance(exc, HTTPException):
        code = HTTPStatus(exc.status_code).name.lower()
        return exc.status_code, error_body(code, str(exc.detail), None)
    if isinstance(exc, RequestValidationError):
        message = "The request body or parameters are invalid."
        details = jsonable_encoder(exc.errors())
        return 422, error_body("invalid_request", message, details)
    details: Any = None
    if isinstance(exc, UNPROCESSABLE):
        status = 422
    elif isinstance(exc, RecipeValidationError):
        status = 422
        details = exc.errors
    elif isinstance(exc, NOT_FOUND):
        status = 404
    elif isinstance(exc, PathNotAllowed):
        status = 403
    elif isinstance(exc, ValueError):
        status = 400
    else:
        status = 500
        details = "".join(traceback.format_exception(exc))
    body = error_body(type(exc).__name__, str(exc), details)
    step = _STEP.match(body["message"])
    body["step_id"] = getattr(exc, "step_id", None) or (step and step[1])
    return status, body


def error_body(error: str, message: str, details: Any) -> dict[str, Any]:
    """The JSON body of every error response."""
    return {"error": error, "message": message, "details": details, "step_id": None}


async def _handle(request: Request, exc: Exception) -> JSONResponse:
    status, body = translate_error(exc)
    return JSONResponse(body, status_code=status)


def install_handlers(app: FastAPI) -> None:
    """Send every error through :func:`translate_error`.

    The expected errors are registered by class so they are answered without
    being logged as server errors; anything else falls to the ``Exception``
    handler, which Starlette also logs.
    """
    for kind in (
        HTTPException,
        RequestValidationError,
        *UNPROCESSABLE,
        RecipeValidationError,
        *NOT_FOUND,
        PathNotAllowed,
        ValueError,
        Exception,
    ):
        app.add_exception_handler(kind, _handle)
