# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Recipe files under the recipe roots."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Request

from aa_recipe_manager.server.models import RecipesResponse
from aa_recipe_manager.server.recipes import scan_recipes
from aa_recipe_manager.server.routers import ERROR_RESPONSES

router = APIRouter(prefix="/recipes", tags=["Recipes"], responses=ERROR_RESPONSES)


@router.get("", response_model=RecipesResponse)
def list_recipes(request: Request):
    """Every recipe under the recipe roots, with the files that include it.

    A YAML file that does not parse is listed with its ``error``.
    """
    roots = [Path(root).resolve() for root in request.app.state.config.recipe_roots]
    return {
        "roots": [root.as_posix() for root in roots],
        "recipes": [
            asdict(entry)
            for entry in scan_recipes(roots, request.app.state.recipe_cache)
        ],
    }
