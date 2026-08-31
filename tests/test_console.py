# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Prompting from inside a step, where stdout is captured away from the user.

A step that asks a question must reach the terminal, and must fail rather than
block the pipeline when there is no terminal.
"""

import io
import sys

import pytest

from aa_recipe_manager.executor.console import (
    NoConsoleError,
    console_print,
    console_stream,
    interactive_prompt,
    require_console,
)
from aa_recipe_manager.executor.engine.logcapture import capture_output, install_router
from aa_recipe_manager.executor.runtime_context import execution_context


class _FakeStdin(io.StringIO):
    """Stdin stand-in whose tty-ness is controllable."""

    def __init__(self, text="", tty=True):
        super().__init__(text)
        self._tty = tty

    def isatty(self):
        return self._tty


def test_console_stream_unwraps_the_router():
    """The router replaces sys.stdout for the run; the terminal is underneath."""
    real = sys.stdout
    with install_router():
        assert sys.stdout is not real
        assert console_stream() is real
    assert console_stream() is real


def test_console_print_bypasses_the_step_log():
    """Text a user must read to answer must not land only in the log file."""
    log = io.StringIO()
    terminal = io.StringIO()
    real = sys.stdout
    sys.stdout = terminal
    try:
        with install_router(), capture_output(log):
            print("goes to the log")
            console_print("goes to the terminal")
    finally:
        sys.stdout = real

    assert "goes to the log" in log.getvalue()
    assert "goes to the terminal" not in log.getvalue()
    assert "goes to the terminal" in terminal.getvalue()


def test_require_console_is_a_noop_outside_a_step(monkeypatch):
    """A notebook's stdin is not a tty, yet input() there works fine."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    require_console()  # no step_id in context -> not our business


def test_require_console_raises_inside_a_step_without_a_tty(monkeypatch):
    """The early check.

    Best effort: some platforms report a redirected stdin as a tty, so
    interactive_prompt's end-of-input check is the real guarantee.
    """
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=False))
    with execution_context(mode="direct", step_id="build_cal_mapping"):
        with pytest.raises(NoConsoleError) as excinfo:
            require_console(remedy='Set conflict_resolution="error".')
    message = str(excinfo.value)
    assert "no interactive input" in message
    assert 'conflict_resolution="error"' in message


def test_require_console_passes_inside_a_step_with_a_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", _FakeStdin(tty=True))
    with execution_context(mode="direct", step_id="build_cal_mapping"):
        require_console()


def test_interactive_prompt_reads_from_the_terminal(monkeypatch):
    terminal = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _FakeStdin("2\n", tty=True))
    monkeypatch.setattr(sys, "stdout", terminal)
    with execution_context(mode="direct", step_id="build_cal_mapping"):
        answer = interactive_prompt("pick: ", context="[1] a\n[2] b")

    assert answer == "2"
    # The options have to reach the terminal, or the question cannot be answered.
    assert "[1] a" in terminal.getvalue()
    assert "pick: " in terminal.getvalue()


def test_interactive_prompt_raises_at_end_of_input(monkeypatch):
    """A closed stdin must not spin the caller's retry loop."""
    monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=True))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with execution_context(mode="direct", step_id="build_cal_mapping"):
        with pytest.raises(NoConsoleError):
            interactive_prompt("pick: ")


def test_interactive_prompt_refuses_a_non_tty_before_writing(monkeypatch):
    terminal = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _FakeStdin("1\n", tty=False))
    monkeypatch.setattr(sys, "stdout", terminal)
    with execution_context(mode="direct", step_id="build_cal_mapping"):
        with pytest.raises(NoConsoleError):
            interactive_prompt("pick: ", context="[1] a")
    assert terminal.getvalue() == ""


def test_interactive_prompt_uses_builtin_input_outside_a_step(monkeypatch):
    """Outside a step nothing is captured and the caller owns stdin.

    The notebook path: ipykernel serves input() through its frontend, so
    reading sys.stdin directly would hang.
    """
    monkeypatch.setattr(sys, "stdin", _FakeStdin("", tty=False))
    seen = []
    monkeypatch.setattr("builtins.input", lambda message: seen.append(message) or "1")

    assert interactive_prompt("pick: ") == "1"
    assert seen == ["pick: "]
