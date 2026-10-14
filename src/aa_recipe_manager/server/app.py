# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The FastAPI application behind ``aa-recipe serve``."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from aa_recipe_manager import __version__
from aa_recipe_manager.server.config import ServerConfig
from aa_recipe_manager.server.errors import install_handlers
from aa_recipe_manager.server.models import HealthResponse
from aa_recipe_manager.server.routers import ops, recipes, sessions
from aa_recipe_manager.server.security import LocalOnlyMiddleware
from aa_recipe_manager.server.sessions import SessionStore

#: Where the web build puts the client. Absent until the client is built.
STATIC_DIR = Path(__file__).parent / "static"


def create_app(config: ServerConfig | None = None) -> FastAPI:
    """Build the client server.

    Args:
        config: Server settings. When omitted they are read from the
            environment ``aa-recipe serve`` sets, which is how uvicorn's
            reloader builds the app.

    Returns:
        The application, serving the API under ``/api`` and the built client
        at ``/``.
    """
    config = config or ServerConfig.from_env()
    app = FastAPI(
        title="AA-SI recipe manager client",
        version=__version__,
        openapi_url="/api/openapi.json",
        docs_url="/api/docs",
        redoc_url=None,
    )
    app.state.config = config
    app.state.sessions = SessionStore(config)
    app.state.ops = None
    app.state.recipe_cache = {}
    app.add_middleware(LocalOnlyMiddleware, config=config)
    install_handlers(app)

    @app.get("/api/health", response_model=HealthResponse, tags=["Health"])
    def health():
        """The server is up; its version, state folder and docs site."""
        return {
            "status": "ok",
            "version": __version__,
            "dev": config.dev,
            "state_dir": Path(config.state_dir).as_posix(),
            "docs_url": config.docs_url,
        }

    for module in (recipes, ops, sessions):
        app.include_router(module.router, prefix="/api")

    if (STATIC_DIR / "index.html").is_file():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="client")
    else:

        @app.get("/", include_in_schema=False)
        def client_not_built():
            return {
                "message": "The client is not built. Run the web build or use "
                "the Vite dev server.",
                "docs": "/api/docs",
            }

    return app
