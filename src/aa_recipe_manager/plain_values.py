# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Turn values loaded from YAML into plain text and JSON types."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def clean_text(text: str | None) -> str | None:
    """Collapse the trailing newline a folded YAML block leaves behind."""
    if text is None:
        return None
    return str(text).strip() or None


def json_safe(value: Any) -> Any:
    """Coerce a value (including ruamel scalar subclasses) to plain JSON types."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return int(value)
    if isinstance(value, float):
        return float(value)
    if isinstance(value, str):
        return str(value)
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return str(value)
