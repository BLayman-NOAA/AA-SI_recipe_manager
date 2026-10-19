# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Read and write the server's JSON state files."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

#: Attempts at the final rename before a write gives up.
REPLACE_ATTEMPTS = 20


def write_json(path: Path, data: dict[str, Any]) -> None:
    """Write a JSON file atomically: a temporary file, then a rename.

    On Windows a reader or a virus scanner holding the old file open makes
    the rename fail for a moment, so it is retried.
    """
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    for attempt in range(REPLACE_ATTEMPTS):
        try:
            os.replace(temporary, path)
            return
        except PermissionError:
            if attempt == REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(0.05)


def read_json(path: Path) -> dict[str, Any] | None:
    """A JSON object from a file, or None when it is missing or not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
