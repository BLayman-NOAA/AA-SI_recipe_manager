# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Run events: a structured, ordered record of what a run is doing.

A run reports itself as a stream of :class:`RunEvent` objects sent to an
:class:`EventSink`. The kinds are listed in :data:`EVENT_KINDS`:

* ``run_started`` and ``plan`` open the stream, in that order, followed by
  any ``warning`` about targets that will not be saved.
* In a run that completes, every ``step_started`` is followed by one
  ``step_finished`` or ``step_failed`` for that step. A step's live ``log``
  and ``instance_finished`` events fall between them. In a failed or
  cancelled run, a step may still be open when ``run_finished`` arrives, and
  ``run_finished`` closes it: when one instance of a mapped chain fails, only
  the failing member gets ``step_failed``, which lists the chain's members in
  ``chain``, and a cancel leaves a partly run chain without an end.
* ``log`` carries captured output tagged with its step. Output from a process
  worker cannot stream; it arrives in one ``buffered`` event just before the
  step's ``step_finished`` or ``step_failed``.
* ``warning`` carries a message the user should see.
* ``run_finished`` closes the stream. It is emitted once ``execute`` was
  entered, also when the run fails or is cancelled. An argument error raised
  before the run starts produces ``run_finished`` alone.

``seq`` starts at 1 and strictly increases within a run. It is assigned under
a lock as the event is sent, so the order a sink receives events (and the line
order of a JSONL file) is ``seq`` order. Events of different steps interleave
under a concurrent backend. Untargeted steps are never announced: they appear
only in ``plan`` and ``run_finished``.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TextIO, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from aa_recipe_manager.executor.base import ProgressCallback

EVENT_KINDS = (
    "run_started",
    "plan",
    "step_started",
    "instance_finished",
    "step_finished",
    "step_failed",
    "log",
    "warning",
    "run_finished",
)

#: Longest unterminated log text held back waiting for a newline.
_MAX_PARTIAL_LINE = 4096


@dataclass
class RunEvent:
    """One event of a run.

    Attributes:
        seq: Position in the run's stream, from 1.
        time: Wall clock time of the event, in epoch seconds.
        kind: One of :data:`EVENT_KINDS`.
        run_id: The run's id, or None before it is known.
        data: The kind's fields.
    """

    seq: int
    time: float
    kind: str
    run_id: str | None
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return the event as a JSON ready dict."""
        return {
            "seq": self.seq,
            "time": self.time,
            "kind": self.kind,
            "run_id": self.run_id,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunEvent:
        """Rebuild an event from :meth:`to_dict` output."""
        return cls(
            seq=int(data["seq"]),
            time=float(data["time"]),
            kind=str(data["kind"]),
            run_id=data.get("run_id"),
            data=dict(data.get("data") or {}),
        )


@runtime_checkable
class EventSink(Protocol):
    """Anything that receives run events."""

    def emit(self, event: RunEvent) -> None: ...


class NullEventSink:
    """A sink that drops every event."""

    def emit(self, event: RunEvent) -> None:
        return None


class CallbackEventSink:
    """Send each event to a function."""

    def __init__(self, fn: Callable[[RunEvent], None]) -> None:
        self._fn = fn

    def emit(self, event: RunEvent) -> None:
        self._fn(event)


class MemoryEventSink:
    """Keep every event in a list, for tests and in process use."""

    def __init__(self) -> None:
        self.events: list[RunEvent] = []

    def emit(self, event: RunEvent) -> None:
        self.events.append(event)

    def of(self, kind: str) -> list[RunEvent]:
        """Return the events of one kind, in order."""
        return [event for event in self.events if event.kind == kind]


class JsonlEventSink:
    """Write one JSON object per line, flushed per event.

    A path is opened in append mode, so an existing file keeps its earlier
    lines. A file object given by the caller is written to but not closed.
    """

    def __init__(self, target: str | Path | TextIO) -> None:
        if isinstance(target, (str, Path)):
            self._stream: TextIO = open(
                target, "a", encoding="utf-8", newline="\n"
            )
            self._owned = True
        else:
            self._stream = target
            self._owned = False

    def emit(self, event: RunEvent) -> None:
        line = json.dumps(event.to_dict(), default=str, ensure_ascii=False)
        self._stream.write(line + "\n")
        self._stream.flush()

    def close(self) -> None:
        """Close the file when this sink opened it."""
        if self._owned and not self._stream.closed:
            self._stream.close()

    def __enter__(self) -> JsonlEventSink:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


class ProgressEventSink:
    """Drive a :class:`ProgressCallback` from events.

    ``step_started`` calls ``on_step_start``; ``step_finished`` and
    ``step_failed`` call ``on_step_end``. Other kinds are ignored. A failure
    is passed on as a :class:`PipelineExecutionError` rebuilt from the event.
    """

    def __init__(self, progress: ProgressCallback) -> None:
        self._progress = progress

    def emit(self, event: RunEvent) -> None:
        data = event.data
        if event.kind == "step_started":
            self._progress.on_step_start(data["step"], data["index"], data["total"])
        elif event.kind == "step_finished":
            self._progress.on_step_end(
                data["step"],
                data["index"],
                data["total"],
                skipped=data["disposition"] != "computed",
                elapsed=data["seconds"],
                instance_seconds=tuple(data.get("instance_seconds") or ()),
            )
        elif event.kind == "step_failed":
            from aa_recipe_manager.exceptions import PipelineExecutionError

            self._progress.on_step_end(
                data["step"],
                data["index"],
                data["total"],
                elapsed=data["seconds"],
                error=PipelineExecutionError(data["step"], data["message"]),
            )


class EventEmitter:
    """Number events and send them to a sink.

    The emitter is what a run holds; the sink is what a caller passes. A sink
    that raises is reported once in :attr:`failed` and then receives nothing
    more: a broken event file must not fail the run.

    Attributes:
        run_id: The run's id, stamped on every event once known.
        failed: ``repr`` of the sink's exception, or None.
    """

    def __init__(self, sink: EventSink | None = None, *, run_id: str | None = None):
        self._sink = sink if sink is not None else NullEventSink()
        self.run_id = run_id
        self.failed: str | None = None
        self._seq = 0
        # Reentrant: a sink that prints from inside a step is tapped and
        # emits again on the same thread.
        self._lock = threading.RLock()

    @property
    def seq(self) -> int:
        """The seq of the last event emitted, 0 before any."""
        return self._seq

    def emit(self, kind: str, **data: Any) -> RunEvent | None:
        """Send one event, returning it, or None once the sink has failed."""
        if kind not in EVENT_KINDS:
            raise ValueError(f"unknown event kind {kind!r}")
        with self._lock:
            if self.failed is not None:
                return None
            self._seq += 1
            event = RunEvent(
                seq=self._seq,
                time=time.time(),
                kind=kind,
                run_id=self.run_id,
                data=data,
            )
            try:
                self._sink.emit(event)
            except Exception as exc:
                self.failed = repr(exc)
                return None
        return event


class CancelToken:
    """A cooperative stop request checked between units and instances.

    Attributes:
        reason: Why the run was cancelled, once it was.
    """

    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason: str | None = None

    def cancel(self, reason: str | None = None) -> None:
        """Ask the run to stop. The first reason given is kept."""
        if self.reason is None:
            self.reason = reason
        self._event.set()

    def is_set(self) -> bool:
        """Whether a stop was asked for."""
        return self._event.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until a stop is asked for or ``timeout`` passes."""
        return self._event.wait(timeout)


class CancelFileWatcher:
    """Set a token when a file appears.

    Polls in a daemon thread. Used as a context manager it starts on enter
    and stops on exit. A file is the portable signal: Windows has no SIGTERM
    a process can catch for this.
    """

    def __init__(
        self, path: str | Path, token: CancelToken, *, interval: float = 1.0
    ) -> None:
        self._path = Path(path)
        self._token = token
        self._interval = interval
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Check the file once now, then keep polling in a thread."""
        if self._thread is not None:
            return
        if self._check():
            return
        self._thread = threading.Thread(
            target=self._poll, name="aa-recipe-cancel-watcher", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop polling and wait for the thread to end."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None

    def _check(self) -> bool:
        if self._path.exists():
            self._token.cancel(f"cancel file {self._path} appeared")
            return True
        return False

    def _poll(self) -> None:
        while not self._stop.wait(self._interval):
            if self._check():
                return

    def __enter__(self) -> CancelFileWatcher:
        self.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.stop()


class LogTap:
    """Turn captured stdout and stderr writes into ``log`` events.

    Text is buffered per thread and stream until it holds a newline, then sent
    up to and including the last newline, so one event can carry several
    lines and there are never more events than writes. A partial line longer
    than 4096 characters (progress bars redrawn with a carriage return) is
    sent as is. Each buffer remembers the step that wrote it, taken from the
    execution context.
    """

    def __init__(self, emitter: EventEmitter) -> None:
        self._emitter = emitter
        self._local = threading.local()

    def _emitting(self) -> bool:
        return getattr(self._local, "emitting", False)

    def _buffers(self) -> dict[str, list[Any]]:
        buffers = getattr(self._local, "buffers", None)
        if buffers is None:
            buffers = {}
            self._local.buffers = buffers
        return buffers

    def write(self, stream: str, text: str) -> None:
        """Buffer ``text`` written to ``stream`` by the current thread."""
        # A sink that prints while handling a log event would otherwise be
        # tapped again, recursively.
        if not text or self._emitting():
            return
        from aa_recipe_manager.executor.runtime_context import get_execution_context

        step = get_execution_context().step_id
        buffers = self._buffers()
        held = buffers.get(stream)
        if held is not None and held[0] != step:
            self._send(held[0], stream, held[1])
            held = None
        pending = text if held is None else held[1] + text
        cut = pending.rfind("\n") + 1
        if cut:
            self._send(step, stream, pending[:cut])
            pending = pending[cut:]
        if len(pending) > _MAX_PARTIAL_LINE:
            self._send(step, stream, pending)
            pending = ""
        if pending:
            buffers[stream] = [step, pending]
        else:
            buffers.pop(stream, None)

    def flush(self) -> None:
        """Send whatever the current thread still holds."""
        buffers = self._buffers()
        for stream, (step, text) in list(buffers.items()):
            self._send(step, stream, text)
        buffers.clear()

    def _send(self, step: str | None, stream: str, text: str) -> None:
        self._local.emitting = True
        try:
            self._emitter.emit(
                "log",
                step=step,
                stream=stream,
                text=text,
                buffered=False,
                instance=None,
            )
        finally:
            self._local.emitting = False


class EventLogHandler(logging.Handler):
    """Turn logging records into ``log`` events (stream ``logging``)."""

    def __init__(self, emitter: EventEmitter, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._emitter = emitter

    def emit(self, record: logging.LogRecord) -> None:
        from aa_recipe_manager.executor.runtime_context import get_execution_context

        try:
            text = self.format(record)
        except Exception:
            self.handleError(record)
            return
        self._emitter.emit(
            "log",
            step=get_execution_context().step_id,
            stream="logging",
            text=text + "\n",
            buffered=False,
            instance=None,
        )
