# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Generate a browsable HTML reference for the op registry."""

from aa_recipe_manager.docs.html import render_html
from aa_recipe_manager.docs.payload import build_payload

__all__ = ["build_payload", "render_html"]
