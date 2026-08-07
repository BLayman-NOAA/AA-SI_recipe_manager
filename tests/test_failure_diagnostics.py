# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""What a failed run reports about itself.

A run that fails must surface the error that actually failed it, together with
enough context to act on: the step's own output, and the chain of exceptions
behind it. These are regression tests for a real incident where a
``PermissionError`` raised by temp-dir cleanup replaced the step error that
caused it, and the failing step's stdout was discarded, leaving a report that
named a directory but no cause.
"""

from __future__ import annotations

import logging
import os
import stat
import sys
import types

import pytest

from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.executor.engine.tasks import TASK_LOG_ATTR, attach_task_log
from aa_recipe_manager.fsutil import grant_access, rmtree, rmtree_onerror
from aa_recipe_manager.model.types import (
    DAGNode,
    Dependency,
    Implementation,
    PipelineDAG,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="POSIX permission semantics"
)

_HELPER_MODULE_NAME = "ar_diagnostics_test_helpers"


@pytest.fixture
def noisy_helper() -> types.ModuleType:
    """A module whose callable prints, then raises."""
    module = types.ModuleType(_HELPER_MODULE_NAME)

    def noisy_boom(value: int) -> int:
        print(f"converted {value} of 7 files")
        print("about to fail")
        raise RuntimeError("conversion failed")

    def quiet_ok(value: int) -> int:
        return value + 1

    module.noisy_boom = noisy_boom  # type: ignore[attr-defined]
    module.quiet_ok = quiet_ok  # type: ignore[attr-defined]
    sys.modules[_HELPER_MODULE_NAME] = module
    yield module
    sys.modules.pop(_HELPER_MODULE_NAME, None)


def _one_step_dag(step_id: str, callable_name: str) -> PipelineDAG:
    spec = Spec(
        op=step_id,
        description="",
        inputs={"value": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    impl = Implementation(
        op=step_id,
        key="d",
        callable_path=f"{_HELPER_MODULE_NAME}.{callable_name}",
        dependency=Dependency(name="pytest", version=">=7.0", source="pypi"),
    )
    node = DAGNode(
        step=Step(id=step_id, op=step_id, inputs={"value": "${inputs.seed}"}),
        spec=spec,
        implementation=impl,
    )
    recipe = Recipe(
        name="diagnostics_pipeline", version="1.0.0", steps=[node.step],
        schema_version="1",
    )
    return PipelineDAG(
        recipe=recipe,
        nodes={step_id: node},
        edges=[],
        topological_order=[step_id],
    )


# ---------------------------------------------------------------------------
# rmtree permission handling
# ---------------------------------------------------------------------------


@posix_only
def test_rmtree_removes_tree_containing_unlistable_directory(tmp_path):
    # 0o200 is exactly what `os.chmod(path, stat.S_IWRITE)` leaves behind: a
    # directory that cannot be listed or traversed, so every later scandir of
    # it raises EACCES naming that path.
    store = tmp_path / "sample.zarr"
    array_dir = store / "Sonar" / "Beam_group1" / "backscatter_r"
    array_dir.mkdir(parents=True)
    (array_dir / "0.0.0").write_bytes(b"chunk")
    os.chmod(array_dir, 0o200)

    rmtree(store)

    assert not store.exists()


@posix_only
def test_rmtree_removes_entry_under_unwritable_parent(tmp_path):
    # Unlinking needs write+execute on the PARENT, which is why granting
    # permissions on the failing path alone is not enough.
    store = tmp_path / "sample.zarr"
    group = store / "Beam_group1"
    group.mkdir(parents=True)
    (group / "backscatter_r").write_bytes(b"chunk")
    os.chmod(group, 0o500)

    rmtree(store)

    assert not store.exists()


@posix_only
def test_grant_access_preserves_existing_mode_bits(tmp_path):
    target = tmp_path / "group"
    target.mkdir()
    os.chmod(target, 0o750)

    grant_access(target)

    mode = stat.S_IMODE(os.stat(target).st_mode)
    assert mode & stat.S_IRWXU == stat.S_IRWXU  # owner can read/write/traverse
    assert mode & stat.S_IRGRP  # group bits are not clobbered


def test_grant_access_on_missing_path_is_a_noop(tmp_path):
    grant_access(tmp_path / "not-there")  # must not raise


def test_onerror_grants_access_to_parent_and_path_then_retries(
    tmp_path, monkeypatch
):
    # Platform-independent check of the handler's contract: the POSIX tests
    # above cover the real semantics but only run on POSIX.
    granted: list[str] = []
    monkeypatch.setattr(
        "aa_recipe_manager.fsutil.grant_access",
        lambda path: granted.append(str(path)),
    )
    target = tmp_path / "group" / "backscatter_r"
    retried: list[str] = []

    rmtree_onerror(lambda p: retried.append(str(p)), str(target), None)

    assert granted == [str(tmp_path / "group"), str(target)]
    assert retried == [str(target)]


def test_onerror_never_overwrites_a_mode(tmp_path):
    # The defect being fixed: `os.chmod(path, stat.S_IWRITE)` assigns 0o200 and
    # discards every other bit. grant_access must only ever add bits.
    target = tmp_path / "dir"
    target.mkdir()
    before = stat.S_IMODE(os.stat(target).st_mode)

    grant_access(target)

    after = stat.S_IMODE(os.stat(target).st_mode)
    assert after & before == before, f"{before:o} -> {after:o} lost bits"


# ---------------------------------------------------------------------------
# Cleanup must never decide the run's outcome
# ---------------------------------------------------------------------------


def test_cleanup_failure_does_not_mask_step_error(
    tmp_path, monkeypatch, noisy_helper
):
    def exploding_cleanup(_temp_loc):
        raise PermissionError(
            13,
            "Permission denied",
            str(tmp_path / "exe_temp" / "boom.zarr" / "backscatter_r"),
        )

    monkeypatch.setattr(
        SequentialExecutor, "_cleanup_temp_dir", staticmethod(exploding_cleanup)
    )

    with pytest.raises(PipelineExecutionError) as excinfo:
        SequentialExecutor().execute(
            _one_step_dag("boom", "noisy_boom"),
            inputs={"seed": 3},
            outputs_dir=str(tmp_path / "outputs"),
            temp_dir=str(tmp_path / "exe_temp"),
        )

    # The step error survives; the cleanup PermissionError does not replace it.
    assert excinfo.value.step_id == "boom"
    assert isinstance(excinfo.value.original, RuntimeError)


def test_cleanup_failure_on_successful_run_is_a_warning(
    tmp_path, monkeypatch, noisy_helper
):
    def exploding_cleanup(_temp_loc):
        raise PermissionError(13, "Permission denied", "exe_temp")

    monkeypatch.setattr(
        SequentialExecutor, "_cleanup_temp_dir", staticmethod(exploding_cleanup)
    )

    result = SequentialExecutor().execute(
        _one_step_dag("fine", "quiet_ok"),
        inputs={"seed": 3},
        outputs_dir=str(tmp_path / "outputs"),
        temp_dir=str(tmp_path / "exe_temp"),
    )

    assert result.executed_steps == ["fine"]
    assert any(
        "failed to remove temp dir" in entry for entry in result.logs
    ), result.logs


# ---------------------------------------------------------------------------
# A failing task's captured output
# ---------------------------------------------------------------------------


def test_attach_task_log_stores_text_on_exception():
    exc = RuntimeError("nope")
    attach_task_log(exc, "converted 3 of 7 files\n")
    assert getattr(exc, TASK_LOG_ATTR) == "converted 3 of 7 files\n"


def test_attach_task_log_keeps_the_innermost_capture():
    exc = RuntimeError("nope")
    attach_task_log(exc, "first")
    attach_task_log(exc, "second")
    assert getattr(exc, TASK_LOG_ATTR) == "first"


def test_attach_task_log_ignores_empty_text():
    exc = RuntimeError("nope")
    attach_task_log(exc, "")
    assert getattr(exc, TASK_LOG_ATTR, None) is None


def test_failing_step_output_reaches_the_log(tmp_path, noisy_helper):
    with pytest.raises(PipelineExecutionError):
        SequentialExecutor().execute(
            _one_step_dag("boom", "noisy_boom"),
            inputs={"seed": 3},
            outputs_dir=str(tmp_path / "outputs"),
            temp_dir=str(tmp_path / "exe_temp"),
        )

    log = (tmp_path / "outputs" / "logs" / "standard_out.txt").read_text(
        encoding="utf-8"
    )
    # The prints the step made before raising are the record of how far it got.
    assert "converted 3 of 7 files" in log
    assert "about to fail" in log
    assert "boom FAILED" in log


def test_failing_step_reports_nonzero_elapsed(tmp_path, noisy_helper):
    seen: list[tuple[str, float, object]] = []

    class _Progress:
        def on_step_start(self, step_id, index, total):
            pass

        def on_step_end(
            self, step_id, index, total, *, skipped=False, elapsed=0.0,
            error=None, instance_seconds=(),
        ):
            seen.append((step_id, elapsed, error))

    with pytest.raises(PipelineExecutionError):
        SequentialExecutor().execute(
            _one_step_dag("boom", "noisy_boom"),
            inputs={"seed": 3},
            outputs_dir=str(tmp_path / "outputs"),
            temp_dir=str(tmp_path / "exe_temp"),
            progress=_Progress(),
        )

    failures = [entry for entry in seen if entry[2] is not None]
    assert failures, seen
    # 0.0 would say the step failed instantly, which hid how long read_raw
    # actually ran before dying.
    assert failures[0][1] > 0.0


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def test_doctor_report_covers_every_section(tmp_path):
    from aa_recipe_manager import diagnostics

    report = diagnostics.build_report(
        str(tmp_path / "aa-recipe.config.yaml"),
        {"temp_dir": str(tmp_path / "exe_temp"), "user_cache_dir": None},
    )

    for heading in ("packages", "platform", "memory", "run config", "umask"):
        assert heading in report
    assert "aa-recipe-manager" in report
    assert "echopype" in report
    # The probe writes the same nested shape a zarr store does.
    assert "Beam_group1" in report
    assert "probe: removed cleanly" in report
    assert "(unset)" in report  # user_cache_dir


def test_doctor_reports_a_remote_path_without_probing(tmp_path):
    from aa_recipe_manager import diagnostics

    report = diagnostics.build_report(None, {"user_cache_dir": "gs://bucket/x"})

    assert "remote: skipping local probe" in report
    assert "(none found)" in report


def test_doctor_probe_leaves_no_trace(tmp_path):
    from aa_recipe_manager import diagnostics

    temp_dir = tmp_path / "exe_temp"
    diagnostics.build_report(None, {"temp_dir": str(temp_dir)})

    # A directory the probe created is removed again, so running doctor never
    # changes what the next run sees.
    assert not temp_dir.exists()


def test_traceback_prints_when_debug_was_requested(monkeypatch, capsys):
    from aa_recipe_manager import cli

    monkeypatch.setattr(cli, "_REQUESTED_LOG_LEVEL", "DEBUG")
    try:
        raise OSError(28, "No space left on device")
    except OSError as exc:
        cli._echo_traceback(exc)

    err = capsys.readouterr().err
    assert "Traceback (most recent call last)" in err
    assert "No space left on device" in err


def test_traceback_is_not_gated_on_logging_being_enabled(monkeypatch, capsys):
    # Importing echopype calls logging.disable(logging.WARNING), a process-wide
    # mute that makes isEnabledFor(DEBUG) return False even at root level DEBUG.
    # Gating on it printed "re-run with --log-level DEBUG" to someone who had
    # already passed exactly that, and withheld the traceback they asked for.
    from aa_recipe_manager import cli

    monkeypatch.setattr(cli, "_REQUESTED_LOG_LEVEL", "DEBUG")
    logging.disable(logging.WARNING)
    try:
        assert not logging.getLogger().isEnabledFor(logging.DEBUG)
        try:
            raise OSError(28, "No space left on device")
        except OSError as exc:
            cli._echo_traceback(exc)
    finally:
        logging.disable(logging.NOTSET)

    assert "Traceback (most recent call last)" in capsys.readouterr().err


def test_traceback_hint_shown_without_debug(monkeypatch, capsys):
    from aa_recipe_manager import cli

    monkeypatch.setattr(cli, "_REQUESTED_LOG_LEVEL", "INFO")
    cli._echo_traceback(OSError(28, "No space left on device"))

    err = capsys.readouterr().err
    assert "--log-level DEBUG" in err
    assert "Traceback (most recent call last)" not in err


def test_doctor_reports_an_unprobeable_directory(tmp_path):
    from aa_recipe_manager import diagnostics

    blocker = tmp_path / "not_a_dir"
    blocker.write_text("i am a file", encoding="utf-8")

    report = diagnostics.build_report(None, {"temp_dir": str(blocker)})

    assert "probe: cannot create directory" in report
