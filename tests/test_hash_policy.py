# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the Stage 7a hash-policy hardening.

Covers the cache-identity levers (``Spec.cache_key`` / ``Spec.version`` /
``Implementation.version`` / ``CustomSpec.cache_key``), the recipe-level
cache epoch, fingerprint-payload retention, and the checksum-preferring
remote path fingerprints (``memory://`` stands in for ``gs://``, with
monkeypatched ``info``/``ls`` injecting gcsfs-style checksum fields).
"""

from __future__ import annotations

import hashlib
import itertools
import json

import fsspec
import pytest
from fsspec.implementations.memory import MemoryFileSystem

from aa_recipe_manager.executor.checkpoint import (
    _remote_path_fingerprint,
    compute_step_fingerprints,
    compute_step_hashes,
)
from aa_recipe_manager.model.types import (
    CustomSpec,
    DAGNode,
    ExecutionHints,
    Implementation,
    PipelineDAG,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)
from aa_recipe_manager.registry.loader import load_builtin_registry

from test_executor import (  # noqa: F401  (helper scaffolding)
    _HELPER_MODULE_NAME,
    _dep,
    _linear_inc_dag,
    _make_dag,
    _path_input_dag,
)


# ---------------------------------------------------------------------------
# Cache epoch
# ---------------------------------------------------------------------------


def test_cache_epoch_changes_every_step_hash() -> None:
    dag = _linear_inc_dag()
    baseline = compute_step_hashes(dag)

    dag.recipe.execution = ExecutionHints(cache_epoch="2026-07")
    bumped = compute_step_hashes(dag)

    assert set(baseline) == set(bumped)
    for step_id in baseline:
        assert baseline[step_id] != bumped[step_id]


def test_same_epoch_hashes_stable() -> None:
    dag_a = _linear_inc_dag()
    dag_a.recipe.execution = ExecutionHints(cache_epoch="e1")
    dag_b = _linear_inc_dag()
    dag_b.recipe.execution = ExecutionHints(cache_epoch="e1")
    assert compute_step_hashes(dag_a) == compute_step_hashes(dag_b)


def test_unset_epoch_matches_default_hints() -> None:
    """``execution:`` block present without an epoch hashes like no block."""
    dag_a = _linear_inc_dag()
    dag_b = _linear_inc_dag()
    dag_b.recipe.execution = ExecutionHints()
    assert compute_step_hashes(dag_a) == compute_step_hashes(dag_b)


# ---------------------------------------------------------------------------
# Op identity: cache_key / version levers
# ---------------------------------------------------------------------------


def _single_step_dag(
    *,
    op: str,
    cache_key: str | None = None,
    spec_version: str | None = None,
    impl_key: str = "default",
    callable_name: str = "add_one",
    impl_version: str | None = None,
) -> PipelineDAG:
    spec = Spec(
        op=op,
        description="test op",
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
        cache_key=cache_key,
        version=spec_version,
    )
    impl = Implementation(
        op=op,
        key=impl_key,
        callable_path=f"{_HELPER_MODULE_NAME}.{callable_name}",
        dependency=_dep(),
        output_map={"out": "__return__"},
        version=impl_version,
    )
    node = DAGNode(
        step=Step(id="only", op=op, inputs={"x": "${inputs.seed}"}),
        spec=spec,
        implementation=impl,
    )
    return _make_dag([node], [])


def test_cache_key_makes_op_and_callable_rename_cache_neutral() -> None:
    """Renaming the op, impl key, and callable is invisible with a pinned key."""
    original = _single_step_dag(op="add_one", cache_key="stable_identity")
    renamed = _single_step_dag(
        op="increment_value",
        cache_key="stable_identity",
        impl_key="renamed_impl",
        callable_name="renamed_arg",
    )
    assert compute_step_hashes(original) == compute_step_hashes(renamed)


def test_op_rename_without_cache_key_invalidates() -> None:
    """Without a pinned cache_key the op name is the identity (default)."""
    original = _single_step_dag(op="add_one")
    renamed = _single_step_dag(op="increment_value")
    assert compute_step_hashes(original) != compute_step_hashes(renamed)


def test_callable_rename_alone_is_cache_neutral() -> None:
    """callable_path / implementation key no longer enter the fingerprint."""
    original = _single_step_dag(op="add_one")
    moved = _single_step_dag(
        op="add_one", impl_key="other", callable_name="renamed_arg"
    )
    assert compute_step_hashes(original) == compute_step_hashes(moved)


def test_spec_version_bump_changes_hash() -> None:
    v1 = _single_step_dag(op="add_one")
    v2 = _single_step_dag(op="add_one", spec_version="2")
    assert compute_step_hashes(v1) != compute_step_hashes(v2)


def test_impl_version_bump_changes_hash() -> None:
    v1 = _single_step_dag(op="add_one")
    v2 = _single_step_dag(op="add_one", impl_version="2")
    assert compute_step_hashes(v1) != compute_step_hashes(v2)


def test_version_bump_invalidates_descendants() -> None:
    dag_a = _linear_inc_dag()
    dag_b = _linear_inc_dag()
    dag_b.nodes["first"].spec = dag_b.nodes["first"].spec.model_copy(
        update={"version": "2"}
    )
    hashes_a = compute_step_hashes(dag_a)
    hashes_b = compute_step_hashes(dag_b)
    assert hashes_a["start"] == hashes_b["start"]  # upstream untouched
    assert hashes_a["first"] != hashes_b["first"]
    assert hashes_a["second"] != hashes_b["second"]  # Merkle descendant


def test_explicit_cache_key_equal_to_op_is_hash_neutral() -> None:
    """Pinning ``cache_key: <op>`` in a spec never changes existing hashes."""
    implicit = _single_step_dag(op="add_one")
    pinned = _single_step_dag(op="add_one", cache_key="add_one")
    assert compute_step_hashes(implicit) == compute_step_hashes(pinned)


def test_all_builtin_specs_have_pinned_cache_key() -> None:
    """Convention guard: every shipped spec pins its cache identity."""
    registry = load_builtin_registry()
    missing = [
        op for op in registry.list_ops() if registry.get_spec(op).cache_key is None
    ]
    assert missing == []


# ---------------------------------------------------------------------------
# Custom-spec cache_key
# ---------------------------------------------------------------------------


def _custom_step_dag(
    *,
    callable_name: str,
    description: str,
    cache_key: str | None,
) -> PipelineDAG:
    custom = CustomSpec(
        description=description,
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
        callable_path=f"{_HELPER_MODULE_NAME}.{callable_name}",
        output_map={"out": "__return__"},
        cache_key=cache_key,
    )
    spec = Spec(
        op="custom",
        description=description,
        inputs={"x": PortDeclaration(type="int")},
        outputs={"out": PortDeclaration(type="int")},
    )
    node = DAGNode(
        step=Step(
            id="only",
            op="custom",
            inputs={"x": "${inputs.seed}"},
            custom_spec=custom,
        ),
        spec=spec,
        implementation=None,
    )
    return _make_dag([node], [])


def test_custom_spec_cache_key_ignores_callable_path() -> None:
    a = _custom_step_dag(
        callable_name="add_one", description="v1 docs", cache_key="my_custom"
    )
    b = _custom_step_dag(
        callable_name="renamed_arg", description="v2 docs", cache_key="my_custom"
    )
    assert compute_step_hashes(a) == compute_step_hashes(b)


def test_custom_spec_without_cache_key_keeps_full_dump() -> None:
    a = _custom_step_dag(callable_name="add_one", description="docs", cache_key=None)
    b = _custom_step_dag(
        callable_name="renamed_arg", description="docs", cache_key=None
    )
    assert compute_step_hashes(a) != compute_step_hashes(b)


# ---------------------------------------------------------------------------
# Fingerprint payload retention
# ---------------------------------------------------------------------------


def test_fingerprint_payload_hash_roundtrip() -> None:
    """sha256 of the canonical dump of each stored payload equals the hash."""
    dag = _linear_inc_dag()
    dag.recipe.execution = ExecutionHints(cache_epoch="e1")
    fingerprints = compute_step_fingerprints(dag, {"seed": 1})
    assert set(fingerprints.hashes) == set(fingerprints.payloads)
    for step_id, payload in fingerprints.payloads.items():
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()
        assert digest == fingerprints.hashes[step_id]
        assert payload["epoch"] == "e1"
        assert payload["fingerprint"]["cache_key"] == "add_one"


def test_compute_step_hashes_matches_fingerprints() -> None:
    dag = _linear_inc_dag()
    assert compute_step_hashes(dag) == compute_step_fingerprints(dag).hashes


# ---------------------------------------------------------------------------
# Remote path fingerprints: checksum preference (memory:// as gs:// stand-in)
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_fs() -> MemoryFileSystem:
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    yield fs
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]


@pytest.fixture
def memory_file(memory_fs: MemoryFileSystem) -> str:
    with memory_fs.open("/raw/a.dat", "wb") as fh:
        fh.write(b"payload")
    return "memory://raw/a.dat"


def _patch_info(monkeypatch: pytest.MonkeyPatch, extra_factory) -> None:
    """Wrap MemoryFileSystem.info to merge in gcsfs-style fields per call."""
    real_info = MemoryFileSystem.info

    def fake_info(self, path, **kwargs):
        info = dict(real_info(self, path, **kwargs))
        info.update(extra_factory())
        return info

    monkeypatch.setattr(MemoryFileSystem, "info", fake_info)


def test_remote_fingerprint_prefers_checksum_over_mtime(
    memory_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    mtimes = itertools.count()  # a re-upload changes mtime, not content
    _patch_info(
        monkeypatch,
        lambda: {"md5Hash": "abc123==", "mtime": next(mtimes)},
    )
    first = _remote_path_fingerprint(memory_file)
    second = _remote_path_fingerprint(memory_file)
    assert first == second
    assert first["checksum"] == ["md5", "abc123=="]
    assert "mtime_ns" not in first


def test_remote_fingerprint_checksum_change_invalidates(
    memory_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A mutable holder rather than an iterator: MemoryFileSystem.exists()
    # also routes through info(), so per-call iterators would desync.
    current = {"crc32c": "aaa=="}
    _patch_info(monkeypatch, lambda: dict(current))
    first = _remote_path_fingerprint(memory_file)
    current["crc32c"] = "bbb=="
    second = _remote_path_fingerprint(memory_file)
    assert first != second
    assert first["checksum"] == ["crc32c", "aaa=="]
    assert second["checksum"] == ["crc32c", "bbb=="]


def test_remote_fingerprint_falls_back_to_size(memory_file: str) -> None:
    # No backend checksum -> fall back to size only. mtime is never used (it is
    # upload time on object stores and would false-miss on re-upload).
    fingerprint = _remote_path_fingerprint(memory_file)
    assert fingerprint["kind"] == "file"
    assert fingerprint["size"] == len(b"payload")
    assert "checksum" not in fingerprint
    assert "mtime_ns" not in fingerprint


def test_remote_dir_entries_carry_checksums(
    memory_fs: MemoryFileSystem, monkeypatch: pytest.MonkeyPatch
) -> None:
    with memory_fs.open("/raw/a.dat", "wb") as fh:
        fh.write(b"one")
    with memory_fs.open("/raw/b.dat", "wb") as fh:
        fh.write(b"two")

    real_ls = MemoryFileSystem.ls

    def fake_ls(self, path, detail=False, **kwargs):
        entries = real_ls(self, path, detail=detail, **kwargs)
        if not detail:
            return entries
        patched = []
        for entry in entries:
            entry = dict(entry)
            entry["crc32c"] = f"sum-{entry['name'].rsplit('/', 1)[-1]}"
            patched.append(entry)
        return patched

    monkeypatch.setattr(MemoryFileSystem, "ls", fake_ls)

    fingerprint = _remote_path_fingerprint("memory://raw")
    assert fingerprint["kind"] == "dir"
    names = {entry["name"]: entry for entry in fingerprint["entries"]}
    assert names["a.dat"]["checksum"] == ["crc32c", "sum-a.dat"]
    assert names["b.dat"]["checksum"] == ["crc32c", "sum-b.dat"]


def test_unverified_remote_fingerprint_is_unique_per_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def raiser(*args, **kwargs):
        raise OSError("no credentials")

    monkeypatch.setattr("fsspec.core.url_to_fs", raiser)

    with pytest.warns(RuntimeWarning, match="treating as changed"):
        first = _remote_path_fingerprint("memory://raw/a.dat")
    with pytest.warns(RuntimeWarning, match="treating as changed"):
        second = _remote_path_fingerprint("memory://raw/a.dat")

    assert first["kind"] == "remote-unverified"
    assert second["kind"] == "remote-unverified"
    assert first != second  # unique nonce -> guaranteed cache miss


def test_unverified_remote_changes_step_hash(
    memory_file: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unverifiable remote input yields a different hash than a verified one,
    and never repeats — so cached results are recomputed, not stale-hit."""
    dag = _path_input_dag(memory_file)
    verified = compute_step_hashes(dag, {"raw_dir": memory_file})

    def raiser(*args, **kwargs):
        raise OSError("no credentials")

    monkeypatch.setattr("fsspec.core.url_to_fs", raiser)
    with pytest.warns(RuntimeWarning):
        unverified_a = compute_step_hashes(dag, {"raw_dir": memory_file})
    with pytest.warns(RuntimeWarning):
        unverified_b = compute_step_hashes(dag, {"raw_dir": memory_file})

    assert unverified_a["probe"] != verified["probe"]
    assert unverified_a["probe"] != unverified_b["probe"]
