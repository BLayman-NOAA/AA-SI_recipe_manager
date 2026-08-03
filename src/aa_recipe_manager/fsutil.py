# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Filesystem helpers shared by the executor and the ``doctor`` command."""

from __future__ import annotations

import os
import shutil
import stat
import sys
from typing import Any, Callable


def grant_access(path: str | os.PathLike) -> None:
    """Add the owner permissions needed to remove ``path``.

    Read and write are always granted; execute is added for directories only,
    so a data file is never made executable. Bits are OR-ed onto the current
    mode rather than replacing it: assigning ``stat.S_IWRITE`` outright is
    ``0o200``, which strips read and execute from a directory and leaves it
    permanently unlistable.
    """
    try:
        mode = os.stat(path).st_mode
    except OSError:
        return
    wanted = mode | stat.S_IWRITE | stat.S_IREAD
    if stat.S_ISDIR(mode):
        wanted |= stat.S_IEXEC
    if wanted == mode:
        return
    try:
        os.chmod(path, wanted)
    except OSError:
        pass


def rmtree_onerror(func: Callable[..., Any], path: str, _exc: Any) -> None:
    """``shutil.rmtree`` error handler that fixes up permissions and retries.

    Removing an entry needs write and execute on its *parent*; listing a
    directory needs read and execute on the directory *itself*. Both are
    granted before retrying, since which one is missing depends on whether
    rmtree failed while scanning or while unlinking.
    """
    grant_access(os.path.dirname(path) or ".")
    grant_access(path)
    func(path)


def rmtree(path: str | os.PathLike) -> None:
    """``shutil.rmtree`` with :func:`rmtree_onerror` installed.

    Python 3.12 deprecated ``onerror`` in favour of ``onexc``, which passes the
    exception instead of the ``sys.exc_info()`` triple; the handler ignores that
    argument either way.
    """
    if sys.version_info >= (3, 12):
        shutil.rmtree(path, onexc=rmtree_onerror)
    else:
        shutil.rmtree(path, onerror=rmtree_onerror)
