# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Thread-safe stdout/stderr capture for concurrently running steps.

``contextlib.redirect_stdout`` rebinds the *process-global* ``sys.stdout``. That
is fine for one step at a time, but the default Dask backend runs tasks as
threads in the client process: several tasks entering ``redirect_stdout`` at
once trample each other, so output is lost outright or attributed to the wrong
step, and whichever redirect exits last restores a stale stream.

The fix is one router installed as ``sys.stdout``/``sys.stderr`` for the whole
run. It dispatches each write to a **per-thread** sink, so every task binds its
own buffer without touching what any other thread sees. Threads with nothing
bound (Dask internals, library worker pools) fall through to the real stream,
which is what they got before any of this existed.
"""

from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


class ThreadRoutedStream:
    """A ``sys.stdout`` stand-in that routes writes per thread.

    Only the text-stream surface the write path touches is implemented; the
    rest is delegated to the wrapped default stream.
    """

    def __init__(self, default: Any) -> None:
        self._default = default
        self._local = threading.local()

    # -- binding -------------------------------------------------------------

    def bind(self, sink: Any) -> Any:
        """Route this thread's writes to ``sink``; returns the previous sink."""
        previous = getattr(self._local, "sink", None)
        self._local.sink = sink
        return previous

    def unbind(self, previous: Any) -> None:
        self._local.sink = previous

    @property
    def default(self) -> Any:
        return self._default

    def _target(self) -> Any:
        return getattr(self._local, "sink", None) or self._default

    # -- stream surface ------------------------------------------------------

    def write(self, data: str) -> int:
        target = self._target()
        target.write(data)
        # Interleaved output is only useful if it arrives in order; a buffered
        # target would reorder it against the other threads' writes.
        try:
            target.flush()
        except Exception:  # a closed stream must not kill the run
            pass
        return len(data)

    def flush(self) -> None:
        try:
            self._target().flush()
        except Exception:
            pass

    def isatty(self) -> bool:
        return False

    def writable(self) -> bool:
        return True

    def readable(self) -> bool:
        return False

    def seekable(self) -> bool:
        return False

    def __getattr__(self, name: str) -> Any:
        # ``encoding``, ``errors``, ``fileno``, … come from the real stream.
        return getattr(self._default, name)


# One router per process, reference-counted across overlapping runs. Saving and
# restoring sys.stdout per run only survives strictly nested runs: two runs
# overlapping on different threads (an embedding service handling two requests)
# would have the first to finish restore the real stream out from under the
# second, and the second then leave its own dead router installed for good.
# Routing is per *thread*, so one shared router serves any number of concurrent
# runs correctly; only the outermost installer swaps the streams, and only the
# last one out puts them back.
_INSTALL_LOCK = threading.Lock()
_ROUTERS: tuple[ThreadRoutedStream, ThreadRoutedStream] | None = None
_SAVED_STREAMS: tuple[Any, Any] | None = None
_INSTALL_DEPTH = 0


@contextmanager
def install_router() -> Iterator[tuple[ThreadRoutedStream, ThreadRoutedStream]]:
    """Install thread-routing ``sys.stdout`` / ``sys.stderr`` for a run.

    Safe to nest and to enter concurrently from several threads: overlapping
    runs share one router and the streams are restored once the last of them
    exits.
    """
    global _ROUTERS, _SAVED_STREAMS, _INSTALL_DEPTH

    with _INSTALL_LOCK:
        if _INSTALL_DEPTH == 0:
            _SAVED_STREAMS = (sys.stdout, sys.stderr)
            _ROUTERS = (
                ThreadRoutedStream(sys.stdout),
                ThreadRoutedStream(sys.stderr),
            )
            sys.stdout, sys.stderr = _ROUTERS
        _INSTALL_DEPTH += 1
        routers = _ROUTERS
    assert routers is not None
    try:
        yield routers
    finally:
        with _INSTALL_LOCK:
            _INSTALL_DEPTH -= 1
            if _INSTALL_DEPTH == 0:
                if _SAVED_STREAMS is not None:
                    sys.stdout, sys.stderr = _SAVED_STREAMS
                _SAVED_STREAMS = None
                _ROUTERS = None


@contextmanager
def capture_output(sink: Any) -> Iterator[None]:
    """Send this thread's stdout/stderr to ``sink`` for the duration.

    Uses the installed router when there is one (concurrent runs). Without a
    router — an inline run, or a task in a dedicated worker *process* where the
    global streams are nobody else's business — it falls back to the plain
    global redirect, which is equivalent there.
    """
    out, err = sys.stdout, sys.stderr
    if isinstance(out, ThreadRoutedStream) and isinstance(err, ThreadRoutedStream):
        prev_out = out.bind(sink)
        prev_err = err.bind(sink)
        try:
            yield
        finally:
            out.unbind(prev_out)
            err.unbind(prev_err)
        return

    from contextlib import redirect_stderr, redirect_stdout

    with redirect_stdout(sink), redirect_stderr(sink):
        yield
