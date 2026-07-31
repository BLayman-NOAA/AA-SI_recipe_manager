# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Concurrent steps each capture their own stdout.

The default Dask backend runs tasks as threads in the client process.
``contextlib.redirect_stdout`` rebinds the *process-global* ``sys.stdout``, so
concurrent tasks used to trample each other: output vanished or was attributed
to whichever step happened to hold the redirect. Observed in a real run as one
``Read 1 raw file(s)`` line in the log for a three-file fan-out.
"""

from __future__ import annotations

import io
import sys
import threading
import time

from aa_recipe_manager.executor.engine.logcapture import (
    ThreadRoutedStream,
    capture_output,
    install_router,
)


def _run_threads(n: int, lines: int) -> dict[str, io.StringIO]:
    """Run ``n`` threads that each print ``lines`` tagged lines while capturing."""
    buffers: dict[str, io.StringIO] = {}
    barrier = threading.Barrier(n)

    def worker(tag: str) -> None:
        buf = io.StringIO()
        buffers[tag] = buf
        barrier.wait()  # maximize overlap; this is a race test
        with capture_output(buf):
            for i in range(lines):
                print(f"{tag}-{i}")
                time.sleep(0.005)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return buffers


class TestThreadRoutedCapture:
    def test_concurrent_threads_capture_only_their_own_output(self):
        with install_router():
            buffers = _run_threads(4, 3)
        for tag, buf in buffers.items():
            captured = buf.getvalue().splitlines()
            assert captured == [f"{tag}-{i}" for i in range(3)], (
                f"{tag} captured {captured!r}"
            )

    def test_no_output_is_lost_across_threads(self):
        with install_router():
            buffers = _run_threads(4, 3)
        total = sum(len(b.getvalue().splitlines()) for b in buffers.values())
        assert total == 12

    def test_router_restores_streams_on_exit(self):
        before_out, before_err = sys.stdout, sys.stderr
        with install_router():
            assert isinstance(sys.stdout, ThreadRoutedStream)
            assert isinstance(sys.stderr, ThreadRoutedStream)
        assert sys.stdout is before_out
        assert sys.stderr is before_err

    def test_router_restores_streams_after_an_exception(self):
        before = sys.stdout
        try:
            with install_router():
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        assert sys.stdout is before

    def test_unbound_thread_falls_through_to_the_real_stream(self):
        # Dask internals and library worker pools print without binding; they
        # must still reach the console rather than land in a random step's log.
        default = io.StringIO()
        router = ThreadRoutedStream(default)
        saved = sys.stdout
        sys.stdout = router
        try:
            bound = io.StringIO()
            with capture_output(bound):
                print("mine")
            print("not mine")
        finally:
            sys.stdout = saved
        assert bound.getvalue() == "mine\n"
        assert default.getvalue() == "not mine\n"

    def test_stderr_is_captured_too(self):
        with install_router():
            buf = io.StringIO()
            with capture_output(buf):
                print("to stderr", file=sys.stderr)
        assert buf.getvalue() == "to stderr\n"

    def test_nested_capture_restores_the_outer_sink(self):
        with install_router():
            outer, inner = io.StringIO(), io.StringIO()
            with capture_output(outer):
                print("outer-before")
                with capture_output(inner):
                    print("inner")
                print("outer-after")
        assert inner.getvalue() == "inner\n"
        assert outer.getvalue() == "outer-before\nouter-after\n"

    def test_works_without_a_router_installed(self):
        # An inline run, or a task in a dedicated worker process, has no router;
        # capture_output must still capture (falling back to a global redirect).
        buf = io.StringIO()
        with capture_output(buf):
            print("captured")
        assert buf.getvalue() == "captured\n"

    def test_router_delegates_unknown_attributes_to_the_default_stream(self):
        default = io.StringIO()
        default.custom_marker = "x"  # type: ignore[attr-defined]
        router = ThreadRoutedStream(default)
        assert router.custom_marker == "x"
        assert router.isatty() is False
        assert router.writable() is True
