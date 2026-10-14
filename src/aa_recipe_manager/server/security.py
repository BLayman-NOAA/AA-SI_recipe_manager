# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Keep the server answering only the local browser client.

The server binds to loopback, but a page on another site can still make the
browser send requests to it. The Host check stops DNS rebinding, and the custom
header plus Origin check stops cross site form posts and scripted requests,
because a browser cannot add a custom header to a cross origin request without
a CORS preflight, and this server answers no preflight.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.websockets import WebSocketClose

from aa_recipe_manager.server.errors import error_body

if TYPE_CHECKING:
    from starlette.types import ASGIApp, Receive, Scope, Send

    from aa_recipe_manager.server.config import ServerConfig

MUTATING_METHODS = {"POST", "PUT", "DELETE", "PATCH"}
CLIENT_HEADER = "x-recipe-client"


class LocalOnlyMiddleware:
    """Reject requests that do not come from the local client.

    Every request must carry an allowed Host header. Requests under ``/api/``
    that change state must also carry ``X-Recipe-Client: 1`` and, when they
    have an Origin header, an allowed origin.
    """

    def __init__(self, app: ASGIApp, config: ServerConfig) -> None:
        self.app = app
        self.hosts = config.allowed_hosts()
        self.origins = config.allowed_origins()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        problem = self._problem(scope, headers)
        if problem is None:
            await self.app(scope, receive, send)
        elif scope["type"] == "websocket":
            await WebSocketClose(code=1008)(scope, receive, send)
        else:
            response = JSONResponse(error_body(*problem, None), status_code=403)
            await response(scope, receive, send)

    def _problem(self, scope: Scope, headers: Headers) -> tuple[str, str] | None:
        host = headers.get("host", "")
        if host not in self.hosts:
            return "forbidden_host", f"Host {host!r} is not served here."
        mutating = scope.get("method") in MUTATING_METHODS
        if not (mutating and scope["path"].startswith("/api/")):
            return None
        if headers.get(CLIENT_HEADER) != "1":
            message = "Requests that change state need the X-Recipe-Client: 1 header."
            return "missing_client_header", message
        origin = headers.get("origin")
        if origin is not None and origin not in self.origins:
            return "foreign_origin", f"Origin {origin!r} is not allowed."
        return None
