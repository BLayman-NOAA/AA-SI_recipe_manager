# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the Stage 7b content-addressed checkpoint layout.

Covers the ``<root>/<hash[:2]>/<hash[:16]>/meta.json`` addressing, the sidecar-v2
schema, the sidecar-last commit protocol (an entry without its sidecar is
invisible), per-``run_id`` artifact coexistence, the reworked ``clean``
semantics, and legacy (v1, step_id-addressed) caches becoming invisible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aa_recipe_manager.executor import CheckpointManager, SequentialExecutor
from aa_recipe_manager.executor.checkpoint import (
    CACHE_METADATA_DIR,
    META_FILENAME,
    ZARR_DATA_DIR,
    compute_step_fingerprints,
    compute_step_hashes,
    entry_dir_parts,
    generate_run_id,
)


def _entry_dir(root, step_id, step_hash):
    """The content-addressed entry directory (``<step_id>/<hash>``) under ``root``."""
    step_dir, key = entry_dir_parts(step_id, step_hash)
    return root / step_dir / key

from test_executor import (  # noqa: F401  (helper scaffolding)
    _artifact_path,
    _linear_inc_dag,
    _meta_for_step,
    _meta_names,
    helper_module,
)


# ---------------------------------------------------------------------------
# Layout + sidecar v2
# ---------------------------------------------------------------------------


def test_entry_layout_and_sidecar_v2_fields(helper_module, tmp_path):
    dag = _linear_inc_dag()
    out = tmp_path / "ckpt"
    SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=out, checkpoint_mode="eager"
    )

    fingerprints = compute_step_fingerprints(dag, {"seed": 1})
    for step_id in ("start", "first", "second"):
        step_hash = fingerprints.hashes[step_id]
        meta_file = _entry_dir(out, step_id, step_hash) / META_FILENAME
        assert meta_file.exists()
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        assert meta["schema_version"] == 2
        assert meta["step_id"] == step_id
        assert meta["step_hash"] == step_hash
        assert meta["run_id"]
        assert meta["created_at"]
        assert meta["recipe"] == {"name": "stage6_pipeline", "version": "1.0.0"}
        # The persisted payload is byte-equivalent to what was hashed.
        assert meta["fingerprint_payload"] == fingerprints.payloads[step_id]
        # Artifact paths are relative to the hash dir, run_id-first.
        for entry in meta["outputs"].values():
            assert entry["path"].startswith(f"{meta['run_id']}/")
            assert _artifact_path(out, meta, "out").exists()


def test_save_requires_known_hash(tmp_path):
    manager = CheckpointManager(tmp_path / "ckpt", {})
    with pytest.raises(ValueError, match="no step hash known"):
        manager.save("mystery", {"v": 1})
    with pytest.raises(ValueError, match="no step hash known"):
        manager.save_marker("mystery")


def test_artifact_urls_resolve(tmp_path):
    manager = CheckpointManager(tmp_path / "ckpt", {"s": "abcd1234"})
    manager.save("s", {"v": {"k": 1}})
    urls = manager.artifact_urls("s")
    assert set(urls) == {"v"}
    assert Path(urls["v"]).exists()


# ---------------------------------------------------------------------------
# Commit protocol: the sidecar is the commit point
# ---------------------------------------------------------------------------


def test_artifacts_without_sidecar_are_invisible(tmp_path):
    manager = CheckpointManager(tmp_path / "ckpt", {"s": "abcd1234"})
    manager.save("s", {"v": {"k": 1}})
    assert manager.has_checkpoint("s")

    meta_file = _entry_dir(tmp_path / "ckpt", "s", "abcd1234") / META_FILENAME
    meta_file.unlink()
    assert not manager.has_checkpoint("s")
    with pytest.raises(FileNotFoundError):
        manager.load("s")


def test_artifacts_exist_before_sidecar_is_written(tmp_path, monkeypatch):
    """Artifacts-first, sidecar-last write order (the commit invariant)."""
    manager = CheckpointManager(tmp_path / "ckpt", {"s": "abcd1234"})
    real_write = CheckpointManager._write_meta
    observed: dict[str, bool] = {}

    def spying_write(self, meta):
        observed["artifacts_exist"] = all(
            (self._entry_dir(meta.step_id, meta.step_hash) / entry["path"]).exists()
            for entry in meta.outputs.values()
        )
        return real_write(self, meta)

    monkeypatch.setattr(CheckpointManager, "_write_meta", spying_write)
    manager.save("s", {"v": {"k": 1}})
    assert observed["artifacts_exist"]


def test_failed_sidecar_write_leaves_no_visible_entry(tmp_path, monkeypatch):
    manager = CheckpointManager(tmp_path / "ckpt", {"s": "abcd1234"})

    def failing_write(self, meta):
        raise OSError("simulated interrupted write")

    monkeypatch.setattr(CheckpointManager, "_write_meta", failing_write)
    with pytest.raises(OSError):
        manager.save("s", {"v": {"k": 1}})

    fresh = CheckpointManager(tmp_path / "ckpt", {"s": "abcd1234"})
    assert not fresh.has_checkpoint("s")


def test_concurrent_run_ids_coexist_last_sidecar_wins(tmp_path):
    """Writers never share object keys; last sidecar wins benignly."""
    root = tmp_path / "ckpt"
    writer_a = CheckpointManager(root, {"s": "abcd1234"}, run_id=generate_run_id())
    writer_b = CheckpointManager(root, {"s": "abcd1234"}, run_id=generate_run_id())
    assert writer_a.run_id != writer_b.run_id

    writer_a.save("s", {"v": {"who": "a"}})
    writer_b.save("s", {"v": {"who": "b"}})

    entry_dir = _entry_dir(root, "s", "abcd1234")
    assert (entry_dir / writer_a.run_id).exists()  # artifacts never interleave
    assert (entry_dir / writer_b.run_id).exists()

    meta = json.loads((entry_dir / META_FILENAME).read_text(encoding="utf-8"))
    assert meta["run_id"] == writer_b.run_id  # last sidecar wins
    # Either writer's manager loads the committed entry.
    assert writer_a.load("s") == {"v": {"who": "b"}}


# ---------------------------------------------------------------------------
# clean() under content addressing
# ---------------------------------------------------------------------------


def test_clean_stale_preserves_other_recipes_entries(helper_module, tmp_path):
    root = tmp_path / "ckpt"
    dag = _linear_inc_dag()
    SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=root, checkpoint_mode="eager"
    )
    # Another recipe's entry sharing the same cache root.
    foreign = CheckpointManager(root, {"foreign_step": "f0f0f0f0"})
    foreign.save("foreign_step", {"v": {"k": 1}})

    # Same DAG, different input value -> all this recipe's hashes are stale.
    manager = CheckpointManager(root, compute_step_hashes(dag, {"seed": 2}))
    manager.clean(dag, mode="stale")

    remaining = _meta_names(root)
    assert remaining == {"foreign_step"}


def test_clean_stale_keeps_current_hashes(helper_module, tmp_path):
    root = tmp_path / "ckpt"
    dag = _linear_inc_dag()
    SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=root, checkpoint_mode="eager"
    )
    manager = CheckpointManager(root, compute_step_hashes(dag, {"seed": 1}))
    removed = manager.clean(dag, mode="stale")
    assert removed == []
    assert _meta_names(root) == {"start", "first", "second"}


def test_clean_all_sweeps_legacy_v1_dirs(helper_module, tmp_path):
    root = tmp_path / "ckpt"
    dag = _linear_inc_dag()
    SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=root, checkpoint_mode="eager"
    )
    # Plant a legacy (v1, step_id-addressed) cache alongside, using the
    # historical root-level dir names (`cache_metadata`, `zarr_data`).
    legacy_meta = root / "cache_metadata" / "old__cache_meta.json"
    legacy_meta.parent.mkdir(parents=True, exist_ok=True)
    legacy_meta.write_text("{}", encoding="utf-8")
    legacy_store = root / "zarr_data" / "old_out.zarr"
    legacy_store.mkdir(parents=True, exist_ok=True)

    manager = CheckpointManager(root, compute_step_hashes(dag, {"seed": 1}))
    manager.clean(dag, mode="all")

    assert not _meta_names(root)
    assert not legacy_meta.exists()
    assert not legacy_store.exists()


def test_legacy_v1_cache_is_invisible(helper_module, tmp_path):
    """A v1 (step_id-addressed) cache neither hits nor crashes — recompute."""
    root = tmp_path / "ckpt"
    dag = _linear_inc_dag()
    hashes = compute_step_hashes(dag, {"seed": 1})
    # Plant v1-layout sidecars with matching hashes at the OLD addresses.
    meta_dir = root / CACHE_METADATA_DIR
    meta_dir.mkdir(parents=True, exist_ok=True)
    for step_id, step_hash in hashes.items():
        (meta_dir / f"{step_id}__cache_meta.json").write_text(
            json.dumps(
                {
                    "step_id": step_id,
                    "step_hash": step_hash,
                    "marker": False,
                    "outputs": {},
                }
            ),
            encoding="utf-8",
        )

    result = SequentialExecutor().execute(
        dag, inputs={"seed": 1}, output_dir=root, checkpoint_mode="eager"
    )
    # Everything recomputed; v1 entries were never consulted.
    assert result.executed_steps == ["start", "first", "second"]
    assert [c[0] for c in helper_module.call_log] == ["add_one"] * 3
