# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""EchogramGL mounted at /echogram: saved Sv drawn as pieces, and its absence."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aa_recipe_manager import api
from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.echogram import INSTALL_HINT, EchogramService

from .conftest import BASE_URL, FIXTURES, run_session

if os.environ.get("CI"):
    # CI installs the echogram extra; these tests must not skip there quietly.
    SERVICE = EchogramService()
    assert SERVICE.available, f"EchogramGL is required in CI: {SERVICE.reason}"
else:
    pytest.importorskip("aa_si_echogram_gl.server")


def execute(tmp_path, name: str) -> Path:
    """Run a fixture recipe into a user cache under ``tmp_path``; return it."""
    cache = tmp_path / "cache"
    api.execute(FIXTURES / name, user_cache_dir=cache, outputs_dir=tmp_path / "out")
    return cache


def saved(client, tmp_path, name: str) -> str:
    """Run a fixture recipe into a user cache and open a session over it."""
    return run_session(client, name, execute(tmp_path, name))


def artifacts(client, sid: str, step: str = "sv", **params):
    return client.get(f"/api/sessions/{sid}/steps/{step}/artifacts", params=params)


@pytest.fixture
def mapped(client, tmp_path):
    sid = saved(client, tmp_path, "sv_mapped.yaml")
    response = artifacts(client, sid)
    assert response.status_code == 200, response.text
    return sid, response.json()


def test_mapped_sv_is_mounted_as_pieces(mapped):
    _, found = mapped
    echogram = found["echogram"]
    assert echogram["available"] is True and echogram["reason"] is None
    resolution = echogram["resolution"]
    assert resolution["mode"] == "artifacts" and resolution["status"] == "found"
    assert resolution["step"] == "sv" and resolution["stepHash"] == found["step_hash"]
    assert resolution["kind"] == "dataset" and resolution["fannedOut"] is True
    assert resolution["var"] == "Sv" and resolution["entries"] == 3
    instances = resolution["instances"]
    assert [i["index"] for i in instances] == [0, 1, 2]
    assert len({i["mount"] for i in instances}) == 3
    assert all(a["end"] < b["start"] for a, b in zip(instances, instances[1:]))


def test_a_mount_serves_its_header_and_chunks(client, mapped):
    _, found = mapped
    mount = found["echogram"]["resolution"]["instances"][0]["mount"]

    header = client.get(f"/echogram/api/describe/{mount}")
    assert header.status_code == 200
    assert header.json()["kind"] == "mvbs" and header.json()["chunks"]

    meta = client.get(f"/echogram/mount/{mount}/Sv/.zarray")
    assert meta.status_code == 200
    chunk = client.get(f"/echogram/mount/{mount}/Sv/0.0.0")
    assert chunk.status_code == 200 and chunk.content
    assert chunk.headers["cache-control"] == "no-store"
    assert chunk.headers["content-type"] == "application/octet-stream"


def test_mounts_are_stable_across_requests(client, mapped):
    sid, found = mapped
    again = artifacts(client, sid).json()["echogram"]["resolution"]
    mounts = [i["mount"] for i in found["echogram"]["resolution"]["instances"]]
    assert [i["mount"] for i in again["instances"]] == mounts


def test_listing_without_resolving(client, mapped, monkeypatch):
    sid, found = mapped
    service = client.app.state.echogram

    def resolving(*args, **kwargs):
        raise AssertionError("resolve=false must not resolve or mount")

    monkeypatch.setattr(service, "resolve", resolving)
    listed = artifacts(client, sid, resolve="false")
    assert listed.status_code == 200, listed.text
    listed = listed.json()
    assert listed["artifacts"] == found["artifacts"]
    assert listed["found"] == found["found"]
    assert listed["echogram"] == {"available": True, "reason": None, "resolution": None}


def test_choosing_output_and_variable(client, mapped):
    sid, found = mapped
    chosen = artifacts(client, sid, output="ds_Sv", var="Sv").json()
    assert chosen["echogram"] == found["echogram"]

    response = artifacts(client, sid, var="nothing")
    assert response.status_code == 400
    assert response.json()["error"] == "CatalogError"
    assert artifacts(client, sid, output="nothing").status_code == 400
    assert artifacts(client, sid, start="not a time").status_code == 400


def test_a_window_keeps_the_pieces_inside_it(client, mapped):
    sid, found = mapped
    second = found["echogram"]["resolution"]["instances"][1]
    window = {"start": str(second["start"]), "end": str(second["end"])}
    resolution = artifacts(client, sid, **window).json()["echogram"]["resolution"]
    assert [i["index"] for i in resolution["instances"]] == [1]


def test_echogram_errors_use_the_client_error_body(client, mapped):
    response = client.get("/echogram/mount/0123456789abcdef/Sv/.zarray")
    assert response.status_code == 410
    assert response.json()["error"] == "gone"
    assert "resolve the step again" in response.json()["message"]

    # An unknown describe mount is gone too, so the client resolves the step again.
    response = client.get("/echogram/api/describe/0123456789abcdef")
    assert response.status_code == 410
    assert response.json()["error"] == "gone"


def test_open_reads_only_under_session_folders(client, mapped, tmp_path):
    _, found = mapped
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    response = client.get("/echogram/api/open", params={"path": outside.as_posix()})
    assert response.status_code == 403

    store = found["artifacts"][0]["url"]
    response = client.get("/echogram/api/open", params={"path": store})
    assert response.status_code == 200, response.text
    assert response.json()["kind"] == "dataset"


def test_a_plain_step_is_one_instance(client, tmp_path):
    sid = saved(client, tmp_path, "sv.yaml")
    found = artifacts(client, sid).json()
    assert found["fanned_out"] is False and found["found"] == 1
    resolution = found["echogram"]["resolution"]
    assert resolution["kind"] == "dataset" and resolution["entries"] == 1
    assert len(resolution["instances"]) == 1


def test_an_unreadable_store_is_reported_not_raised(client, tmp_path):
    sid = saved(client, tmp_path, "sv.yaml")
    [record] = api.step_artifacts(
        FIXTURES / "sv.yaml", "sv", user_cache_dir=tmp_path / "cache"
    ).artifacts
    for path in Path(record.url).iterdir():
        if path.is_file():
            path.write_text("not metadata", encoding="utf-8")
    response = artifacts(client, sid)
    assert response.status_code == 200, response.text
    resolution = response.json()["echogram"]["resolution"]
    assert resolution["kind"] is None and resolution["outputs"][0]["reason"]


def test_a_resolver_failure_is_reported_not_raised(client, tmp_path, monkeypatch):
    sid = saved(client, tmp_path, "sv.yaml")

    def broken(*args, **kwargs):
        raise OSError("bucket unreachable")

    service = client.app.state.echogram
    monkeypatch.setattr(service._catalog, "resolve_artifacts", broken)
    response = artifacts(client, sid)
    assert response.status_code == 200, response.text
    found = response.json()
    assert found["found"] == 1
    assert found["echogram"] == {
        "available": True,
        "reason": "OSError: bucket unreachable",
        "resolution": None,
    }


def test_without_echogram_gl(config, tmp_path, monkeypatch):
    cache = execute(tmp_path, "sv.yaml")
    monkeypatch.setitem(sys.modules, "aa_si_echogram_gl", None)
    app = create_app(config)
    assert not any(
        getattr(route, "path", "").startswith("/echogram") for route in app.routes
    )
    client = TestClient(app, base_url=BASE_URL)
    sid = run_session(client, "sv.yaml", cache)
    found = artifacts(client, sid).json()
    assert found["found"] == 1
    assert found["echogram"] == {
        "available": False,
        "reason": INSTALL_HINT,
        "resolution": None,
    }
    assert client.get("/echogram/api/describe/x").status_code == 404
