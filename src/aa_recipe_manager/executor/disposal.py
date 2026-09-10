# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Deleting the on-disk artifacts of ports marked ``disposable``.

Separate from the memory eviction in ``engine/runner.py``, which swaps a live
object for a lazy checkpoint ref and needs the step to be durable. This deletes
files outright and applies precisely to steps that are *not* checkpointed, so
the two never touch the same port.

The motivating case is streaming a survey that does not fit on the disk: a
mapped chain downloads one raw file, converts it to an intermediate store,
checkpoints Sv, and then wants both the download and the store gone before the
next instance starts. Peak disk then tracks the executor's concurrency instead
of the size of the survey.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from aa_recipe_manager import fsutil

logger = logging.getLogger(__name__)


def disposable_ports(spec: Any) -> list[str]:
    """Names of a spec's output ports marked ``disposable``.

    Args:
        spec: Step specification carrying an ``outputs`` mapping.

    Returns:
        list: Port names, empty when none are marked.
    """
    return [
        name
        for name, port in getattr(spec, "outputs", {}).items()
        if getattr(port, "disposable", False)
    ]


def _paths_in(value: Any) -> list[str]:
    """Local filesystem paths a port value names.

    A disposable port normally holds one path string or a list of them.
    Anything else is ignored rather than guessed at, and remote URLs are never
    returned: this deletes the run's own scratch, never a bucket object.

    Args:
        value: The port's runtime value.

    Returns:
        list: Local path strings found in *value*.
    """
    if isinstance(value, (str, os.PathLike)):
        candidates = [os.fspath(value)]
    elif isinstance(value, (list, tuple)):
        candidates = [
            os.fspath(item)
            for item in value
            if isinstance(item, (str, os.PathLike))
        ]
    else:
        return []
    return [text for text in candidates if "://" not in text]


def dispose_value(value: Any) -> int:
    """Delete the files and directories a port value names.

    Missing paths are not an error: a disposal may run twice (once per chain
    instance, once at the runner level) and the second pass finds nothing.

    Args:
        value: The port's runtime value.

    Returns:
        int: Number of paths actually removed.
    """
    removed = 0
    for text in _paths_in(value):
        path = Path(text)
        try:
            if path.is_dir():
                fsutil.rmtree(path)
            elif path.exists():
                path.unlink()
            else:
                continue
        except OSError as exc:
            # Disposal is an optimisation. Losing the race with an antivirus
            # scanner or a still-open handle costs disk, not correctness, so it
            # must never fail the run that produced the data.
            logger.warning("Could not dispose %s: %s", path, exc)
            continue
        removed += 1
        logger.debug("Disposed %s", path)
    return removed


def dispose_step_outputs(spec: Any, outputs: dict[str, Any] | None) -> int:
    """Delete every disposable port's files from one step's outputs.

    Args:
        spec: Step specification declaring the ports.
        outputs: The step's runtime outputs, or None.

    Returns:
        int: Number of paths removed across all disposable ports.
    """
    if not outputs:
        return 0
    return sum(
        dispose_value(outputs.get(name)) for name in disposable_ports(spec)
    )
