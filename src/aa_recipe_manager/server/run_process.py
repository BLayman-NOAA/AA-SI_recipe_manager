# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The process that runs one recipe for the client server.

Usage: ``python -m aa_recipe_manager.server.run_process RUN_DIR``. The server
writes ``RUN_DIR/job.json`` and starts this module with the recipe's folder as
working directory. Events go to the job's events file, and a cancel file that
appears stops the run between steps. The exit code is 0 when the run
completed, 1 when it failed and 2 when it was cancelled.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import time
import traceback
from pathlib import Path
from typing import Any

from aa_recipe_manager import api
from aa_recipe_manager.events import (
    CancelFileWatcher,
    CancelToken,
    JsonlEventSink,
    RunEvent,
)


class _CountingSink:
    """Write events to a file and remember the last one's seq and kind."""

    def __init__(self, sink: JsonlEventSink) -> None:
        self._sink = sink
        self.last_seq = 0
        self.last_kind: str | None = None

    def emit(self, event: RunEvent) -> None:
        self._sink.emit(event)
        self.last_seq = event.seq
        self.last_kind = event.kind

    def close(self) -> None:
        self._sink.close()


class _StderrHandler(logging.StreamHandler):
    """Write log records to ``sys.stderr`` as it is when each record arrives.

    A run replaces ``sys.stderr`` to capture each step's output, so a handler
    holding the stream it started with would bypass that capture.
    """

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, value: Any) -> None:
        pass


def main(argv: list[str] | None = None) -> int:
    """Run one job folder.

    Returns:
        The exit code: 0 completed, 1 failed, 2 cancelled.
    """
    args = sys.argv[1:] if argv is None else argv
    job = json.loads((Path(args[0]) / "job.json").read_text(encoding="utf-8"))
    os.environ.setdefault("MPLBACKEND", "Agg")
    os.chdir(job["cwd"])

    sink = _CountingSink(JsonlEventSink(job["events"]))
    token = CancelToken()
    watcher = CancelFileWatcher(job["cancel_file"], token, interval=1.0)
    watcher.start()
    stop_signal = signal.SIGBREAK if sys.platform == "win32" else signal.SIGTERM
    signal.signal(stop_signal, lambda *_: token.cancel("stop signal"))
    handler = _StderrHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    config = job["run_config"]
    executor = config["executor"] or "sequential"
    options = {}
    if executor == "dask":
        options = {
            key: value
            for key, value in (
                ("scheduler", config["dask_scheduler"]),
                ("n_workers", config["dask_workers"]),
            )
            if value is not None
        }
    default_cache = f"./{api.DEFAULT_OUTPUT_ROOT}"
    started = time.time()
    try:
        result = api.execute(
            job["recipe"],
            inputs=job["inputs"] or None,
            executor=executor,
            executor_options=options or None,
            user_cache_dir=config["user_cache_dir"] or default_cache,
            outputs_dir=job["outputs_dir"],
            temp_dir=config["temp_dir"],
            storage_options=config["storage_options"],
            survey_cache_dir=config["survey_cache_dir"],
            targets=job["targets"] or None,
            only=job["mode"] == "only",
            force=job["force"],
            force_steps=job["force_steps"],
            events=sink,
            cancel=token,
            log_destination="file",
        )
    except BaseException as exc:
        if sink.last_kind != "run_finished":
            sink.emit(
                RunEvent(
                    seq=sink.last_seq + 1,
                    time=time.time(),
                    kind="run_finished",
                    run_id=None,
                    data={
                        "status": "failed",
                        "seconds": time.time() - started,
                        "manifest": None,
                        "executed": [],
                        "skipped": [],
                        "pruned": [],
                        "untargeted": [],
                        "error_type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                )
            )
        traceback.print_exc()
        return 1
    finally:
        watcher.stop()
        sink.close()
    return 2 if result.status == "cancelled" else 0


if __name__ == "__main__":
    sys.exit(main())
