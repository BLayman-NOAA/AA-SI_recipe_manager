# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Runs: one recipe run per process, recorded under the session's folder.

Each run has a folder ``<state_dir>/sessions/<sid>/runs/<rid>/`` holding
``job.json`` (what the run process reads), ``run.json`` (the server's record),
``events.jsonl`` (written by the run process), ``cancel`` (touched to cancel),
``run.log`` (the process's own output), ``outputs/`` (the outputs folder when
the run config sets none) and ``manifest.json`` (copied when the run ends).

A watcher thread per active run follows its events file and updates
``run.json``. Run processes outlive the server: on start the server picks up
the runs still going, and gives the others the outcome their events file
reports, or interrupted when it reports none.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any

import psutil

from aa_recipe_manager import api
from aa_recipe_manager.server import procs, tail
from aa_recipe_manager.server.errors import (
    ActiveRunExists,
    InvalidRecipe,
    RunNotActive,
    RunNotFound,
)
from aa_recipe_manager.server.files import read_json, write_json
from aa_recipe_manager.server.sessions import RUNS_FOLDER

if TYPE_CHECKING:
    from aa_recipe_manager.server.config import ServerConfig
    from aa_recipe_manager.server.sessions import SessionStore

RUN_SCHEMA = 1
JOB_SCHEMA = 1
ACTIVE_STATUSES = frozenset({"queued", "starting", "running"})
EXIT_STATUS = {0: "completed", 1: "failed", 2: "cancelled"}
LOG_LIMIT = 20000
POLL_SECONDS = 0.25
#: The ``run_finished`` list each step disposition goes in; cache hits are
#: ``skipped`` too.
_STEP_GROUPS = {
    "computed": "executed",
    "marker": "skipped",
    "skipped": "skipped",
    "pruned": "pruned",
}
_RUN_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")

logger = logging.getLogger(__name__)


@dataclass
class RunRecord:
    """The server's record of one run, kept in ``run.json``."""

    id: str
    session: str
    created: str
    mode: str
    targets: list[str]
    force: bool
    force_steps: list[str]
    executor: str | None
    outputs_dir: str | None
    log: str
    cwd: str | None = None
    schema: int = RUN_SCHEMA
    started: str | None = None
    finished: str | None = None
    status: str = "queued"
    pid: int | None = None
    pid_created: float | None = None
    exit_code: int | None = None
    cancel_requested_at: str | None = None
    stop_requested_at: str | None = None
    killed: bool = False
    summary: dict[str, Any] | None = None
    manifest: str | None = None
    last_seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunRecord:
        return cls(**data)


@dataclass
class _Progress:
    """What has been read from a run's events file so far.

    ``steps`` sorts the finished steps as core's ``run_finished`` does, so a
    run that ends without one still gets an honest summary.
    """

    offset: int = 0
    last_seq: int = 0
    finished: dict[str, Any] | None = None
    failed_step: str | None = None
    running: list[str] = field(default_factory=list)
    steps: dict[str, list[str]] = field(
        default_factory=lambda: {"executed": [], "skipped": [], "pruned": []}
    )

    def read(self, event: dict[str, Any]) -> None:
        """Take note of one event."""
        self.last_seq = max(self.last_seq, int(event.get("seq", 0)))
        kind, data = event.get("kind"), event.get("data") or {}
        step = data.get("step")
        if kind == "step_started" and step not in self.running:
            self.running.append(step)
        elif kind in ("step_finished", "step_failed") and step in self.running:
            self.running.remove(step)
        if kind == "step_finished":
            disposition = str(data.get("disposition"))
            group = _STEP_GROUPS.get(disposition)
            if group is None and disposition.startswith("hit-"):
                group = "skipped"
            if group is not None:
                self.steps[group].append(data.get("step"))
        elif kind == "step_failed":
            self.failed_step = data.get("step")
        elif kind == "run_finished":
            self.finished = event


@dataclass
class _Watch:
    thread: threading.Thread
    timer: threading.Timer | None = None
    progress: _Progress = field(default_factory=_Progress)


class RunManager:
    """Start, follow, cancel and stop the runs of every session.

    The records of runs this server knows about are kept in memory behind one
    lock; ``run.json`` is rewritten, atomically, on every change of state.
    """

    def __init__(self, sessions: SessionStore, config: ServerConfig) -> None:
        self.sessions = sessions
        self.config = config
        self._records: dict[str, RunRecord] = {}
        self._watches: dict[str, _Watch] = {}
        self._lock = threading.Lock()
        self._stopping = threading.Event()

    def start(
        self,
        sid: str,
        *,
        mode: str,
        targets: list[str],
        force: bool,
        force_steps: list[str],
    ) -> dict[str, Any]:
        """Start a run of a session in a new process.

        Raises:
            SessionNotFound: No such session.
            ActiveRunExists: The session has a run that has not finished.
            ValueError: The targets or forced steps are not step ids, or a
                mode that needs targets has none.
            InvalidRecipe: The recipe has validation errors.
            UpstreamNotCachedError: ``only`` mode, and a parent of a target is
                not cached.
        """
        job = self.sessions.run_job(sid)
        self._refuse_second_run(sid, self.active_run(sid))
        if mode != "all" and not targets:
            raise ValueError(f"Mode {mode!r} needs at least one target.")
        if mode == "all" and targets:
            raise ValueError("Mode 'all' runs every step; use mode 'to' for targets.")
        unknown = sorted((set(targets) | set(force_steps)) - set(job["step_ids"]))
        if unknown:
            raise ValueError(
                f"No step named {', '.join(unknown)}. "
                f"Steps: {', '.join(job['step_ids'])}."
            )
        if not job["validation"]["valid"]:
            problems = job["validation"]["problems"]
            raise InvalidRecipe("The recipe has errors; fix them to run it.", problems)
        if mode == "only":
            arguments = self.sessions.preview_args(
                sid, targets=targets, only=True, force=force, force_steps=force_steps
            )
            api.plan_preview(**arguments)

        runs_dir = self.sessions.runs_dir(sid)
        with self._lock:
            self._refuse_second_run(sid, self._active_in(sid))
            rid = self._new_id(runs_dir)
            run_dir = runs_dir / rid
            run_dir.mkdir(parents=True)
            config = job["run_config"]
            outputs_dir = config["outputs_dir"] or (run_dir / "outputs").as_posix()
            write_json(
                run_dir / "job.json",
                {
                    "schema": JOB_SCHEMA,
                    "run_id": rid,
                    "session_id": sid,
                    "recipe": job["origin"],
                    "cwd": job["cwd"],
                    "inputs": job["inputs"],
                    "run_config": config,
                    "mode": mode,
                    "targets": list(targets),
                    "force": force,
                    "force_steps": list(force_steps),
                    "outputs_dir": outputs_dir,
                    "events": (run_dir / "events.jsonl").as_posix(),
                    "cancel_file": (run_dir / "cancel").as_posix(),
                    "files": job["files"],
                    "created": _now(),
                },
            )
            record = RunRecord(
                id=rid,
                session=sid,
                created=_now(),
                mode=mode,
                targets=list(targets),
                force=force,
                force_steps=list(force_steps),
                executor=config["executor"],
                outputs_dir=outputs_dir,
                log=(run_dir / "run.log").as_posix(),
                cwd=job["cwd"],
            )
            self._records[rid] = record
            self._save(record)

        try:
            process = self._spawn(run_dir, job["cwd"])
        except Exception as exc:
            self._end(rid, "failed", None, f"The run process did not start: {exc}")
            raise
        with self._lock:
            record.pid = process.pid
            record.pid_created = procs.create_time(process.pid)
            record.started = _now()
            record.status = "starting"
            self._save(record)
            self._watch(rid, partial(_exit_code, process))
            return record.to_dict()

    def get(self, rid: str) -> dict[str, Any]:
        """A run's record.

        Raises:
            RunNotFound: No such run.
        """
        with self._lock:
            return self._record(rid).to_dict()

    def list(self, sid: str) -> list[dict[str, Any]]:
        """A session's runs, newest first.

        Raises:
            SessionNotFound: No such session.
        """
        runs_dir = self.sessions.runs_dir(sid)
        found = []
        folders = runs_dir.iterdir() if runs_dir.is_dir() else []
        with self._lock:
            for folder in folders:
                if folder.name in self._records:
                    found.append(self._records[folder.name].to_dict())
                    continue
                record = read_json(folder / "run.json")
                if record is not None:
                    found.append(record)
        return sorted(found, key=lambda run: (run["created"], run["id"]), reverse=True)

    def cancel(self, rid: str) -> dict[str, Any]:
        """Ask a run to stop after the steps already running.

        Touches the run's cancel file and kills its process tree if it has
        not stopped after ``cancel_grace_seconds``.

        Raises:
            RunNotFound: No such run.
            RunNotActive: The run has already finished.
        """
        with self._lock:
            record = self._record(rid)
            if record.status not in ACTIVE_STATUSES:
                raise RunNotActive(f"Run {rid} has already {record.status}.")
            (self._run_dir(record) / "cancel").touch()
            if record.cancel_requested_at is None:
                record.cancel_requested_at = _now()
                self._save(record)
                self._arm_grace(rid, self.config.cancel_grace_seconds)
            return record.to_dict()

    def stop(self, rid: str) -> dict[str, Any]:
        """Stop a run now, without waiting for the steps that are running.

        The cancel file is touched too, so a run process not yet started
        still stops before its first step.

        Raises:
            RunNotFound: No such run.
            RunNotActive: The run has already finished.
        """
        with self._lock:
            record = self._record(rid)
            if record.status not in ACTIVE_STATUSES:
                raise RunNotActive(f"Run {rid} has already {record.status}.")
            (self._run_dir(record) / "cancel").touch()
            if record.stop_requested_at is None:
                record.stop_requested_at = _now()
                self._save(record)
        self.kill(rid)
        with self._lock:
            return self._record(rid).to_dict()

    def kill(self, rid: str) -> None:
        """Stop a run's whole process tree now. A finished run is left alone.

        Raises:
            RunNotFound: No such run.
        """
        with self._lock:
            record = self._record(rid)
            if record.status not in ACTIVE_STATUSES:
                return
            record.killed = True
            self._save(record)
            pid, created = record.pid, record.pid_created
        if procs.is_alive(pid, created):
            procs.kill_tree(pid)

    def log(self, rid: str, *, step: str | None, stream: str | None) -> dict[str, Any]:
        """A run's captured output, with one ``traceback`` entry per failure.

        Raises:
            RunNotFound: No such run.
        """
        events = tail.read_events(self.events_path(rid))
        entries = tail.log_entries(events, step=step, stream=stream)
        return {
            "run_id": rid,
            "step": step,
            "stream": stream,
            "entries": entries[-LOG_LIMIT:],
            "truncated": len(entries) > LOG_LIMIT,
        }

    def forget_session(self, sid: str) -> None:
        """Drop the records of a deleted session's runs.

        Its runs have all finished, since a session with an active run cannot
        be deleted, so only finished watches and timers can be left.
        """
        with self._lock:
            for rid in [r.id for r in self._records.values() if r.session == sid]:
                del self._records[rid]
                watch = self._watches.pop(rid, None)
                if watch is not None and watch.timer is not None:
                    watch.timer.cancel()

    def events_path(self, rid: str) -> Path:
        """The run's events file, which may not exist yet.

        Raises:
            RunNotFound: No such run.
        """
        with self._lock:
            return self._run_dir(self._record(rid)) / "events.jsonl"

    def active_run(self, sid: str) -> str | None:
        """The id of the session's run that has not finished, or None."""
        with self._lock:
            return self._active_in(sid)

    def recover(self) -> None:
        """Pick up the runs a previous server left active.

        A run whose process is still alive is followed again. One whose
        process is gone is read to the end of its events file at once: it
        ends as its ``run_finished`` says, or interrupted when there is none.
        """
        root = self.sessions.root
        paths = root.glob(f"*/{RUNS_FOLDER}/*/run.json") if root.is_dir() else []
        gone = []
        for path in paths:
            data = read_json(path)
            if data is None or data.get("status") not in ACTIVE_STATUSES:
                continue
            record = RunRecord.from_dict(data)
            exited = _gone
            if procs.is_alive(record.pid, record.pid_created):
                try:
                    exited = partial(_ended, psutil.Process(record.pid))
                except psutil.Error:
                    pass
            with self._lock:
                if record.id in self._records:
                    continue
                self._records[record.id] = record
                self._watch(record.id, exited)
                if exited is _gone:
                    gone.append(self._watches[record.id].thread)
                elif record.cancel_requested_at is not None:
                    asked = datetime.fromisoformat(record.cancel_requested_at)
                    waited = (datetime.now(timezone.utc) - asked).total_seconds()
                    self._arm_grace(
                        record.id, max(0.0, self.config.cancel_grace_seconds - waited)
                    )
        for thread in gone:
            thread.join()

    def shutdown(self) -> None:
        """Stop the watcher threads and grace timers; the runs keep going."""
        self._stopping.set()
        with self._lock:
            watches = list(self._watches.values())
        for watch in watches:
            if watch.timer is not None:
                watch.timer.cancel()
            watch.thread.join(timeout=5)

    def _spawn(self, run_dir: Path, cwd: str) -> subprocess.Popen:
        command = [
            sys.executable,
            "-m",
            "aa_recipe_manager.server.run_process",
            str(run_dir),
        ]
        env = {key: value for key, value in os.environ.items()}
        env.pop("AA_RECIPE_SERVER_CONFIG", None)
        env["PYTHONUNBUFFERED"] = "1"
        with open(run_dir / "run.log", "ab") as log:
            return subprocess.Popen(
                command,
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                env=env,
                **procs.group_options(),
            )

    def _watch(self, rid: str, exited: Callable[[], tuple[bool, int | None]]) -> None:
        """Start following a run; call with the lock held."""
        thread = threading.Thread(
            target=self._follow,
            args=(rid, exited),
            name=f"aa-recipe-run-{rid}",
            daemon=True,
        )
        self._watches[rid] = _Watch(thread=thread)
        thread.start()

    def _follow(self, rid: str, exited: Callable[[], tuple[bool, int | None]]) -> None:
        """Update a run's record from its events until its process ends."""
        with self._lock:
            path = self._run_dir(self._records[rid]) / "events.jsonl"
            progress = self._watches[rid].progress
        try:
            while not self._stopping.is_set():
                done, code = exited()
                events, progress.offset = tail.read_chunk(path, progress.offset)
                if events:
                    self._absorb(rid, events, progress)
                if done:
                    self._finish(rid, code, progress)
                    return
                self._stopping.wait(POLL_SECONDS)
        except Exception as exc:
            logger.exception("Following run %s failed", rid)
            self._abandon(rid, f"The server stopped following the run: {exc}")

    def _abandon(self, rid: str, message: str) -> None:
        """Stop a run the server can no longer follow and mark it interrupted.

        Its process tree is killed first, so no unwatched run keeps writing to
        the session's cache. The session must not stay blocked either, so when
        even recording the end fails the status is changed in memory.
        """
        with self._lock:
            record = self._records[rid]
            pid, created = record.pid, record.pid_created
        try:
            if procs.is_alive(pid, created):
                procs.kill_tree(pid)
                message += " Its process was stopped."
            self._end(rid, "interrupted", None, message)
        except Exception:
            logger.exception("Recording the end of run %s failed", rid)
            with self._lock:
                self._records[rid].status = "interrupted"
                self._watches.pop(rid, None)

    def _absorb(
        self, rid: str, events: list[dict[str, Any]], progress: _Progress
    ) -> None:
        with self._lock:
            record = self._records[rid]
            for event in events:
                progress.read(event)
                if event.get("kind") == "run_started" and record.status == "starting":
                    record.status = "running"
                    self._save(record)
            record.last_seq = max(record.last_seq, progress.last_seq)

    def _finish(self, rid: str, code: int | None, progress: _Progress) -> None:
        """Record how a run ended, once its process is gone.

        The status is decided and written under one hold of the lock, so a
        grace timer kill cannot change the record in between.
        """
        finished = progress.finished
        with self._lock:
            record = self._records[rid]
            if record.killed:
                status = "cancelled"
            elif code is not None and code in EXIT_STATUS:
                status = EXIT_STATUS[code]
            elif finished is not None:
                status = finished["data"].get("status", "failed")
            elif record.cancel_requested_at is not None:
                status = "cancelled"
            else:
                status = "failed" if code is not None else "interrupted"
            if finished is None:
                if record.killed and record.stop_requested_at is not None:
                    running = ", ".join(progress.running)
                    message = "The run was stopped immediately" + (
                        f" while {running} was running." if running else "."
                    )
                elif record.killed:
                    message = "The run was stopped by the server."
                elif code is not None:
                    message = (
                        f"The run process stopped unexpectedly (exit code {code}); "
                        "it may have been killed outside the client."
                    )
                else:
                    message = (
                        "The run process ended while the server was not "
                        "watching, without reporting how the run went."
                    )
                self._append_end(record, status, code, message, progress)
                return
            data = finished["data"]
            record.summary = {
                "executed": len(data.get("executed") or []),
                "skipped": len(data.get("skipped") or []),
                "pruned": len(data.get("pruned") or []),
                "untargeted": len(data.get("untargeted") or []),
                "failed_step": progress.failed_step,
                "error_type": data.get("error_type"),
                "message": data.get("message"),
                "seconds": data.get("seconds"),
            }
            record.manifest = self._copy_manifest(record, data.get("manifest"))
            record.status = status
            record.exit_code = code
            record.finished = _now()
            self._close(record)

    def _end(self, rid: str, status: str, code: int | None, message: str) -> None:
        """End a run that wrote no ``run_finished``, appending one for it.

        The events file is read again, outside the lock, since a watcher may
        not have read all of it.
        """
        with self._lock:
            path = self._run_dir(self._records[rid]) / "events.jsonl"
        progress = _Progress()
        for event in tail.read_events(path):
            progress.read(event)
        with self._lock:
            record = self._records[rid]
            record.last_seq = max(record.last_seq, progress.last_seq)
            self._append_end(record, status, code, message, progress)

    def _append_end(
        self,
        record: RunRecord,
        status: str,
        code: int | None,
        message: str,
        progress: _Progress,
    ) -> None:
        """Append a synthetic ``run_finished`` and close the run; lock held.

        ``record.last_seq`` must already be the last seq in the events file,
        and ``progress`` must have read all of it.
        """
        steps = progress.steps
        path = self._run_dir(record) / "events.jsonl"
        last = record.last_seq
        seconds = None
        if record.started is not None:
            started = datetime.fromisoformat(record.started)
            seconds = (datetime.now(timezone.utc) - started).total_seconds()
        event = {
            "seq": last + 1,
            "time": time.time(),
            "kind": "run_finished",
            "run_id": None,
            "data": {
                "status": status,
                "seconds": seconds,
                "manifest": None,
                "executed": list(steps["executed"]),
                "skipped": list(steps["skipped"]),
                "pruned": list(steps["pruned"]),
                "untargeted": [],
                "error_type": None,
                "message": message,
                "synthetic": True,
            },
        }
        with open(path, "a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(event) + "\n")
        record.last_seq = last + 1
        record.status = status
        record.exit_code = code
        record.finished = _now()
        record.summary = {
            "executed": len(steps["executed"]),
            "skipped": len(steps["skipped"]),
            "pruned": len(steps["pruned"]),
            "untargeted": 0,
            "failed_step": progress.failed_step,
            "error_type": None,
            "message": message,
            "seconds": seconds,
        }
        self._close(record)

    def _close(self, record: RunRecord) -> None:
        """Save a finished run and drop its timer; call with the lock held."""
        self._save(record)
        watch = self._watches.pop(record.id, None)
        if watch is not None and watch.timer is not None:
            watch.timer.cancel()

    def _copy_manifest(self, record: RunRecord, manifest: str | None) -> str | None:
        """Copy the run's manifest into its folder, so later runs keep it."""
        if not manifest or "://" in manifest:
            return None
        source = Path(manifest)
        if not source.is_absolute():
            if record.cwd is None:
                return None
            source = Path(record.cwd) / source
        if not source.is_file():
            return None
        target = self._run_dir(record) / "manifest.json"
        try:
            shutil.copyfile(source, target)
        except OSError:
            logger.warning(
                "Could not copy the manifest of run %s", record.id, exc_info=True
            )
            return None
        return target.as_posix()

    def _arm_grace(self, rid: str, seconds: float) -> None:
        """Kill the run if it is still going after ``seconds``; lock held."""
        watch = self._watches.get(rid)
        if watch is None or watch.timer is not None:
            return
        timer = threading.Timer(seconds, self.kill, args=(rid,))
        timer.daemon = True
        watch.timer = timer
        timer.start()

    @staticmethod
    def _refuse_second_run(sid: str, running: str | None) -> None:
        if running is not None:
            raise ActiveRunExists(f"Run {running} of this session has not finished.")

    def _active_in(self, sid: str) -> str | None:
        for record in self._records.values():
            if record.session == sid and record.status in ACTIVE_STATUSES:
                return record.id
        return None

    def _record(self, rid: str) -> RunRecord:
        """The record of a run, read from disk on first use; lock held."""
        if rid in self._records:
            return self._records[rid]
        if _RUN_ID.match(rid):
            root = self.sessions.root
            for path in root.glob(f"*/{RUNS_FOLDER}/{rid}/run.json"):
                data = read_json(path)
                if data is not None:
                    record = RunRecord.from_dict(data)
                    self._records[rid] = record
                    return record
        raise RunNotFound(f"No run {rid!r}.")

    def _run_dir(self, record: RunRecord) -> Path:
        return self.sessions.root / record.session / RUNS_FOLDER / record.id

    def _save(self, record: RunRecord) -> None:
        write_json(self._run_dir(record) / "run.json", record.to_dict())

    def _new_id(self, runs_dir: Path) -> str:
        while True:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            rid = f"{stamp}-{secrets.token_hex(3)}"
            if rid not in self._records and not (runs_dir / rid).exists():
                return rid


def _exit_code(process: subprocess.Popen) -> tuple[bool, int | None]:
    """Whether a run process this server started has ended, and its code."""
    code = process.poll()
    return code is not None, code


def _ended(process: psutil.Process) -> tuple[bool, None]:
    """Whether a run process an earlier server started has ended."""
    return not procs.running(process), None


def _gone() -> tuple[bool, None]:
    """A run process that had already ended when the server started."""
    return True, None


def _now() -> str:
    """The time now, as ISO 8601 UTC with milliseconds."""
    now = datetime.now(timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
