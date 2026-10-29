# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Where a session's step outputs are saved, as the artifacts endpoint reports it."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.server.echogram import EchogramService

from .conftest import FIXTURES, HEADERS, run_session, start, wait_for

ENDED = {"completed", "failed", "cancelled", "interrupted"}
SERVED = EchogramService().available


def test_echogram_gl_is_installed_in_ci():
    """CI installs the echogram extra, so the assertions below cannot adapt away."""
    if not os.environ.get("CI"):
        pytest.skip("only CI must have EchogramGL")
    service = EchogramService()
    assert service.available, f"EchogramGL is required in CI: {service.reason}"


def artifacts(client, sid: str, step: str, **params):
    return client.get(f"/api/sessions/{sid}/steps/{step}/artifacts", params=params)


def test_a_run_to_a_step_saves_it(run_client, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    run = wait_for(
        run_client, start(run_client, sid, mode="to", targets=["bump"])["id"], ENDED
    )
    assert run["status"] == "completed"

    response = artifacts(run_client, sid, "bump")
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["held_by"] == ["user"]
    assert (found["found"], found["fanned_out"], found["marker"]) == (1, False, False)
    preview = run_client.get(f"/api/sessions/{sid}/preview").json()
    hashes = {step["step_id"]: step["step_hash"] for step in preview["steps"]}
    assert found["step_hash"] == hashes["bump"]

    [record] = found["artifacts"]
    assert [record[k] for k in ("output", "format", "tier")] == ["out", "json", "user"]
    assert record["instance_index"] is None and record["size_bytes"] is None
    assert Path(record["url"]).is_file()
    assert Path(record["url"]).is_relative_to(tmp_path / "cache")

    echogram = found["echogram"]
    assert echogram["available"] is SERVED
    if SERVED:
        # Found, but a json value is nothing to draw; the outputs say why.
        resolution = echogram["resolution"]
        assert resolution["status"] == "found" and resolution["kind"] is None
        assert resolution["outputs"][0]["reason"]
    else:
        assert echogram["resolution"] is None


def test_nothing_saved_is_found_zero(client, tmp_path):
    sid = run_session(client, "flat.yaml", tmp_path / "cache")
    found = artifacts(client, sid, "bump").json()
    assert (found["found"], found["held_by"], found["artifacts"]) == (0, [], [])
    assert found["echogram"]["resolution"] is None
    assert found["echogram"]["available"] is SERVED


def test_unknown_step_and_session(client, tmp_path):
    sid = run_session(client, "flat.yaml", tmp_path / "cache")
    response = artifacts(client, sid, "bmp")
    assert response.status_code == 404
    body = response.json()
    assert body["error"] == "StepNotFound" and body["step_id"] == "bmp"
    assert "bump" in body["message"]

    response = artifacts(client, "20260101-000000-abcdef", "bump")
    assert response.status_code == 404
    assert response.json()["error"] == "SessionNotFound"


def test_mapped_step_lists_each_instance_in_order(client, tmp_path):
    cache = tmp_path / "cache"
    api.execute(
        FIXTURES / "mapped.yaml",
        user_cache_dir=cache,
        outputs_dir=tmp_path / "outputs",
        targets=["proc2"],
        checkpoint_steps=["seg"],
    )
    sid = run_session(client, "mapped.yaml", cache)

    found = artifacts(client, sid, "proc2").json()
    assert found["fanned_out"] and found["held_by"] == ["user"]
    assert (found["instances"], found["found"]) == (3, 3)
    records = found["artifacts"]
    assert [r["instance_index"] for r in records] == [0, 1, 2]
    assert [r["item"] for r in records] == [10, 20, 30]
    assert len({r["step_hash"] for r in records}) == 3

    proc = artifacts(client, sid, "proc").json()
    assert (proc["instances"], proc["found"], proc["held_by"]) == (3, 0, [])


def test_mapped_step_whose_source_is_not_saved(client, tmp_path):
    sid = run_session(client, "mapped.yaml", tmp_path / "cache")
    found = artifacts(client, sid, "proc2").json()
    assert found["instances"] is None and found["artifacts"] == []
    assert "seg" in found["note"]


def test_relative_cache_dirs_resolve_beside_the_recipe(
    client, config, tmp_path, monkeypatch
):
    project = tmp_path / "recipes" / "project"
    project.mkdir(parents=True)
    shutil.copy(FIXTURES / "flat.yaml", project / "flat.yaml")
    (project / "flat.config.yaml").write_text(
        "user_cache_dir: ./cache\n", encoding="utf-8"
    )
    config.recipe_roots.append(tmp_path / "recipes")
    api.execute(
        project / "flat.yaml",
        user_cache_dir=project / "cache",
        outputs_dir=tmp_path / "outputs",
        targets=["bump"],
    )
    response = client.post(
        "/api/sessions",
        json={"path": (project / "flat.yaml").as_posix()},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    sid = response.json()["id"]

    found = artifacts(client, sid, "bump").json()
    assert found["held_by"] == ["user"]
    assert Path(found["artifacts"][0]["url"]).is_relative_to(project / "cache")
