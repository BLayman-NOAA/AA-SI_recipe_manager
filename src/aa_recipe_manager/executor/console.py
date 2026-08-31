# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Reaching the terminal from inside a running step.

A step's stdout and stderr are redirected to the run log for the duration of
``execute_step`` (see ``engine/logcapture.py``), and the log sink does not
include the console. These helpers write to the stream the router wrapped, and
refuse when there is no interactive input to read, so a step that needs to ask
something fails with a message instead of blocking the pipeline.
"""

from __future__ import annotations

import sys
import threading
from typing import Any

from aa_recipe_manager.executor.engine.logcapture import ThreadRoutedStream
from aa_recipe_manager.executor.runtime_context import get_execution_context

#: Steps can run concurrently as threads in one process, and two prompts at
#: once would interleave their reads from the single stdin.
_PROMPT_LOCK = threading.Lock()


class NoConsoleError(RuntimeError):
    """Raised when a step asks the user something with no terminal attached."""


_NO_CONSOLE_MESSAGE = (
    "This step needs to ask you a question, but the run has no interactive "
    "input to read an answer from. Recipe steps run with their output captured "
    "into the run log, and distributed backends run them in workers with no "
    "stdin at all, so the prompt would print nowhere and then block forever."
)


def _no_console(remedy: str) -> NoConsoleError:
    return NoConsoleError(
        _NO_CONSOLE_MESSAGE if not remedy else f"{_NO_CONSOLE_MESSAGE}\n\n{remedy}"
    )


def under_executor() -> bool:
    """Return True when a step is currently executing.

    ``step_id`` is set by both the in-process runner and the distributed
    worker, and is None in the zero-value context a notebook sees.
    """
    return get_execution_context().step_id is not None


def console_stream() -> Any:
    """Return the terminal stdout, unwrapping the run's log capture.

    During a run ``sys.stdout`` is a :class:`ThreadRoutedStream` whose writes
    go to the current step's log sink; the stream it wrapped is kept on its
    ``default`` property.
    """
    stream = sys.stdout
    if isinstance(stream, ThreadRoutedStream):
        return stream.default
    return stream


def console_print(*args, **kwargs) -> None:
    """Print to the terminal rather than into the step's log."""
    kwargs.setdefault("file", console_stream())
    print(*args, **kwargs)


def require_console(remedy: str = "") -> None:
    """Raise when this step cannot prompt the user.

    A no-op outside the executor, where the caller owns stdin: a notebook's
    ``input`` works despite stdin not being a tty. Best effort, since some
    platforms report a redirected stdin as a tty; :func:`interactive_prompt`
    catches the rest when the read returns nothing.

    Args:
        remedy: What to do instead, appended to the message.

    Raises:
        NoConsoleError: When stdin is not an interactive terminal.
    """
    if not under_executor():
        return
    stdin = sys.stdin
    try:
        interactive = stdin is not None and stdin.isatty()
    except (AttributeError, OSError, ValueError):
        interactive = False
    if not interactive:
        raise _no_console(remedy)


def interactive_prompt(message: str, context: str = "", remedy: str = "") -> str:
    """Write *message* to the terminal and read one line back.

    Args:
        message: The question, written without a trailing newline.
        context: Text the user needs in order to answer, written first. The
            caller should also print it so the run log keeps a record.
        remedy: What to do instead, used in the error when there is no
            terminal.

    Returns:
        The line the user entered, without its newline.

    Raises:
        NoConsoleError: When stdin is not an interactive terminal, or when
            reading hits end of input.
    """
    if not under_executor():
        # ipykernel serves input() through its frontend, and sys.stdin there is
        # neither a tty nor where the answer arrives.
        return input(message if not context else f"{context}\n{message}")

    require_console(remedy)

    out = console_stream()
    with _PROMPT_LOCK:
        try:
            if context:
                out.write(context if context.endswith("\n") else context + "\n")
            out.write(message)
            out.flush()
            line = sys.stdin.readline()
        except (EOFError, OSError, ValueError) as exc:
            raise _no_console(remedy) from exc

    if line == "":
        # readline returns "" only at end of input; a bare Enter gives "\n".
        raise _no_console(remedy)
    return line.rstrip("\n")
