# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for executor/refs.py's shared ref resolution."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from aa_recipe_manager.exceptions import PipelineExecutionError
from aa_recipe_manager.executor.checkpoint import CheckpointManager
from aa_recipe_manager.executor.engine.tasks import TaskClosure
from aa_recipe_manager.executor.invocation import RuntimeContext
from aa_recipe_manager.executor.refs import (
    CheckpointRef,
    FoldedCheckpointRef,
    ValueRef,
    resolve_ref,
)


class TestResolveRef:
    def test_value_ref_returns_value_without_a_store(self):
        ref = ValueRef("s", "out", 42)
        assert resolve_ref(ref, store=None) == 42

    def test_checkpoint_ref_loads_from_store(self, tmp_path):
        manager = CheckpointManager(tmp_path / "ckpt", {"s": "hash"})
        manager.save("s", {"out": 7})
        assert resolve_ref(CheckpointRef("s", "out"), manager) == 7

    def test_checkpoint_ref_without_store_raises(self):
        with pytest.raises(PipelineExecutionError):
            resolve_ref(CheckpointRef("s", "out"), store=None)

    def test_folded_checkpoint_ref_reloads_and_refolds_in_order(self, tmp_path):
        manager = CheckpointManager(
            tmp_path / "ckpt", {"m": "hash"}
        )
        manager.save("m", {"out": 10}, instance_hash="h0")
        manager.save("m", {"out": 20}, instance_hash="h1")
        manager.save("m", {"out": 30}, instance_hash="h2")
        ref = FoldedCheckpointRef("m", "out", ("h0", "h1", "h2"))
        assert resolve_ref(ref, manager) == [10, 20, 30]

    def test_folded_checkpoint_ref_without_store_raises(self):
        ref = FoldedCheckpointRef("m", "out", ("h0",))
        with pytest.raises(PipelineExecutionError):
            resolve_ref(ref, store=None)


class TestSharedResolutionPath:
    """Guard against the two call sites drifting: TaskClosure.materialize()
    and RuntimeContext.get() must both route every ref through resolve_ref."""

    def test_materialize_routes_through_resolve_ref(self, tmp_path):
        manager = CheckpointManager(tmp_path / "ckpt", {"s": "hash"})
        manager.save("s", {"out": 5})
        closure = TaskClosure(refs={("s", "out"): CheckpointRef("s", "out")})
        with patch(
            "aa_recipe_manager.executor.engine.tasks.resolve_ref",
            wraps=resolve_ref,
        ) as spy:
            runtime = closure.materialize(manager)
        spy.assert_called_once()
        assert runtime.get("s", "out") == 5

    def test_runtime_context_get_routes_through_resolve_ref(self, tmp_path):
        manager = CheckpointManager(tmp_path / "ckpt", {"s": "hash"})
        manager.save("s", {"out": 9})
        runtime = RuntimeContext(store=manager)
        runtime.record("s", {"out": "placeholder"})
        runtime.evict("s", "out", CheckpointRef("s", "out"))
        with patch(
            "aa_recipe_manager.executor.invocation.resolve_ref", wraps=resolve_ref
        ) as spy:
            value = runtime.get("s", "out")
        spy.assert_called_once()
        assert value == 9

    def test_runtime_context_get_passes_plain_values_through_unchanged(self):
        runtime = RuntimeContext()
        runtime.record("s", {"out": object()})
        # Plain (non-ref) values must not go through resolve_ref at all.
        with patch(
            "aa_recipe_manager.executor.invocation.resolve_ref"
        ) as spy:
            runtime.get("s", "out")
        spy.assert_not_called()
