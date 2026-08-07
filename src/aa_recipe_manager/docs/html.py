# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Render a payload into the single-file HTML reference page."""

from __future__ import annotations

import importlib.resources
import json
from typing import Any

#: Placeholder the template carries in place of the embedded payload.
PAYLOAD_TOKEN = "__AA_RECIPE_OP_PAYLOAD__"

#: Escaped inside the JSON so a description containing "</script>" cannot
#: close the data block early. JSON string escapes keep the value unchanged.
_SCRIPT_SAFE = {"<": "\\u003c", ">": "\\u003e", "&": "\\u0026"}


def render_html(payload: dict[str, Any]) -> str:
    """Render the payload into a complete, self-contained HTML document.

    Args:
        payload: Document model from :func:`build_payload`.

    Returns:
        The HTML text, with all CSS, JavaScript, and data inline.
    """
    data = json.dumps(payload, ensure_ascii=False)
    for character, escape in _SCRIPT_SAFE.items():
        data = data.replace(character, escape)
    return load_template().replace(PAYLOAD_TOKEN, data)


def load_template() -> str:
    """Read the page template shipped alongside this module."""
    return (
        importlib.resources.files("aa_recipe_manager.docs")
        .joinpath("template.html")
        .read_text(encoding="utf-8")
    )
