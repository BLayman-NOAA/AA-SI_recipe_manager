# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Give the caller's matplotlib backend back when a run finishes.

Plotting ops switch the *process-global* matplotlib backend to a headless one
(``aa_si_visualization._artifact_output.configure_matplotlib_backend``) so a
figure never touches a GUI toolkit from a worker thread. That is right for the
duration of a run and wrong to leave behind: under the CLI the process is ours
to keep, but an embedding process (a notebook, a long-lived service) chose its
backend deliberately and still needs it after ``execute`` returns.

Nothing here imports matplotlib. The guard acts only on a matplotlib that is
already loaded, so a run that never plots costs one ``sys.modules`` lookup.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterator


def _read_backend(mpl: Any) -> Any:
    """The configured backend, read *without* resolving matplotlib's sentinel.

    ``matplotlib.get_backend()`` resolves the "auto" sentinel as a side effect,
    which is exactly the GUI-toolkit selection this guard exists to avoid
    triggering. Bypassing ``rcParams.__getitem__`` reads the raw setting: either
    a backend name, or the sentinel object meaning "not chosen yet".
    """
    try:
        return dict.__getitem__(mpl.rcParams, "backend")
    except Exception:
        return None


def _same_backend(current: Any, target: Any) -> bool:
    """Whether the backend is already what we would restore it to."""
    if isinstance(current, str) and isinstance(target, str):
        return current.lower() == target.lower()
    return current is target


def _restore_backend(mpl: Any, target: Any) -> None:
    """Put ``target`` back as the configured backend.

    A named backend goes through the public ``use``, which runs matplotlib's
    own switch machinery. The auto sentinel cannot: both ``use`` and
    ``rcParams.__setitem__`` deliberately ignore it once a backend has been
    chosen, so restoring "not chosen yet" needs the same raw dict access the
    read side uses. That is not a real switch anyway -- it defers the choice
    again, and the next ``get_backend`` resolves it fresh.
    """
    if isinstance(target, str):
        mpl.use(target, force=True)
    else:
        dict.__setitem__(mpl.rcParams, "backend", target)


@contextmanager
def preserve_backend() -> Iterator[None]:
    """Restore the matplotlib backend the caller had when the run started.

    Three cases, all handled by restoring whatever the raw setting was:

    * matplotlib never loaded -> no-op (the common CLI case).
    * a backend was chosen (a notebook's inline backend, say) -> put it back.
    * loaded but unresolved -> put the sentinel back, so the next plot
      auto-selects normally instead of silently inheriting the run's headless
      backend.
    """
    mpl = sys.modules.get("matplotlib")
    previous = _read_backend(mpl) if mpl is not None else None
    try:
        yield
    finally:
        # Structured as nested conditions, never an early ``return``: a return
        # inside ``finally`` discards an exception the run is still raising, and
        # this guard must be invisible to a failing step.
        mpl = sys.modules.get("matplotlib")
        if mpl is not None:
            target = previous
            if target is None:
                # matplotlib was imported *during* the run, so there is no
                # caller setting to go back to; hand back the auto sentinel.
                target = mpl.rcParamsDefault.get("backend")
            if target is not None and not _same_backend(_read_backend(mpl), target):
                try:
                    _restore_backend(mpl, target)
                except Exception:  # a bad restore must never fail a run
                    pass
