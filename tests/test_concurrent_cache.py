# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Stage 9 concurrency safety of the content-addressed store.

Covers the per-task ``write_token`` (two workers of one run writing the same
content hash never share an artifact key), sidecar-last visibility, and the
tiered store's hit-tier locking under many concurrent writers.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from concurrent.futures import ThreadPoolExecutor

from aa_recipe_manager.executor import CheckpointManager, TieredCheckpointStore
from aa_recipe_manager.executor.checkpoint import (
    META_FILENAME,
    entry_dir_parts,
    generate_run_id,
)


def _entry_dir(root, step_id, step_hash):
    step_dir, key = entry_dir_parts(step_id, step_hash)
    return root / step_dir / key


def test_write_token_keeps_same_run_concurrent_writers_separate(tmp_path):
    """Same run_id + same hash + two tokens -> distinct artifact dirs, one entry."""
    root = tmp_path / "ckpt"
    run_id = generate_run_id()
    mgr = CheckpointManager(root, {"s": "abcd1234"}, run_id=run_id)

    mgr.save("s", {"v": {"who": "a"}}, write_token="aaaa")
    mgr.save("s", {"v": {"who": "b"}}, write_token="bbbb")

    entry = _entry_dir(root, "s", "abcd1234")
    assert (entry / f"{run_id}.aaaa").exists()  # artifacts never interleave
    assert (entry / f"{run_id}.bbbb").exists()
    # Sidecar committed last; the entry is a single valid checkpoint.
    assert mgr.has_checkpoint("s")
    assert mgr.load("s") == {"v": {"who": "b"}}


def test_entry_without_sidecar_is_invisible(tmp_path):
    """Artifacts present but no meta.json -> not a cache hit (commit protocol)."""
    root = tmp_path / "ckpt"
    mgr = CheckpointManager(root, {"s": "abcd1234"}, run_id=generate_run_id())
    mgr.save("s", {"v": {"k": 1}})
    assert mgr.has_checkpoint("s")

    # Remove only the sidecar, leaving the artifact directory behind.
    entry = _entry_dir(root, "s", "abcd1234")
    (entry / META_FILENAME).unlink()
    fresh = CheckpointManager(root, {"s": "abcd1234"})
    assert not fresh.has_checkpoint("s")


def test_tiered_store_survives_many_concurrent_writers(tmp_path):
    """Distinct instance hashes written from many threads all load back."""
    user = CheckpointManager(
        tmp_path / "ckpt", {"s": "abcd1234"}, run_id=generate_run_id()
    )
    store = TieredCheckpointStore(user=user)
    # Realistic (well-distributed) instance hashes: real ones are sha256, so
    # their leading chars — the browsable entry-dir key — differ per instance.
    hashes = [hashlib.sha256(f"inst-{i}".encode()).hexdigest() for i in range(24)]

    def _save(i: int) -> None:
        store.save(
            "s", {"v": {"i": i}}, instance_hash=hashes[i], write_token=f"t{i}"
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(_save, range(len(hashes))))

    for i, h in enumerate(hashes):
        assert store.has_checkpoint("s", instance_hash=h)
        assert store.load("s", instance_hash=h) == {"v": {"i": i}}


def test_sidecar_last_makes_partial_write_invisible_across_managers(tmp_path):
    """A crash mid-write (artifacts, no sidecar) never becomes a phantom hit."""
    root = tmp_path / "ckpt"
    mgr = CheckpointManager(root, {"s": "abcd1234"}, run_id=generate_run_id())
    mgr.save("s", {"v": {"k": 1}})
    entry = _entry_dir(root, "s", "abcd1234")
    meta = json.loads((entry / META_FILENAME).read_text(encoding="utf-8"))
    assert meta["run_id"]
    # Simulate a torn write: keep artifacts, drop the sidecar.
    shutil.copytree(entry, tmp_path / "backup")
    (entry / META_FILENAME).unlink()
    assert not CheckpointManager(root, {"s": "abcd1234"}).has_checkpoint("s")
