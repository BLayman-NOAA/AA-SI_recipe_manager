# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Read a run's ``events.jsonl`` while the run process is still writing it."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

#: The log stream of progress lines.
PROGRESS_STREAM = "progress"

_FINISHED_LABEL = {
    "computed": "computed",
    "hit-user-cache": "loaded from user cache",
    "hit-survey-cache": "loaded from survey cache",
    "marker": "already done",
    "skipped": "skipped: cached",
    "pruned": "skipped: cached",
    "untargeted": "not needed",
}

#: Dispositions whose time is worth showing.
_TIMED = {"computed", "hit-user-cache", "hit-survey-cache", "marker"}


def read_chunk(path: Path, offset: int = 0) -> tuple[list[dict[str, Any]], int]:
    """Parse the complete lines after a byte offset.

    A last line without its newline is still being written, so it is left for
    the next read. A complete line that is not JSON is skipped.

    Args:
        path: The events file. A missing file reads as empty.
        offset: Byte offset to start at.

    Returns:
        The events, and the offset just past the last complete line.
    """
    try:
        with open(path, "rb") as handle:
            handle.seek(offset)
            data = handle.read()
    except FileNotFoundError:
        return [], offset
    end = data.rfind(b"\n") + 1
    events = []
    for line in data[:end].split(b"\n"):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events, offset + end


def read_events(path: Path, *, after: int = 0) -> list[dict[str, Any]]:
    """Every complete event in the file with ``seq`` above ``after``."""
    events, _ = read_chunk(path)
    return [event for event in events if event.get("seq", 0) > after]


def _seconds(value: float) -> str:
    return f"{value:.2f}s"


def _count(value: int, noun: str) -> str:
    return f"{value} {noun}{'' if value == 1 else 's'}"


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _spread(times: Sequence[float]) -> str:
    total = sum(times)
    return (
        f"sum {_seconds(total)}, avg {_seconds(total / len(times))}, "
        f"min {_seconds(min(times))}, max {_seconds(max(times))}"
    )


def format_step_time(elapsed: float, instance_seconds: Sequence[float] = ()) -> str:
    """A step's time, with the spread when it fanned out, as the CLI prints it."""
    if len(instance_seconds) < 2:
        return _seconds(elapsed)
    return f"{len(instance_seconds)} instances: {_spread(instance_seconds)}"


def _text(value: Any) -> str:
    return "?" if value is None else str(value)


def _prefix(data: dict[str, Any]) -> str:
    index, total, step = data.get("index"), data.get("total"), data.get("step")
    return f"[{_text(index)}/{_text(total)}] {_text(step)} ..."


def _run_started(data: dict[str, Any]) -> str:
    targets = ", ".join(data.get("targets") or [])
    if data.get("only"):
        parts = [f"only {targets}"]
    else:
        parts = [f"to {targets}" if targets else "all"]
    if data.get("force"):
        parts.append("force")
    if data.get("force_steps"):
        parts.append(f"force {', '.join(data['force_steps'])}")
    if _number(data.get("step_count")):
        parts.append(_count(data["step_count"], "step"))
    return f"run ... start ({', '.join(parts)})"


def _step_started(data: dict[str, Any]) -> str:
    instances = data.get("instances")
    count = f" ({_count(instances, 'instance')})" if _number(instances) else ""
    return f"{_prefix(data)} start{count}"


def _step_finished(data: dict[str, Any]) -> str:
    disposition = str(data.get("disposition") or "")
    instances = data.get("instances")
    times = data.get("instance_seconds") or []
    parts = []
    if instances:
        counts = [
            f"{number} {word}"
            for number, word in (
                (instances["computed"], "computed"),
                (instances["cached"], "loaded"),
                (instances["skipped"], "skipped"),
            )
            if number
        ]
        many = _count(instances["count"], "instance")
        parts.append(f"{many}: {', '.join(counts)}" if counts else many)
    if disposition in _TIMED and _number(data.get("seconds")):
        if instances and len(times) >= 2:
            parts.append(_spread(times))
        else:
            parts.append(format_step_time(data["seconds"], times))
    label = _FINISHED_LABEL.get(disposition, disposition)
    details = f" ({'; '.join(parts)})" if parts else ""
    return f"{_prefix(data)} {label}{details}"


def _step_failed(data: dict[str, Any]) -> str:
    seconds = data.get("seconds")
    elapsed = f" ({format_step_time(seconds)})" if _number(seconds) else ""
    return f"{_prefix(data)} failed{elapsed}"


def _run_finished(data: dict[str, Any]) -> str:
    seconds = data.get("seconds")
    elapsed = f" ({_seconds(seconds)})" if _number(seconds) else ""
    message = f": {data['message']}" if data.get("message") else ""
    return f"run ... {data.get('status') or 'finished'}{elapsed}{message}"


_PROGRESS = {
    "run_started": _run_started,
    "step_started": _step_started,
    "step_finished": _step_finished,
    "step_failed": _step_failed,
    "run_finished": _run_finished,
}


def progress_text(event: dict[str, Any]) -> str | None:
    """The progress line of an event, worded like the CLI's, or None.

    The client's ``runs/progress.ts`` writes the same text for the runs it
    holds; ``web/test/fixtures/progress.expected.json`` keeps the two in step.
    """
    render = _PROGRESS.get(event.get("kind"))
    return render(event.get("data") or {}) if render else None


def log_entries(
    events: list[dict[str, Any]], *, step: str | None, stream: str | None
) -> list[dict[str, Any]]:
    """The log entries of a run's events, optionally for one step or stream.

    ``log`` events become entries as they are. Run and step starts and
    finishes become ``progress`` entries, worded like the CLI's progress
    lines. Each ``step_failed`` also becomes a ``traceback`` entry holding its
    traceback, or ``error_type: message`` when it has none. The client's
    reducer builds the same entries for the runs it holds.
    """
    entries = []
    for event in events:
        kind = event.get("kind")
        data = event.get("data") or {}
        owner = None if kind in ("run_started", "run_finished") else data.get("step")
        found = []
        progress = progress_text(event)
        if progress is not None:
            found.append((PROGRESS_STREAM, progress, None, False))
        if kind == "log":
            found.append(
                (
                    data.get("stream", "stdout"),
                    data.get("text", ""),
                    data.get("instance"),
                    bool(data.get("buffered")),
                )
            )
        elif kind == "step_failed":
            text = data.get("traceback") or (
                f"{data.get('error_type')}: {data.get('message')}"
            )
            found.append(("traceback", text, data.get("instance"), False))
        for name, text, instance, buffered in found:
            if step is not None and owner != step:
                continue
            if stream is not None and name != stream:
                continue
            entries.append(
                {
                    "seq": event["seq"],
                    "time": event["time"],
                    "step": owner,
                    "stream": name,
                    "text": text,
                    "buffered": buffered,
                    "instance": instance,
                }
            )
    return entries


async def follow(
    path: Path,
    *,
    after: int = 0,
    interval: float = 0.1,
    stop: Callable[[], Awaitable[bool]],
    keepalive: float | None = None,
) -> AsyncIterator[dict[str, Any] | None]:
    """Yield events as they are written, until ``run_finished`` or ``stop``.

    The file may not exist yet; it is read once it does. ``stop`` is asked
    before each read, so a run that ended between two polls still has its
    last events read.

    Args:
        path: The events file.
        after: Yield only events with a higher ``seq``.
        interval: Seconds between polls.
        stop: Returns True when following should end.
        keepalive: When set, yield None after this many seconds without an
            event, so the caller can keep its connection open.
    """
    offset = 0
    quiet_since = time.monotonic()
    while True:
        stopping = await stop()
        events, offset = read_chunk(path, offset)
        for event in events:
            if event.get("seq", 0) <= after:
                continue
            yield event
            quiet_since = time.monotonic()
            if event.get("kind") == "run_finished":
                return
        if stopping:
            return
        if keepalive is not None and time.monotonic() - quiet_since >= keepalive:
            yield None
            quiet_since = time.monotonic()
        await asyncio.sleep(interval)
