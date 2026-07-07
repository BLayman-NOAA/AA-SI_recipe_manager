# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the StorageLocation seam and fsspec-backed (gs://-shaped) storage.

``memory://`` is used as a credential-free stand-in for ``gs://``: it is a
non-local, mapper-based fsspec filesystem, so it exercises the same remote code
paths (URL joins, fs.open, fs.rm, storage_options threading) without needing GCP.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import fsspec
import pytest
import xarray as xr

from aa_recipe_manager.executor import SequentialExecutor
from aa_recipe_manager.executor.checkpoint import (
    CACHE_METADATA_DIR,
    ZARR_DATA_DIR,
    CheckpointManager,
    compute_step_hashes,
)
from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.model.types import (
    DAGNode,
    Implementation,
    PortDeclaration,
    Spec,
    Step,
)
from aa_recipe_manager.storage import StorageLocation, is_remote_url

# Reuse the executor test scaffolding (stub callables + DAG builders).
from test_executor import _dep, _install_helper_module, _linear_inc_dag, _make_dag, _HELPER_MODULE_NAME


def _boom_dag():
    """Single ``boom`` step that raises at runtime."""
    spec = Spec(
        op="boom",
        description="",
        inputs={"value": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    impl = Implementation(
        op="boom",
        key="d",
        callable_path=f"{_HELPER_MODULE_NAME}.boom",
        dependency=_dep(),
        output_map={"out": "__return__"},
    )
    node = DAGNode(
        step=Step(id="boom", op="boom", inputs={"value": "${inputs.seed}"}),
        spec=spec,
        implementation=impl,
    )
    return _make_dag([node], [])


@pytest.fixture(autouse=True)
def clear_memory_fs():
    """The in-process MemoryFileSystem store is global; reset it per test."""
    mem = fsspec.filesystem("memory")
    mem.store.clear()
    mem.pseudo_dirs[:] = [""]
    yield
    mem.store.clear()
    mem.pseudo_dirs[:] = [""]


# ---------------------------------------------------------------------------
# is_remote_url scheme detection
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("gs://bucket/prefix", True),
        ("memory://cache/x", True),
        ("s3://bucket/key", True),
        (r"C:\Users\me\recipe_cache", False),
        ("C:/Users/me/recipe_cache", False),
        ("C://Users/me", False),  # degenerate Windows form, still local
        ("./recipe_cache", False),
        ("recipe_cache", False),
        ("/abs/unix/path", False),
        (Path("recipe_cache"), False),
    ],
)
def test_is_remote_url(value, expected):
    assert is_remote_url(value) is expected


# ---------------------------------------------------------------------------
# StorageLocation: parse / join / parent / conversions
# ---------------------------------------------------------------------------


def test_parse_local_is_local_and_pathlike():
    loc = StorageLocation.parse(r"C:\tmp\recipe_cache")
    assert loc.is_local
    assert loc.name == "recipe_cache"
    assert loc.parent.name == "tmp"
    assert os.fspath(loc) == r"C:\tmp\recipe_cache"
    assert isinstance(loc.as_context_value(), Path)


def test_parse_remote_is_not_local():
    loc = StorageLocation.parse("memory://cache/recipe_cache")
    assert not loc.is_local
    assert loc.name == "recipe_cache"
    assert loc.parent.url == "memory://cache"
    assert loc.as_context_value() is loc  # remote stays a StorageLocation


def test_truediv_joins_scheme_safely():
    loc = StorageLocation.parse("memory://cache/recipe_cache")
    child = loc / ZARR_DATA_DIR / "step_out.zarr"
    assert child.url == "memory://cache/recipe_cache/zarr_data/step_out.zarr"
    assert not child.is_local


def test_local_truediv_matches_pathlib():
    loc = StorageLocation.parse(r"C:\tmp\cache")
    child = loc / "zarr_data" / "x.zarr"
    assert Path(os.fspath(child)) == Path(r"C:\tmp\cache") / "zarr_data" / "x.zarr"


def test_fspath_raises_on_remote_with_guidance():
    loc = StorageLocation.parse("memory://cache/recipe_cache")
    with pytest.raises(TypeError, match="remote storage location"):
        os.fspath(loc)


def test_as_local_path_raises_on_remote():
    loc = StorageLocation.parse("memory://cache/x")
    with pytest.raises(ValueError, match="remote storage location"):
        loc.as_local_path()


def test_storage_options_propagate_through_join_and_parent():
    loc = StorageLocation.parse("memory://cache/a/b", {"token": "anon"})
    assert (loc / "c").storage_options == {"token": "anon"}
    assert loc.parent.storage_options == {"token": "anon"}


# ---------------------------------------------------------------------------
# StorageLocation: remote filesystem operations on memory://
# ---------------------------------------------------------------------------


def test_remote_write_read_exists_rm_roundtrip():
    loc = StorageLocation.parse("memory://cache/dir/file.json")
    assert not loc.exists()
    loc.write_text('{"a": 1}')
    assert loc.exists()
    assert json.loads(loc.read_text()) == {"a": 1}
    loc.rm()
    assert not loc.exists()


def test_remote_rm_missing_is_noop():
    StorageLocation.parse("memory://cache/nope").rm()  # must not raise


def test_remote_glob_returns_locations():
    base = StorageLocation.parse("memory://cache/meta")
    (base / "a__cache_meta.json").write_text("{}")
    (base / "b__cache_meta.json").write_text("{}")
    (base / "other.txt").write_text("x")
    hits = base.glob("*__cache_meta.json")
    names = sorted(loc.name for loc in hits)
    assert names == ["a__cache_meta.json", "b__cache_meta.json"]
    assert all(not h.is_local for h in hits)


# ---------------------------------------------------------------------------
# CheckpointManager against a remote (memory://) cache
# ---------------------------------------------------------------------------


def test_remote_checkpoint_json_round_trip():
    manager = CheckpointManager("memory://cache/recipe_cache", {"s": "h"})
    manager.save("s", {"meta": {"k": [1, 2, 3]}})
    assert manager.has_checkpoint("s")
    assert manager.load("s") == {"meta": {"k": [1, 2, 3]}}


def test_remote_checkpoint_pickle_round_trip():
    manager = CheckpointManager("memory://cache/recipe_cache", {"s": "h"})
    payload = {"tuple": (1, 2), "set": {3, 4}}  # not JSON-safe -> pickle
    manager.save("s", {"obj": payload})
    assert manager.load("s")["obj"] == payload


def test_remote_checkpoint_zarr_dataset_round_trip():
    manager = CheckpointManager("memory://cache/recipe_cache", {"sv": "h"})
    ds = xr.Dataset({"value": ("x", [10, 20, 30])})
    manager.save("sv", {"ds_Sv": ds})

    meta = json.loads(
        StorageLocation.parse("memory://cache/recipe_cache/cache_metadata/sv__cache_meta.json").read_text()
    )
    assert meta["outputs"]["ds_Sv"]["format"] == "zarr"
    # Stored relative to the cache root (POSIX separators).
    assert meta["outputs"]["ds_Sv"]["path"].startswith(f"{ZARR_DATA_DIR}/")
    assert manager.has_checkpoint("sv")

    loaded = manager.load("sv")
    assert list(loaded["ds_Sv"]["value"].values) == [10, 20, 30]


def test_remote_checkpoint_marker_round_trip():
    manager = CheckpointManager("memory://cache/recipe_cache", {"plot": "h"})
    manager.save_marker("plot")
    assert manager.has_marker("plot")
    assert not manager.has_checkpoint("plot")


def test_remote_netcdf_checkpoint_format_rejected():
    with pytest.raises(ValueError, match="requires a local output_dir"):
        CheckpointManager(
            "memory://cache/recipe_cache", {"s": "h"}, preferred_format="netcdf"
        )


def test_legacy_absolute_path_meta_still_loads(tmp_path):
    """A meta sidecar with an absolute artifact path (pre-relative-path caches)
    must still resolve on load."""
    manager = CheckpointManager(tmp_path / "ckpt", {"s": "h"})
    # Write a real json artifact, then a legacy meta pointing at its absolute path.
    artifact = tmp_path / "ckpt" / "json_data" / "s_val.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text('{"legacy": true}', encoding="utf-8")
    meta_dir = tmp_path / "ckpt" / CACHE_METADATA_DIR
    meta_dir.mkdir(parents=True, exist_ok=True)
    (meta_dir / "s__cache_meta.json").write_text(
        json.dumps(
            {
                "step_id": "s",
                "step_hash": "h",
                "marker": False,
                "outputs": {"val": {"path": str(artifact), "format": "json"}},
            }
        ),
        encoding="utf-8",
    )
    assert manager.has_checkpoint("s")
    assert manager.load("s") == {"val": {"legacy": True}}


# ---------------------------------------------------------------------------
# Executor end-to-end against a remote cache
# ---------------------------------------------------------------------------


def test_executor_remote_cache_hit_on_second_run():
    _install_helper_module().call_log.clear()
    dag = _linear_inc_dag()
    cache = "memory://cache/recipe_cache"

    first = SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=cache, checkpoint_mode="eager"
    )
    assert first.executed_steps == ["start", "first", "second"]
    assert first.outputs["second"]["out"] == 4

    # Second run: nothing re-runs. The terminal step loads from the memory://
    # cache and the backward planner prunes its now-unneeded ancestors.
    helper = _install_helper_module()
    helper.call_log.clear()
    second = SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=cache, checkpoint_mode="eager"
    )
    assert second.executed_steps == []
    assert helper.call_log == []  # no callable actually invoked
    assert "second" in second.skipped_steps
    assert set(second.pruned_steps) == {"start", "first"}
    assert second.outputs["second"]["out"] == 4


# ---------------------------------------------------------------------------
# Remote outputs: logs
# ---------------------------------------------------------------------------


def test_remote_log_uploaded_to_outputs_dir():
    _install_helper_module().call_log.clear()
    dag = _linear_inc_dag()
    result = SequentialExecutor().execute(
        dag,
        inputs={"seed": 1},
        output_dir="memory://cache/recipe_cache",
        outputs_dir="memory://cache/outputs",
        checkpoint_mode="eager",
        log_destination="file",
    )
    log_url = "memory://cache/outputs/logs/standard_out.txt"
    assert str(result.log_file) == log_url
    fs = fsspec.filesystem("memory")
    assert fs.exists(log_url)
    with fs.open(log_url, "r") as fh:
        text = fh.read()
    assert "step start" in text and "second" in text


def test_remote_log_uploaded_even_when_a_step_fails():
    _install_helper_module().call_log.clear()
    dag = _boom_dag()
    with pytest.raises(PipelineExecutionError):
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir="memory://cache/recipe_cache",
            outputs_dir="memory://cache/outputs",
            checkpoint_mode="eager",
            log_destination="file",
        )
    # The finally block still uploads the captured log to the bucket.
    assert fsspec.filesystem("memory").exists(
        "memory://cache/outputs/logs/standard_out.txt"
    )


def test_remote_artifacts_dir_fails_loud_for_pathlib_consumer():
    """A consumer that has not been upgraded to fsspec must fail loudly (not
    silently write to a mangled local 'gs:/...' dir) when handed a remote dir.

    ``os.fspath`` surfaces the actionable guidance message; ``Path(...)`` in
    3.13 re-wraps it but still raises TypeError naming StorageLocation. Either
    way the consumer cannot silently proceed."""
    loc = StorageLocation.parse("memory://run/outputs")
    with pytest.raises(TypeError, match="remote storage location"):
        os.fspath(loc)
    with pytest.raises(TypeError):  # pathlib rewraps, but still fails loudly
        Path(loc)


# ---------------------------------------------------------------------------
# Phase 4: path-param validation and remote fingerprinting
# ---------------------------------------------------------------------------


def test_validate_path_param_accepts_remote_url_without_network():
    """A gs:// path param must pass validation (no existence check, no mkdir)."""
    from aa_recipe_manager.model.types import ParamDeclaration, Step
    from aa_recipe_manager.parser.dag_builder import _validate_path_param

    step = Step(id="s", op="op")
    decl = ParamDeclaration(type="path", constraints={"must_exist": True})
    errors: list[str] = []
    _validate_path_param(step, "raw_input_folder", decl, "gs://bucket/survey", errors)
    assert errors == []
    # And no bogus local 'gs:/bucket' directory was created.
    assert not Path("gs:/bucket").exists()


def test_remote_fingerprint_detects_content_change():
    from aa_recipe_manager.executor.checkpoint import _path_fingerprint

    url = "memory://inputs/data.bin"
    StorageLocation.parse(url).write_text("aaa")
    fp1 = _path_fingerprint(url)
    assert fp1["kind"] == "file"

    StorageLocation.parse(url).write_text("aaaaaaaaaa")  # larger -> different size
    fp2 = _path_fingerprint(url)
    assert fp1 != fp2  # size change invalidates the cache key


def test_remote_fingerprint_missing_object():
    from aa_recipe_manager.executor.checkpoint import _path_fingerprint

    assert _path_fingerprint("memory://inputs/absent.bin")["kind"] == "missing"


def test_remote_fingerprint_degrades_without_driver():
    """A gs:// input with gcsfs absent must not crash hashing; it degrades."""
    from aa_recipe_manager.executor.checkpoint import _path_fingerprint

    with pytest.warns(RuntimeWarning, match="could not fingerprint remote path"):
        fp = _path_fingerprint("gs://bucket/does-not-matter")
    assert fp["kind"] == "remote-unverified"
