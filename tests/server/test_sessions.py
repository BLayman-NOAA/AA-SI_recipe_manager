# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Opening, reading, changing and closing sessions."""

from __future__ import annotations

import json
import shutil

from fastapi.testclient import TestClient

from aa_recipe_manager.server.app import create_app

from .conftest import BASE_URL, FIXTURES, HEADERS, open_session


def session_record(config, sid: str) -> dict:
    path = config.state_dir / "sessions" / sid / "session.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_open_with_sub(client, config):
    session = open_session(client, "with_sub.yaml")
    assert session["title"] == "with_sub"
    assert session["read_only"] is True
    assert session["version"] == 0
    assert session["origin"] == (FIXTURES / "with_sub.yaml").resolve().as_posix()

    graph = session["graph"]
    assert graph["schema"] == 2
    includes = {include["id"]: include for include in graph["includes"]}
    assert set(includes) == {"inc:sub/chain.yaml", "inc:sub/leaf.yaml"}
    assert includes["inc:sub/leaf.yaml"]["parent"] == "inc:sub/chain.yaml"

    files = [ref["path"] for ref in session["files"]]
    assert files == ["with_sub.yaml", "sub/leaf.yaml", "sub/chain.yaml"]
    assert all(ref["editable"] is False for ref in session["files"])
    assert session["files"][1]["origin"].endswith("/sub/leaf.yaml")

    assert session["validation"] == {"valid": True, "problems": []}

    declared = {field["name"]: field for field in session["inputs"]["declared"]}
    assert declared["n"]["default"] == 3
    assert declared["n"]["source"] == "default"
    assert session["inputs"]["values"]["n"] == 3

    record = session_record(config, session["id"])
    assert record["schema"] == 1
    assert record["files"] == files
    assert record["inputs"] == {} and record["run_config"] == {}


def test_list_and_reload_after_restart(client, config):
    first = open_session(client, "with_sub.yaml")
    second = open_session(client, "flat.yaml")
    listed = client.get("/api/sessions").json()
    assert [item["id"] for item in listed] == sorted(
        [first["id"], second["id"]], reverse=True
    )
    assert {item["title"] for item in listed} == {"with_sub", "flat"}

    restarted = TestClient(create_app(config), base_url=BASE_URL)
    response = restarted.get(f"/api/sessions/{first['id']}")
    assert response.status_code == 200
    assert response.json()["graph"] == first["graph"]
    assert response.json()["validation"] == first["validation"]


def test_file_text(client):
    sid = open_session(client, "with_sub.yaml")["id"]
    response = client.get(f"/api/sessions/{sid}/files/sub/chain.yaml")
    assert response.status_code == 200
    assert response.json()["path"] == "sub/chain.yaml"
    assert "client_chain" in response.json()["text"]

    root = client.get(f"/api/sessions/{sid}/files/with_sub.yaml")
    assert root.status_code == 200


def test_file_outside_the_session_is_refused(client):
    sid = open_session(client, "with_sub.yaml")["id"]
    response = client.get(f"/api/sessions/{sid}/files/..%2F..%2Fx")
    assert response.status_code == 403
    assert response.json()["error"] == "PathNotAllowed"
    response = client.get(f"/api/sessions/{sid}/files/flat.yaml")
    assert response.status_code == 403


def test_set_inputs(client, config):
    sid = open_session(client, "with_sub.yaml")["id"]
    response = client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"n": 2}}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    session = response.json()
    declared = {field["name"]: field for field in session["inputs"]["declared"]}
    assert declared["n"]["source"] == "session"
    assert declared["n"]["value"] == 2
    assert session["inputs"]["values"]["n"] == 2
    assert session_record(config, sid)["inputs"] == {"n": 2}

    response = client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {}}, headers=HEADERS
    )
    assert response.json()["inputs"]["values"]["n"] == 3


def test_set_unknown_input_is_400(client):
    sid = open_session(client, "with_sub.yaml")["id"]
    response = client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"nope": 1}}, headers=HEADERS
    )
    assert response.status_code == 400
    assert response.json()["error"] == "ValueError"


def test_config_file_inputs_show_as_config(client, tmp_path, config):
    run_config = tmp_path / "run.config.yaml"
    run_config.write_text("inputs: {factor: 4}\n", encoding="utf-8")
    config.run_config = run_config
    session = open_session(client, "flat.yaml")
    (factor,) = session["inputs"]["declared"]
    assert (factor["source"], factor["value"]) == ("config", 4)
    assert session["run_config"]["source"] == run_config.as_posix()


def test_set_run_config(client, config):
    sid = open_session(client, "flat.yaml")["id"]
    response = client.put(
        f"/api/sessions/{sid}/config", json={"executor": "dask"}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    run_config = response.json()["run_config"]
    assert run_config["executor"] == "dask"
    assert run_config["overrides"] == {"executor": "dask"}
    assert session_record(config, sid)["run_config"] == {"executor": "dask"}

    response = client.put(
        f"/api/sessions/{sid}/config", json={"executor": "fast"}, headers=HEADERS
    )
    assert response.status_code == 422
    assert response.json()["error"] == "invalid_request"


def test_unknown_request_fields_are_refused(client):
    response = client.post(
        "/api/sessions",
        json={"path": (FIXTURES / "flat.yaml").as_posix(), "extra": 1},
        headers=HEADERS,
    )
    assert response.status_code == 422


def test_delete(client, config):
    sid = open_session(client, "flat.yaml")["id"]
    response = client.delete(f"/api/sessions/{sid}", headers=HEADERS)
    assert response.status_code == 204
    assert not (config.state_dir / "sessions" / sid).exists()
    assert client.get(f"/api/sessions/{sid}").status_code == 404
    assert client.delete(f"/api/sessions/{sid}", headers=HEADERS).status_code == 404


def test_unknown_session_is_404(client):
    response = client.get("/api/sessions/20260101-000000-abcdef")
    assert response.status_code == 404
    assert response.json()["error"] == "SessionNotFound"
    assert client.get("/api/sessions/..%2F..%2Fetc").status_code == 404


def test_open_outside_the_roots_is_403(client, tmp_path):
    outside = tmp_path / "flat.yaml"
    outside.write_text((FIXTURES / "flat.yaml").read_text(encoding="utf-8"))
    response = client.post(
        "/api/sessions", json={"path": outside.as_posix()}, headers=HEADERS
    )
    assert response.status_code == 403
    assert response.json()["error"] == "PathNotAllowed"


def test_open_missing_file_is_404(client):
    path = (FIXTURES / "missing.yaml").as_posix()
    response = client.post("/api/sessions", json={"path": path}, headers=HEADERS)
    assert response.status_code == 404
    assert response.json()["error"] == "RecipeNotFound"


def test_open_relative_to_a_root(client):
    response = client.post(
        "/api/sessions", json={"path": "sub/chain.yaml"}, headers=HEADERS
    )
    assert response.status_code == 201
    assert response.json()["origin"].endswith("/sub/chain.yaml")


def test_open_yaml_syntax_error_is_422(client, config, tmp_path):
    root = tmp_path / "recipes"
    root.mkdir()
    (root / "broken.yaml").write_text("recipe: [unclosed\nsteps: []\n")
    config.recipe_roots.append(root)
    response = client.post(
        "/api/sessions",
        json={"path": (root / "broken.yaml").as_posix()},
        headers=HEADERS,
    )
    assert response.status_code == 422
    assert response.json()["error"] == "RecipeParseError"
    assert not (config.state_dir / "sessions").exists() or not any(
        (config.state_dir / "sessions").iterdir()
    )


def recipe_root_with_flat(config, tmp_path):
    """A new recipe root holding a copy of flat.yaml."""
    root = tmp_path / "recipes"
    root.mkdir()
    (root / "flat.yaml").write_text((FIXTURES / "flat.yaml").read_text())
    config.recipe_roots.append(root)
    return root


def open_flat(client, root):
    path = (root / "flat.yaml").as_posix()
    response = client.post("/api/sessions", json={"path": path}, headers=HEADERS)
    assert response.status_code == 201, response.text
    return response.json()


def test_folder_config_beats_home_config(client, config, tmp_path):
    root = recipe_root_with_flat(config, tmp_path)
    folder_config = root / "aa-recipe.config.yaml"
    folder_config.write_text("executor: dask\ninputs: {factor: 5}\n")
    home_config = tmp_path / "home" / ".config" / "aa-recipe" / "config.yaml"
    home_config.parent.mkdir(parents=True)
    home_config.write_text("executor: prefect\n")
    session = open_flat(client, root)
    assert session["run_config"]["executor"] == "dask"
    assert session["run_config"]["source"] == folder_config.as_posix()
    assert session["inputs"]["values"]["factor"] == 5


def assert_config_problem(client, session, text):
    assert session["validation"]["valid"] is False
    messages = [p["message"] for p in session["validation"]["problems"]]
    assert any(m.startswith("Run config: ") and text in m for m in messages)
    assert session["run_config"]["source"] is None
    reloaded = client.get(f"/api/sessions/{session['id']}")
    assert reloaded.status_code == 200
    assert reloaded.json()["validation"] == session["validation"]


def test_missing_explicit_config_is_a_problem(client, config, tmp_path):
    config.run_config = tmp_path / "gone.yaml"
    session = open_session(client, "flat.yaml")
    assert_config_problem(client, session, "gone.yaml")


def test_missing_environment_config_is_a_problem(client, monkeypatch, tmp_path):
    monkeypatch.setenv("AA_RECIPE_CONFIG", str(tmp_path / "gone.yaml"))
    session = open_session(client, "flat.yaml")
    assert_config_problem(client, session, "AA_RECIPE_CONFIG")


def test_malformed_folder_config_is_a_problem(client, config, tmp_path):
    root = recipe_root_with_flat(config, tmp_path)
    (root / "aa-recipe.config.yaml").write_text("colour: blue\n")
    session = open_flat(client, root)
    assert_config_problem(client, session, "unknown key")
    listed = [item["id"] for item in client.get("/api/sessions").json()]
    assert listed == [session["id"]]


def test_include_under_another_recipe_root(client, config, tmp_path):
    main, shared = tmp_path / "main", tmp_path / "shared"
    shutil.copytree(FIXTURES / "sub", shared)
    main.mkdir()
    text = (FIXTURES / "with_sub.yaml").read_text()
    text = text.replace("sub/chain.yaml", "../shared/chain.yaml")
    (main / "top.yaml").write_text(text)
    config.recipe_roots.append(main)
    session = open_session_at(client, main / "top.yaml")
    assert "../shared/chain.yaml" in [ref["path"] for ref in session["files"]]
    url = f"/api/sessions/{session['id']}/files/..%2Fshared%2Fchain.yaml"
    assert client.get(url).status_code == 403

    config.recipe_roots.append(shared)
    response = client.get(url)
    assert response.status_code == 200
    assert "client_chain" in response.json()["text"]


def open_session_at(client, path):
    response = client.post(
        "/api/sessions", json={"path": path.as_posix()}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    return response.json()
