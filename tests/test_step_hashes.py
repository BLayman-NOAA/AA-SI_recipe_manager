# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for api.step_hashes and api.fingerprint_differences."""

from __future__ import annotations

import json

from aa_recipe_manager import api
from aa_recipe_manager.executor import SequentialExecutor, compute_step_hashes
from aa_recipe_manager.parallel import derive_instance_hash
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.registry.registry import Registry

from test_parallel import (  # noqa: F401  (helper scaffolding)
    _INT,
    _MANY,
    _map_pipeline,
    _recipe,
    _step,
    helpers,
)


def _pipeline():
    collector = _step(
        "merge", "collect_sum", inputs={"values": "${proc.out}"},
        in_ports={"values": _MANY}, out_ports={"total": _INT},
        output_map={"total": "__return__"}, collect="${proc.out}",
    )
    return _recipe(_map_pipeline(collector))


def _metas(root, step_id):
    return [
        json.loads(path.read_text())
        for path in sorted((root / step_id).glob("*/meta.json"))
    ]


def test_hashes_match_what_a_run_computes(helpers):
    recipe = _pipeline()
    expected = compute_step_hashes(build_dag(recipe, Registry(), check_versions=False))
    found = api.step_hashes(recipe)
    assert {step: found[step].step_hash for step in found} == expected
    assert list(found) == ["seg", "proc", "merge"]


def test_mapped_step_is_fanned_out(helpers):
    found = api.step_hashes(_pipeline())
    assert found["proc"].fanned_out
    assert not found["seg"].fanned_out
    assert not found["merge"].fanned_out


def test_instances_carry_the_step_payload(helpers, tmp_path):
    recipe = _pipeline()
    cache = tmp_path / "cache"
    SequentialExecutor().execute(
        build_dag(recipe, Registry(), check_versions=False),
        user_cache_dir=cache,
        checkpoint_mode="eager",
    )
    proc = api.step_hashes(recipe)["proc"]
    metas = _metas(cache, "proc")
    assert len(metas) == 3
    for meta in metas:
        assert meta["fingerprint_payload"] == proc.payload
        disc = meta["instance_discriminator"]
        assert meta["step_hash"] == derive_instance_hash(proc.step_hash, disc)


def test_unmapped_step_entry_is_at_its_hash(helpers, tmp_path):
    recipe = _pipeline()
    cache = tmp_path / "cache"
    SequentialExecutor().execute(
        build_dag(recipe, Registry(), check_versions=False),
        user_cache_dir=cache,
        checkpoint_mode="eager",
    )
    merge = api.step_hashes(recipe)["merge"]
    [meta] = _metas(cache, "merge")
    assert meta["step_hash"] == merge.step_hash


def test_differences_name_the_changed_param(helpers):
    stored = {"fingerprint": {"params": {"factor": 2}}, "epoch": None}
    current = {"fingerprint": {"params": {"factor": 5}}, "epoch": None}
    assert api.fingerprint_differences(stored, current) == [
        {"path": "fingerprint.params.factor", "stored": 2, "current": 5}
    ]
    assert api.fingerprint_differences(stored, stored) == []
