# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Step artifacts: where a step's checkpointed outputs are, per tier."""

from __future__ import annotations

import shutil
from pathlib import Path

import fsspec
import pytest

import _stage9_helpers as H
from aa_recipe_manager import api
from aa_recipe_manager.exceptions import UnknownStepError
from aa_recipe_manager.executor.checkpoint import entry_dir_parts

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"
USER_ROOT = "memory://artifacts/user"
OTHER_USER_ROOT = "memory://artifacts/other_user"
SURVEY_ROOT = "memory://artifacts/survey"


@pytest.fixture(autouse=True)
def clear_memory_fs():
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    yield fs
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]


def _run(recipe, cache, tmp_path, **kwargs):
    return api.execute(
        recipe, user_cache_dir=cache, outputs_dir=tmp_path / "outputs", **kwargs
    )


def _run_mapped(cache, tmp_path):
    """Run mapped.yaml to proc2 with its fan out source saved."""
    _run(
        FIXTURES / "mapped.yaml", cache, tmp_path,
        targets=["proc2"], checkpoint_steps=["seg"],
    )


def test_plain_step_in_user_tier(tmp_path):
    cache = tmp_path / "cache"
    _run(FIXTURES / "flat.yaml", cache, tmp_path, targets=["bump"])

    found = api.step_artifacts(FIXTURES / "flat.yaml", "bump", user_cache_dir=cache)

    assert found.held_by == ["user"]
    assert not found.fanned_out
    assert (found.found, found.instances, found.marker) == (1, None, False)
    [record] = found.artifacts
    assert (record.output, record.format, record.tier) == ("out", "json", "user")
    assert record.instance_index is None and record.item is None
    assert Path(record.url).is_absolute() and Path(record.url).exists()
    assert record.run_id and record.created_at
    hashes = api.step_hashes(FIXTURES / "flat.yaml")
    assert found.step_hash == record.step_hash == hashes["bump"].step_hash


def test_unheld_step_has_no_artifacts(tmp_path):
    found = api.step_artifacts(
        FIXTURES / "flat.yaml", "bump", user_cache_dir=tmp_path / "cache"
    )
    assert (found.held_by, found.artifacts, found.found) == ([], [], 0)
    assert found.note is None


def test_no_cache_configured():
    found = api.step_artifacts(FIXTURES / "flat.yaml", "bump")
    assert found.note == "no cache configured"
    assert found.step_hash == api.step_hashes(FIXTURES / "flat.yaml")["bump"].step_hash


def test_unknown_step_is_refused(tmp_path):
    with pytest.raises(UnknownStepError, match="bmp"):
        api.step_artifacts(FIXTURES / "flat.yaml", "bmp", user_cache_dir=tmp_path)


def test_mapped_step_lists_each_instance(tmp_path):
    cache = tmp_path / "cache"
    _run_mapped(cache, tmp_path)

    found = api.step_artifacts(FIXTURES / "mapped.yaml", "proc2", user_cache_dir=cache)
    assert found.fanned_out
    assert (found.instances, found.found, found.held_by) == (3, 3, ["user"])
    assert [r.instance_index for r in found.artifacts] == [0, 1, 2]
    assert [r.item for r in found.artifacts] == [10, 20, 30]
    assert len({r.step_hash for r in found.artifacts}) == 3
    assert found.step_hash not in {r.step_hash for r in found.artifacts}
    assert all(Path(r.url).exists() for r in found.artifacts)

    # proc ran but was not saved.
    proc = api.step_artifacts(FIXTURES / "mapped.yaml", "proc", user_cache_dir=cache)
    assert (proc.instances, proc.found, proc.held_by) == (3, 0, [])


def test_many_instances_are_read_from_a_listing(tmp_path):
    cache = tmp_path / "cache"
    _run_mapped(cache, tmp_path)
    probed = api.step_artifacts(
        FIXTURES / "mapped.yaml", "proc2", user_cache_dir=cache
    )
    listed = api.step_artifacts(
        FIXTURES / "mapped.yaml", "proc2", user_cache_dir=cache,
        instance_probe_limit=1,
    )
    assert listed.to_dict() == probed.to_dict()


def test_mapped_step_source_not_cached(tmp_path):
    found = api.step_artifacts(
        FIXTURES / "mapped.yaml", "proc2", user_cache_dir=tmp_path / "cache"
    )
    assert found.instances is None
    assert found.note == "instances are known once seg has run"
    assert (found.found, found.artifacts) == (0, [])


def test_partially_held_instances(tmp_path):
    cache = tmp_path / "cache"
    _run_mapped(cache, tmp_path)
    first = api.step_artifacts(FIXTURES / "mapped.yaml", "proc2", user_cache_dir=cache)
    step_dir, key = entry_dir_parts("proc2", first.artifacts[1].step_hash)
    shutil.rmtree(cache / step_dir / key)

    found = api.step_artifacts(FIXTURES / "mapped.yaml", "proc2", user_cache_dir=cache)
    assert (found.instances, found.found, found.held_by) == (3, 2, [])
    assert [r.instance_index for r in found.artifacts] == [0, 2]


def test_user_tier_wins_over_survey(tmp_path):
    recipe = FIXTURES / "flat.yaml"
    _run(recipe, USER_ROOT, tmp_path, targets=["bump"])
    _run(
        recipe, OTHER_USER_ROOT, tmp_path, targets=["bump"],
        survey_cache_dir=SURVEY_ROOT, cache_write_tier="survey",
    )

    both = api.step_artifacts(
        recipe, "bump", user_cache_dir=USER_ROOT, survey_cache_dir=SURVEY_ROOT
    )
    assert both.held_by == ["user", "survey"]
    assert [r.tier for r in both.artifacts] == ["user"]
    assert both.artifacts[0].url.startswith(USER_ROOT)

    survey_only = api.step_artifacts(
        recipe, "bump", user_cache_dir="memory://artifacts/empty",
        survey_cache_dir=SURVEY_ROOT,
    )
    assert survey_only.held_by == ["survey"]
    assert [r.tier for r in survey_only.artifacts] == ["survey"]
    assert survey_only.artifacts[0].url.startswith(SURVEY_ROOT)


def test_sink_marker(tmp_path):
    steps = [
        H.step("start", "const7", out_ports={"v": H.INT},
               output_map={"v": "__return__"}),
        H.step("note", "write_scratch", inputs={"x": "${start.v}"},
               in_ports={"x": H.INT}, sink=True),
    ]
    recipe = H.recipe(steps)
    cache = tmp_path / "cache"
    before = api.step_artifacts(recipe, "note", user_cache_dir=cache)
    assert (before.marker, before.found, before.held_by) == (False, 0, [])

    _run(recipe, cache, tmp_path)
    found = api.step_artifacts(recipe, "note", user_cache_dir=cache)
    assert (found.marker, found.found, found.held_by) == (True, 1, ["user"])
    assert found.artifacts == []


def test_base_dir_matches_running_from_the_folder(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.chdir(FIXTURES)
    _run(FIXTURES / "paths.yaml", cache, tmp_path, targets=["bump"])

    monkeypatch.chdir(tmp_path)
    found = api.step_artifacts(
        FIXTURES / "paths.yaml", "bump", user_cache_dir=cache, base_dir=FIXTURES
    )
    assert (found.held_by, found.found) == (["user"], 1)


def test_to_dict_is_json_ready(tmp_path):
    import json

    cache = tmp_path / "cache"
    _run_mapped(cache, tmp_path)
    found = api.step_artifacts(FIXTURES / "mapped.yaml", "proc2", user_cache_dir=cache)
    data = json.loads(json.dumps(found.to_dict()))
    assert set(data) == {
        "step_id", "step_hash", "fanned_out", "held_by", "instances", "found",
        "marker", "note", "artifacts",
    }
    assert set(data["artifacts"][0]) == {
        "output", "url", "format", "tier", "step_hash", "instance_index", "item",
        "created_at", "run_id", "size_bytes",
    }
    assert data["artifacts"][0]["size_bytes"] is None


def test_sv_dataset_is_zarr(tmp_path):
    pytest.importorskip("aa_si_echogram_gl")
    cache = tmp_path / "cache"
    _run(FIXTURES / "sv_mapped.yaml", cache, tmp_path)

    found = api.step_artifacts(FIXTURES / "sv_mapped.yaml", "sv", user_cache_dir=cache)
    assert (found.instances, found.found) == (3, 3)
    assert [r.format for r in found.artifacts] == ["zarr"] * 3
    assert [r.output for r in found.artifacts] == ["ds_Sv"] * 3

    from aa_si_echogram_gl import catalog

    if not hasattr(catalog, "resolve_artifacts"):
        pytest.skip("this EchogramGL has no resolve_artifacts")
    resolved = catalog.resolve_artifacts(
        [r.to_dict() for r in found.artifacts], found.fanned_out
    )
    assert resolved["status"] == "found"
    assert resolved["kind"] == "dataset"
    instances = resolved["instances"]
    assert [i["index"] for i in instances] == [0, 1, 2]
    assert all(a["end"] < b["start"] for a, b in zip(instances, instances[1:]))
