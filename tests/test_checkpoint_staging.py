# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Staged-vs-streamed remote zarr checkpoint upload.

A remote zarr checkpoint can be written by streaming ``to_zarr`` straight to the
bucket, or by staging it to local scratch and bulk-uploading with
``fs.put(recursive=True)`` — the latter measured ~2.5x faster on a real GCS
uplink because it parallelizes the many per-object PUTs. Which path is taken is
gated by an estimated-size threshold so a survey larger than local disk still
streams. These tests pin the decision logic and prove both paths write an
identical, reloadable store (only the transport differs).
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest
import xarray as xr

from aa_recipe_manager.executor.checkpoint import (
    _DEFAULT_STAGE_MAX_BYTES,
    _estimated_uncompressed_bytes,
    _serialize_output,
    _should_stage,
    _stage_parent_dir,
    _stage_threshold_bytes,
    _staged_remote_zarr,
    _write_tree_consolidated_once,
    _write_zarr_with_retry,
)
from aa_recipe_manager.executor.runtime_context import execution_context
from aa_recipe_manager.storage import StorageLocation


# --- threshold configuration -----------------------------------------------


class TestStageThreshold:
    def test_default_when_env_unset(self, monkeypatch):
        monkeypatch.delenv("AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES", raising=False)
        assert _stage_threshold_bytes() == _DEFAULT_STAGE_MAX_BYTES

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES", str(500_000_000))
        assert _stage_threshold_bytes() == 500_000_000

    def test_zero_disables_staging(self, monkeypatch):
        # The setting for a survey larger than local disk: always stream.
        monkeypatch.setenv("AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES", "0")
        assert _stage_threshold_bytes() == 0
        ds = xr.Dataset({"v": ("x", np.zeros(10))})
        assert _should_stage(ds) is False

    def test_garbage_env_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES", "not-a-number")
        assert _stage_threshold_bytes() == _DEFAULT_STAGE_MAX_BYTES

    def test_negative_env_clamped_to_zero(self, monkeypatch):
        monkeypatch.setenv("AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES", "-5")
        assert _stage_threshold_bytes() == 0


# --- size estimation + decision --------------------------------------------


class TestShouldStage:
    def test_small_dataset_stages(self, monkeypatch):
        monkeypatch.delenv("AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES", raising=False)
        ds = xr.Dataset({"v": ("x", np.zeros(1000, dtype="f8"))})
        assert _should_stage(ds) is True

    def test_dataset_over_threshold_streams(self, monkeypatch):
        ds = xr.Dataset({"v": ("x", np.zeros(1000, dtype="f8"))})  # 8000 bytes
        monkeypatch.setenv("AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES", "4000")
        assert _estimated_uncompressed_bytes(ds) == 8000
        assert _should_stage(ds) is False

    def test_estimate_uses_logical_nbytes_without_computing(self):
        # A dask-backed array reports nbytes from shape x dtype, not by loading.
        pytest.importorskip("dask")
        ds = xr.Dataset({"v": ("x", np.zeros(2000, dtype="f8"))}).chunk({"x": 100})
        assert ds["v"].data.__class__.__module__.startswith("dask")
        assert _estimated_uncompressed_bytes(ds) == 2000 * 8

    def test_unknown_size_streams(self):
        # nbytes unavailable -> 0 -> stream (the always-safe path).
        class _Opaque:
            pass

        assert _estimated_uncompressed_bytes(_Opaque()) == 0
        assert _should_stage(_Opaque()) is False


# --- both paths write an identical, reloadable store -----------------------


def _roundtrip(value, tmp_path, monkeypatch, *, stage: bool) -> xr.Dataset:
    """Serialize ``value`` to a local zarr store via the stage/stream decision.

    A local target exercises the same _should_stage gate and _serialize_output
    dispatch; the staged and streamed branches converge on the local writer, so
    the reloaded store must match regardless of the flag.
    """
    monkeypatch.setenv(
        "AA_RECIPE_CHECKPOINT_STAGE_MAX_BYTES",
        str(_DEFAULT_STAGE_MAX_BYTES) if stage else "0",
    )
    base = StorageLocation.parse(str(tmp_path / ("staged" if stage else "streamed")))
    target, tag = _serialize_output(value, base, preferred_format="zarr")
    assert tag == "zarr"
    return xr.open_zarr(target.as_local_path())


def test_staged_and_streamed_datasets_are_identical(tmp_path, monkeypatch):
    ds = xr.Dataset(
        {"backscatter": (("channel", "ping"), np.arange(60.0).reshape(5, 12))},
        coords={"channel": list("abcde"), "ping": np.arange(12)},
    )
    staged = _roundtrip(ds, tmp_path, monkeypatch, stage=True)
    streamed = _roundtrip(ds, tmp_path, monkeypatch, stage=False)
    xr.testing.assert_identical(staged, streamed)
    xr.testing.assert_identical(staged.load(), ds)


# --- consolidating once is equivalent to xarray's per-node consolidation ----


def _echodata_shaped_tree() -> xr.DataTree:
    """A tree with EchoData's group layout: what makes the repeated
    consolidation expensive is the node count, not the data."""
    groups = ["Environment", "Platform", "Platform/NMEA", "Provenance",
              "Sonar", "Sonar/Beam_group1", "Vendor_specific"]
    tree = {"/": xr.Dataset({"v": ("x", np.arange(3.0))})}
    for i, group in enumerate(groups):
        tree["/" + group] = xr.Dataset({"v": (f"d{i}", np.arange(4.0))})
    return xr.DataTree.from_dict(tree)


def test_consolidate_once_matches_per_node_consolidation(tmp_path):
    tree = _echodata_shaped_tree()
    per_node = tmp_path / "per_node.zarr"
    once = tmp_path / "once.zarr"

    # The reference write is the very thing this helper replaces, and it is
    # itself ~9% flaky on Windows for exactly the reason documented in
    # _write_tree_consolidated_once -- so it needs the retry to be a stable
    # baseline. _write_tree_consolidated_once is called bare on purpose: it
    # renames .zmetadata once, so it does not need one.
    _write_zarr_with_retry(
        lambda: tree.to_zarr(str(per_node), mode="w", zarr_format=2, consolidated=True),
        per_node,
    )
    _write_tree_consolidated_once(tree, str(once))

    def keys(root):
        return {str(p.relative_to(root)).replace("\\", "/")
                for p in root.rglob("*") if p.is_file()}

    assert keys(per_node) == keys(once)
    for key in keys(once):
        assert (per_node / key).read_bytes() == (once / key).read_bytes(), key

    reloaded = xr.open_datatree(str(once), engine="zarr", consolidated=True)
    assert reloaded.identical(
        xr.open_datatree(str(per_node), engine="zarr", consolidated=True)
    )


def test_consolidate_once_writes_root_zmetadata_exactly_once(tmp_path, monkeypatch):
    # The point of the helper: one rename of the root .zmetadata instead of one
    # per node, because that rename is what races with Windows AV/indexing.
    renames = []
    original = pathlib.Path.replace

    def counting(self, target):
        if pathlib.Path(target).name == ".zmetadata":
            renames.append(str(target))
        return original(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", counting)
    store = tmp_path / "once.zarr"

    def write():
        renames.clear()  # a retried attempt starts its count over
        _write_tree_consolidated_once(_echodata_shaped_tree(), str(store))

    # One rename is still one chance to lose the race; retry so the assertion
    # below measures the design, not the weather.
    _write_zarr_with_retry(write, store)

    assert len(renames) == 1


# --- the scratch write retries Windows rename failures ----------------------


class _RecordingFS:
    def __init__(self):
        self.puts = []

    def put(self, src, dst, recursive=False):
        self.puts.append((src, dst, recursive))


class _FakeTarget:
    """Minimal StorageLocation stand-in: staging only needs name/fs/fs_path."""

    def __init__(self, name="echodata.zarr"):
        self.name = name
        self.fs = _RecordingFS()
        self.fs_path = f"bucket/{name}"


def test_staged_write_retries_on_permission_error(tmp_path, monkeypatch):
    # zarr's LocalStore writes each key to a .partial file then renames it into
    # place. On Windows that rename intermittently fails with PermissionError
    # ([WinError 5]) while an AV/indexer holds the just-written file -- the
    # .zmetadata key is the usual casualty because it is rewritten once per
    # DataTree node. Staging must survive it exactly as a local target does.
    monkeypatch.setattr("aa_recipe_manager.executor.checkpoint.time.sleep", lambda _s: None)
    target = _FakeTarget()
    attempts = []

    def flaky_write(local_store):
        attempts.append(local_store)
        if len(attempts) == 1:
            local_store.mkdir(parents=True, exist_ok=True)
            (local_store / ".zmetadata").write_text("half-written")
            raise PermissionError(5, "Access is denied", str(local_store / ".zmetadata"))
        local_store.mkdir(parents=True, exist_ok=True)
        (local_store / ".zmetadata").write_text("complete")

    with execution_context(temp_dir=tmp_path / "exe_temp"):
        _staged_remote_zarr(target, flaky_write)

    assert len(attempts) == 2
    # The partially-written store is cleared before the retry, so the upload
    # never mixes bytes from the failed attempt with the good one.
    assert len(target.fs.puts) == 1
    src, dst, recursive = target.fs.puts[0]
    assert dst == "bucket/echodata.zarr" and recursive is True
    assert src == str(attempts[1])


def test_staged_write_reraises_when_retries_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr("aa_recipe_manager.executor.checkpoint.time.sleep", lambda _s: None)
    target = _FakeTarget()

    def always_fails(local_store):
        raise PermissionError(5, "Access is denied", str(local_store))

    with execution_context(temp_dir=tmp_path / "exe_temp"):
        with pytest.raises(PermissionError):
            _staged_remote_zarr(target, always_fails)

    assert target.fs.puts == []  # nothing uploaded from a failed write
    # The scratch dir is cleaned up even on the failure path.
    assert list((tmp_path / "exe_temp").glob("aa_recipe_ckpt_*")) == []


# --- staging scratch dir honors the run's own temp_dir ----------------------


class TestStageParentDir:
    def test_no_execution_context_falls_back_to_none(self):
        # Outside any execution_context, get_execution_context() returns the
        # zero-value default -- same as tempfile.mkdtemp's own system default.
        assert _stage_parent_dir() is None

    def test_local_temp_dir_is_used_and_created(self, tmp_path):
        run_temp = tmp_path / "exe_temp" / "remove_noise"
        with execution_context(temp_dir=run_temp):
            result = _stage_parent_dir()
        assert result == str(run_temp)
        assert run_temp.is_dir()

    def test_remote_temp_dir_falls_back_to_none(self):
        # Staging is inherently local-scratch-then-upload; a temp_dir that's
        # itself remote (e.g. gs://) has no local directory to offer.
        with execution_context(temp_dir="gs://bucket/exe_temp"):
            assert _stage_parent_dir() is None
