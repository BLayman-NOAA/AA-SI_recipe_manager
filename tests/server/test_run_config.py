# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The session's run config file: contents, sources, choose, create and save."""

from __future__ import annotations

import shutil

from fastapi.testclient import TestClient

from aa_recipe_manager import api
from aa_recipe_manager.server.app import create_app

from .conftest import BASE_URL, FIXTURES, HEADERS
from .test_preview import cli_hashes, hashes

NEEDS = """\
recipe:
  name: needs_folder
  version: "1.0"
  schema_version: "1"

inputs:
  raw_folder:
    type: path
    description: Folder of raw files.
  factor:
    type: int
    default: 3

steps:
  - id: start
    op: custom
    params:
      folder: ${inputs.raw_folder}
    custom_spec:
      description: Count the entries in a folder.
      callable_path: _stage9_helpers.count_entries
      params:
        folder: {type: path, fingerprint_mode: size}
      outputs:
        n: {type: int}
      output_map: {n: __return__}
      dependency: {name: pytest, version: ">=7.0", source: pypi}
"""

FLAT_CONFIG = """\
# Settings for flat.yaml.
#
# The cache is kept short on purpose.
user_cache_dir: ./cache  # beside the recipe
executor: sequential

# Inputs the runs get.
inputs:
  factor: 4  # four, not three
  colour: blue
"""


def recipe_root(config, tmp_path, *names):
    """A new recipe root holding copies of fixture recipes."""
    root = tmp_path / "recipes"
    root.mkdir()
    for name in names:
        shutil.copy(FIXTURES / name, root / name)
    config.recipe_roots.append(root)
    return root


def open_recipe(client, path):
    response = client.post(
        "/api/sessions", json={"path": path.as_posix()}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    return response.json()


def fields(session):
    return {field["name"]: field for field in session["inputs"]["declared"]}


def choose(client, sid, path, **body):
    return client.put(
        f"/api/sessions/{sid}/config-file",
        json={"path": path, **body},
        headers=HEADERS,
    )


def create(client, sid, **body):
    return client.post(f"/api/sessions/{sid}/config-file", json=body, headers=HEADERS)


def save(client, sid, **body):
    return client.post(
        f"/api/sessions/{sid}/config-file/save", json=body, headers=HEADERS
    )


def test_no_config(client, config, tmp_path):
    root = recipe_root(config, tmp_path)
    (root / "needs.yaml").write_text(NEEDS, encoding="utf-8")
    session = open_recipe(client, root / "needs.yaml")
    run_config = session["run_config"]
    assert run_config["path"] is None and run_config["source"] is None
    assert run_config["contents"] is None and run_config["chosen"] is None
    assert run_config["shared_with"] == [] and run_config["unused_inputs"] == []
    assert run_config["candidate"] == (root / "needs.config.yaml").as_posix()
    sources = {name: field["source"] for name, field in fields(session).items()}
    assert sources == {"raw_folder": "missing", "factor": "default"}
    assert session["validation"]["valid"] is False


def test_per_recipe_config_contents_and_sources(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    shutil.copy(root / "flat.yaml", root / "other.yaml")
    path = root / "flat.config.yaml"
    path.write_text(FLAT_CONFIG, encoding="utf-8")
    session = open_recipe(client, root / "flat.yaml")
    run_config = session["run_config"]
    assert run_config["path"] == path.as_posix()
    assert run_config["chosen"] is None
    assert run_config["contents"] == {
        "inputs": {"factor": 4, "colour": "blue"},
        "settings": {"user_cache_dir": "./cache", "executor": "sequential"},
    }
    assert run_config["unused_inputs"] == ["colour"]
    assert run_config["shared_with"] == []
    assert fields(session)["factor"]["source"] == "config"

    response = client.put(
        f"/api/sessions/{session['id']}/inputs",
        json={"values": {"factor": 5}},
        headers=HEADERS,
    )
    factor = fields(response.json())["factor"]
    assert (factor["source"], factor["value"]) == ("session", 5)
    assert response.json()["run_config"]["contents"]["inputs"]["factor"] == 4


def test_shared_folder_config(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    shutil.copy(root / "flat.yaml", root / "other.yaml")
    shutil.copy(root / "flat.yaml", root / "own.yaml")
    (root / "own.config.yaml").write_text("executor: dask\n", encoding="utf-8")
    (root / "notes.yaml").write_text("a: 1\n", encoding="utf-8")
    (root / "sub").mkdir()
    shutil.copy(root / "flat.yaml", root / "sub" / "deeper.yaml")
    shared = root / "aa-recipe.config.yaml"
    shared.write_text("inputs:\n  factor: 6\n", encoding="utf-8")

    session = open_recipe(client, root / "flat.yaml")
    run_config = session["run_config"]
    assert run_config["path"] == shared.as_posix()
    assert run_config["shared_with"] == [(root / "other.yaml").as_posix()]
    assert run_config["contents"] == {"inputs": {"factor": 6}, "settings": {}}
    factor = fields(session)["factor"]
    assert (factor["source"], factor["value"]) == ("config", 6)


def test_saved_config_keeps_comments_and_hashes_as_the_cli(
    client, config, tmp_path, monkeypatch
):
    root = recipe_root(config, tmp_path, "flat.yaml")
    path = root / "flat.config.yaml"
    path.write_bytes(FLAT_CONFIG.replace("\n", "\r\n").encode("utf-8"))
    session = open_recipe(client, root / "flat.yaml")
    sid, version = session["id"], session["version"]

    response = save(
        client,
        sid,
        inputs={"factor": 6, "colour": "blue"},
        settings={"user_cache_dir": "./other_cache"},
        version=version,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["shared_with"] == []
    assert body["changed_hashes"] == ["scaled"]
    assert body["cache_changed"] is True
    expected = (
        FLAT_CONFIG.replace("./cache  #", "./other_cache #")
        .replace("factor: 4", "factor: 6")
        .replace("\n", "\r\n")
    )
    assert path.read_bytes().decode("utf-8") == expected
    saved = body["session"]
    assert saved["version"] == version + 1
    assert fields(saved)["factor"]["value"] == 6
    assert saved["run_config"]["user_cache_dir"] == "./other_cache"

    cli = cli_hashes(monkeypatch, root, "flat.yaml", "--config", path.as_posix())
    served = client.get(f"/api/sessions/{sid}/preview").json()
    assert hashes(served) == cli
    graph = {n["id"]: n["hash"] for n in saved["graph"]["nodes"] if n.get("hash")}
    assert graph == cli

    again = save(client, sid, inputs={"factor": 6, "colour": "blue"}).json()
    assert again["changed"] is False
    assert again["session"]["version"] == version + 1
    assert path.read_bytes().decode("utf-8") == expected


def test_save_removes_inputs_left_out_and_writes_null_settings(
    client, config, tmp_path
):
    root = recipe_root(config, tmp_path, "flat.yaml")
    path = root / "flat.config.yaml"
    path.write_text(FLAT_CONFIG, encoding="utf-8")
    sid = open_recipe(client, root / "flat.yaml")["id"]
    response = save(
        client,
        sid,
        inputs={"factor": 4},
        settings={"executor": None, "outputs_dir": None, "dask_workers": 2},
    )
    assert response.status_code == 200, response.text
    text = path.read_text(encoding="utf-8")
    assert "colour" not in text and "factor: 4  # four, not three" in text
    assert "executor: null\ndask_workers: 2\n" in text
    assert "outputs_dir" not in text
    run_config = response.json()["session"]["run_config"]
    assert run_config["unused_inputs"] == []
    assert run_config["dask_workers"] == 2


def test_created_config_validates_once_its_inputs_are_filled(client, config, tmp_path):
    root = recipe_root(config, tmp_path)
    (root / "data").mkdir()
    (root / "data" / "a.raw").write_text("x", encoding="utf-8")
    (root / "needs.yaml").write_text(NEEDS, encoding="utf-8")
    session = open_recipe(client, root / "needs.yaml")
    sid = session["id"]

    response = create(client, sid, version=session["version"])
    assert response.status_code == 201, response.text
    created = response.json()
    path = root / "needs.config.yaml"
    template = path.read_text(encoding="utf-8")
    assert "  # Required. Folder of raw files.\n  raw_folder: null\n" in template
    assert "# Workers of a local Dask cluster.\ndask_workers: null\n" in template
    assert "factor" not in template
    run_config = created["run_config"]
    assert run_config["path"] == path.as_posix() and run_config["chosen"] is None
    assert run_config["contents"]["inputs"] == {"raw_folder": None}
    assert fields(created)["raw_folder"]["source"] == "missing"
    assert created["validation"]["valid"] is False
    messages = [p["message"] for p in created["validation"]["problems"]]
    unset = "param 'folder' needs recipe input 'raw_folder', which has no value"
    assert any(unset in m for m in messages)

    again = create(client, sid)
    assert again.status_code == 409
    assert again.json()["error"] == "TargetExists"
    assert again.json()["details"] == {"paths": [path.as_posix()]}

    response = save(
        client,
        sid,
        inputs={"raw_folder": "./data"},
        settings={"user_cache_dir": "./cache"},
    )
    assert response.status_code == 200, response.text
    filled = response.json()["session"]
    assert filled["validation"]["valid"] is True, filled["validation"]
    raw = fields(filled)["raw_folder"]
    assert (raw["source"], raw["value"]) == ("config", "./data")
    text = path.read_text(encoding="utf-8")
    assert text == template.replace("raw_folder: null", "raw_folder: ./data").replace(
        "user_cache_dir: null", "user_cache_dir: ./cache"
    )
    loaded = api.run_config(root / "needs.yaml")
    assert loaded.inputs == {"raw_folder": "./data"}


def test_created_config_is_chosen_when_discovery_would_miss_it(
    client, config, tmp_path
):
    root = recipe_root(config, tmp_path, "flat.yaml")
    explicit = root / "server.config.yaml"
    explicit.write_text("executor: dask\n", encoding="utf-8")
    config.run_config = explicit
    session = open_recipe(client, root / "flat.yaml")
    assert session["run_config"]["path"] == explicit.as_posix()
    created = create(client, session["id"]).json()
    path = (root / "flat.config.yaml").resolve().as_posix()
    assert created["run_config"]["chosen"] == path
    assert created["run_config"]["source"] == path


def test_choose_and_clear(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    (root / "flat.config.yaml").write_text("inputs: {factor: 4}\n", encoding="utf-8")
    other = root / "other.config.yaml"
    other.write_text("inputs: {factor: 9}\nexecutor: dask\n", encoding="utf-8")
    session = open_recipe(client, root / "flat.yaml")
    sid, version = session["id"], session["version"]

    chosen = choose(client, sid, "other.config.yaml", version=version).json()
    assert chosen["version"] == version + 1
    run_config = chosen["run_config"]
    assert run_config["chosen"] == other.resolve().as_posix()
    assert run_config["path"] == run_config["source"] == other.resolve().as_posix()
    assert run_config["executor"] == "dask"
    assert fields(chosen)["factor"]["value"] == 9

    same = choose(client, sid, other.as_posix()).json()
    assert same["version"] == version + 1

    restarted = TestClient(create_app(config), base_url=BASE_URL)
    reopened = restarted.get(f"/api/sessions/{sid}").json()
    assert reopened["run_config"]["chosen"] == other.resolve().as_posix()
    assert fields(reopened)["factor"]["value"] == 9

    missing = choose(restarted, sid, "gone.config.yaml")
    assert missing.status_code == 404
    cleared = choose(restarted, sid, None).json()
    assert cleared["run_config"]["chosen"] is None
    assert cleared["run_config"]["path"] == (root / "flat.config.yaml").as_posix()
    assert fields(cleared)["factor"]["value"] == 4


def test_a_chosen_file_that_does_not_load_is_still_shown(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    broken = root / "broken.config.yaml"
    broken.write_text("# bad\ncolour: blue\n", encoding="utf-8")
    sid = open_recipe(client, root / "flat.yaml")["id"]
    session = choose(client, sid, broken.as_posix()).json()
    run_config = session["run_config"]
    assert run_config["source"] is None
    assert run_config["path"] == broken.resolve().as_posix()
    assert "unknown key" in run_config["error"]
    assert run_config["contents"] == {"inputs": {}, "settings": {}}
    assert session["validation"]["valid"] is False


def test_choose_create_and_save_are_limited_to_the_roots(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    outside = tmp_path / "outside.config.yaml"
    outside.write_text("executor: dask\n", encoding="utf-8")
    session = open_recipe(client, root / "flat.yaml")
    sid = session["id"]

    refused = choose(client, sid, outside.as_posix())
    assert refused.status_code == 403
    assert refused.json()["error"] == "PathNotAllowed"
    assert choose(client, sid, "../outside.config.yaml").status_code == 403

    config.run_config = outside
    explicit = client.post(
        f"/api/sessions/{sid}/revert", json={}, headers=HEADERS
    ).json()
    assert explicit["run_config"]["path"] == outside.as_posix()
    assert save(client, sid, settings={"executor": "prefect"}).status_code == 403
    assert outside.read_text(encoding="utf-8") == "executor: dask\n"

    config.recipe_roots.remove(root)
    assert create(client, sid).status_code == 403
    assert not (root / "flat.config.yaml").exists()


def test_stale_version_is_409(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    (root / "flat.config.yaml").write_text("inputs: {factor: 4}\n", encoding="utf-8")
    session = open_recipe(client, root / "flat.yaml")
    sid, stale = session["id"], session["version"] + 3
    for response in (
        choose(client, sid, None, version=stale),
        create(client, sid, version=stale),
        save(client, sid, inputs={"factor": 5}, version=stale),
    ):
        assert response.status_code == 409
        assert response.json()["error"] == "StaleVersion"
        assert response.json()["details"] == {"version": session["version"]}
    assert (root / "flat.config.yaml").read_text() == "inputs: {factor: 4}\n"


def test_save_errors(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    sid = open_recipe(client, root / "flat.yaml")["id"]
    missing = save(client, sid, inputs={"factor": 5})
    assert missing.status_code == 400
    assert "no run config file" in missing.json()["message"]
    (root / "flat.config.yaml").write_text("executor: dask\n", encoding="utf-8")
    assert save(client, sid, settings={"colour": "blue"}).status_code == 422
    assert save(client, sid, settings={"executor": "fast"}).status_code == 422


def test_non_finite_values_arrive_in_their_yaml_spelling(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    flat = root / "flat.yaml"
    flat.write_text(flat.read_text().replace("default: 3", "default: .inf"))
    (root / "flat.config.yaml").write_text(
        "storage_options: {limit: -.inf}\ninputs:\n  factor: .nan\n", encoding="utf-8"
    )

    session = open_recipe(client, flat)

    factor = fields(session)["factor"]
    assert (factor["default"], factor["value"]) == (".inf", ".nan")
    assert session["inputs"]["values"]["factor"] == ".nan"
    run_config = session["run_config"]
    assert run_config["storage_options"] == {"limit": "-.inf"}
    assert run_config["contents"]["inputs"] == {"factor": ".nan"}


def test_a_config_save_reports_changed_hashes_and_cache_moves(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    path = root / "flat.config.yaml"
    path.write_text(
        "# The curated cache.\nsurvey_cache_dir: ./survey\n"
        "user_cache_dir: ./cache\ninputs:\n  factor: 4\n",
        encoding="utf-8",
    )
    sid = open_recipe(client, root / "flat.yaml")["id"]

    hashed = save(client, sid, inputs={"factor": 5}).json()
    assert hashed["changed_hashes"] == ["scaled"]
    assert hashed["cache_changed"] is False
    unrelated = save(client, sid, settings={"executor": "sequential"}).json()
    assert unrelated["changed"] is True
    assert unrelated["changed_hashes"] == [] and unrelated["cache_changed"] is False

    moved = save(client, sid, settings={"survey_cache_dir": "./elsewhere"}).json()
    assert moved["changed_hashes"] == [] and moved["cache_changed"] is True
    assert "# The curated cache.\nsurvey_cache_dir: ./elsewhere\n" in path.read_text()
    options = save(client, sid, settings={"storage_options": {"token": "t"}}).json()
    assert options["cache_changed"] is True
    same = save(client, sid, settings={"executor": "sequential"}).json()
    assert same["changed"] is False
    assert same["changed_hashes"] == [] and same["cache_changed"] is False


def test_cache_changed_follows_the_settings_a_run_gets(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    (root / "flat.config.yaml").write_text(
        "survey_cache_dir: ./survey\n", encoding="utf-8"
    )
    sid = open_recipe(client, root / "flat.yaml")["id"]

    plain = save(client, sid, settings={"survey_cache_dir": "./moved"}).json()
    assert plain["cache_changed"] is True
    response = client.put(
        f"/api/sessions/{sid}/config",
        json={"survey_cache_dir": "./mine"},
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    hidden = save(client, sid, settings={"survey_cache_dir": "./again"}).json()
    assert hidden["changed"] is True and hidden["cache_changed"] is False


def test_save_as_takes_the_chosen_config_along(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    other = root / "other.config.yaml"
    other.write_text("# Mine.\ninputs: {factor: 9}\n", encoding="utf-8")
    sid = open_recipe(client, root / "flat.yaml")["id"]
    choose(client, sid, other.as_posix())

    def save_as(path):
        response = client.post(
            f"/api/sessions/{sid}/save-as", json={"path": path}, headers=HEADERS
        )
        assert response.status_code == 200, response.text
        session = response.json()["session"]
        assert fields(session)["factor"]["value"] == 9
        return session["run_config"]

    beside = save_as("v2.yaml")
    assert not (root / "v2.config.yaml").exists()
    assert beside["chosen"] == beside["path"] == other.resolve().as_posix()

    elsewhere = save_as("copies/v3.yaml")
    copy = root / "copies" / "v3.config.yaml"
    assert copy.read_text(encoding="utf-8") == other.read_text(encoding="utf-8")
    assert elsewhere["chosen"] is None
    assert elsewhere["path"] == copy.as_posix()


def test_save_for_this_recipe_only_writes_its_own_copy(client, config, tmp_path):
    root = recipe_root(config, tmp_path, "flat.yaml")
    shutil.copy(root / "flat.yaml", root / "other.yaml")
    shared = root / "aa-recipe.config.yaml"
    shared.write_text(FLAT_CONFIG, encoding="utf-8")
    session = open_recipe(client, root / "flat.yaml")
    assert session["run_config"]["shared_with"] == [(root / "other.yaml").as_posix()]

    response = save(
        client,
        session["id"],
        inputs={"factor": 6, "colour": "blue"},
        scope="recipe",
        version=session["version"],
    )
    assert response.status_code == 200, response.text
    body = response.json()
    own = root / "flat.config.yaml"
    assert own.read_text(encoding="utf-8") == FLAT_CONFIG.replace(
        "factor: 4", "factor: 6"
    )
    assert shared.read_text(encoding="utf-8") == FLAT_CONFIG
    assert body["changed"] is True
    assert body["changed_hashes"] == ["scaled"]
    assert body["cache_changed"] is False
    run_config = body["session"]["run_config"]
    assert run_config["path"] == own.as_posix()
    assert run_config["chosen"] is None
    assert run_config["shared_with"] == []
    assert fields(body["session"])["factor"]["value"] == 6

    again = save(client, session["id"], inputs={"factor": 7}, scope="recipe")
    assert again.status_code == 409
    assert again.json()["error"] == "TargetExists"
    assert own.read_text(encoding="utf-8") == FLAT_CONFIG.replace(
        "factor: 4", "factor: 6"
    )
    assert save(client, session["id"], scope="everywhere").status_code == 422
