# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Start, watch and stop run processes and everything they start.

A run process is started in a process group of its own, so a Dask cluster it
starts can be stopped with it: on Linux the group is signalled, on Windows
``taskkill /T`` stops the tree and psutil sweeps up anything left.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from typing import Any

import psutil


def group_options() -> dict[str, Any]:
    """``Popen`` keyword arguments that put the child in a group of its own."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def create_time(pid: int) -> float | None:
    """When a process started, in epoch seconds, or None when it is gone."""
    try:
        return psutil.Process(pid).create_time()
    except psutil.Error:
        return None


def is_alive(pid: int | None, created: float | None) -> bool:
    """Whether ``pid`` still names the process that started at ``created``.

    A pid can be reused once its process ends, so the start time is compared
    too. Without a recorded start time any live process with the pid counts.
    """
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        if not running(process):
            return False
        return created is None or abs(process.create_time() - created) < 1
    except psutil.Error:
        return False


def kill_tree(pid: int, *, grace: float = 0.0) -> None:
    """Stop a run process and everything it started, on Linux and Windows.

    Args:
        pid: The run process, started with :func:`group_options`.
        grace: On Linux, seconds between SIGTERM and SIGKILL. Windows has no
            graceful signal here; the cancel file is the graceful path.
    """
    try:
        root = psutil.Process(pid)
        tree = [root, *root.children(recursive=True)]
    except psutil.Error:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            check=False,
        )
        for process in tree:
            try:
                process.kill()
            except psutil.Error:
                pass
    else:
        try:
            group = os.getpgid(pid)
        except ProcessLookupError:
            group = None
        if group is not None:
            _signal_group(group, signal.SIGTERM)
            if grace:
                wait_gone(tree, timeout=grace)
            _signal_group(group, signal.SIGKILL)
        for process in tree:
            try:
                process.kill()
            except psutil.Error:
                pass
    wait_gone(tree, timeout=10)


def wait_gone(
    processes: list[psutil.Process], *, timeout: float
) -> list[psutil.Process]:
    """Wait until the processes end and return those still running.

    Unlike ``psutil.wait_procs`` this never reaps a child of this process, so
    the ``Popen`` that started it still gets its exit code.
    """
    deadline = time.monotonic() + timeout
    pending = [process for process in processes if running(process)]
    while pending and time.monotonic() < deadline:
        time.sleep(0.05)
        pending = [process for process in pending if running(process)]
    return pending


def running(process: psutil.Process) -> bool:
    """Whether a process is still running; a zombie is not."""
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def _signal_group(group: int, signum: int) -> None:
    try:
        os.killpg(group, signum)
    except (ProcessLookupError, PermissionError):
        pass
