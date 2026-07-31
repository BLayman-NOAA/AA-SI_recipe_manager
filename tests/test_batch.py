# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Stage 9d: BatchExecutor and the input-set builders."""

from __future__ import annotations

import json

import pytest

import _stage9_helpers as H
from aa_recipe_manager.executor import (
    BatchExecutor,
    InputSet,
    SequentialExecutor,
    input_sets_from_csv,
    input_sets_from_folder,
    input_sets_from_lists,
)
from aa_recipe_manager.executor.batch import BATCH_MANIFEST_FILENAME
from aa_recipe_manager.model.types import InputDeclaration, ParamDeclaration


def _addk_dag_factory():
    """base=const7 (shared) -> out=addk(base, k) (per-set)."""
    steps = [
        H.step("base", "const7", out_ports={"v": H.INT},
               output_map={"v": "__return__"}),
        H.step("out", "addk", inputs={"x": "${base.v}"}, params={"k": "${inputs.k}"},
               in_ports={"x": H.INT}, out_ports={"y": H.INT},
               output_map={"y": "__return__"},
               param_decls={"k": ParamDeclaration(type="int")}),
    ]

    def factory(inputs):
        return H.build(
            steps,
            inputs_decl={"k": InputDeclaration(type="int", default=0)},
            input_values=inputs,
        )

    return factory


def test_batch_runs_each_set_with_own_outputs_and_manifest(tmp_path):
    sets = [InputSet(label=f"k{k}", inputs={"k": k}) for k in (1, 2, 3)]
    batch = BatchExecutor(SequentialExecutor()).execute_batch(
        _addk_dag_factory(),
        sets,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "outputs"),
        checkpoint_mode="eager",
    )
    assert [r.outputs["out"]["y"] for r in batch.results] == [8, 9, 10]
    assert len(batch) == 3

    outputs = tmp_path / "outputs"
    for label in ("k1", "k2", "k3"):
        assert (outputs / label).is_dir()
    manifest = json.loads((outputs / BATCH_MANIFEST_FILENAME).read_text())
    assert [r["label"] for r in manifest["runs"]] == ["k1", "k2", "k3"]


def test_shared_upstream_computed_once_across_sets(tmp_path):
    # The input-independent 'base' step has the same hash for every set, so it
    # is computed once and reused from the shared cache by later sets.
    sets = [InputSet(label=f"k{k}", inputs={"k": k}) for k in (1, 2, 3)]
    batch = BatchExecutor(SequentialExecutor()).execute_batch(
        _addk_dag_factory(),
        sets,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "outputs"),
        checkpoint_mode="eager",
    )
    # First set computes base; the rest hit the shared cache for it.
    assert "base" in batch.results[0].executed_steps
    assert "base" in batch.results[1].skipped_steps
    assert "base" in batch.results[2].skipped_steps
    # Every set still recomputes its own input-dependent tail.
    for r in batch.results:
        assert "out" in r.executed_steps


def test_duplicate_labels_rejected(tmp_path):
    sets = [
        InputSet(label="dup", inputs={"k": 1}),
        InputSet(label="dup", inputs={"k": 2}),
    ]
    with pytest.raises(ValueError, match="labels must be unique"):
        BatchExecutor(SequentialExecutor()).execute_batch(
            _addk_dag_factory(), sets, user_cache_dir=str(tmp_path / "cache")
        )


def test_empty_input_sets_rejected(tmp_path):
    with pytest.raises(ValueError, match="at least one input set"):
        BatchExecutor(SequentialExecutor()).execute_batch(
            _addk_dag_factory(), [], user_cache_dir=str(tmp_path / "cache")
        )


# --- input-set builders -----------------------------------------------------


def test_input_sets_from_folder(tmp_path):
    for name in ("b.raw", "a.raw", "notes.txt"):
        (tmp_path / name).write_text("x")
    sets = input_sets_from_folder(tmp_path, "raw_file", pattern="*.raw")
    assert [s.label for s in sets] == ["a", "b"]  # sorted, .txt excluded
    assert sets[0].inputs["raw_file"].endswith("a.raw")


def test_input_sets_from_folder_empty_errors(tmp_path):
    with pytest.raises(ValueError, match="no files matching"):
        input_sets_from_folder(tmp_path, "raw_file", pattern="*.raw")


def test_input_sets_from_csv(tmp_path):
    csv_path = tmp_path / "manifest.csv"
    csv_path.write_text("label,k\nlow,1\nhigh,9\n", encoding="utf-8")
    sets = input_sets_from_csv(csv_path)
    assert [s.label for s in sets] == ["low", "high"]
    assert sets[1].inputs == {"k": "9"}


def test_input_sets_from_lists():
    sets = input_sets_from_lists("cruise", ["HB1", "HB2"], labels=["one", "two"])
    assert [s.label for s in sets] == ["one", "two"]
    assert sets[0].inputs == {"cruise": "HB1"}
