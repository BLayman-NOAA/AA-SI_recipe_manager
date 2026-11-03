# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Editing a session: edits, undo and redo, sync with the disk, save, save as."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.editing import digest
from aa_recipe_manager.server.app import create_app
from test_disposal_fan_in import _recipe, helpers  # noqa: F401

from .conftest import BASE_URL, FIXTURES, HEADERS, open_session

SET_K = {
    "kind": "set_param",
    "step": "leaf_add",
    "name": "k",
    "value": 7,
    "path": [],
    "position": None,
}


def set_k(value) -> dict:
    return {**SET_K, "value": value}


def set_n(value) -> dict:
    return {"kind": "set_param", "step": "seed", "name": "n", "value": value}


@pytest.fixture
def root(config, tmp_path) -> Path:
    """A writable copy of the client fixtures, added as a recipe root."""
    folder = tmp_path / "recipes"
    shutil.copytree(FIXTURES, folder)
    config.recipe_roots.append(folder)
    return folder


def open_at(client, path: Path) -> dict:
    response = client.post(
        "/api/sessions", json={"path": path.as_posix()}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    return response.json()


def post(client, sid: str, action: str, body: dict | None = None):
    return client.post(
        f"/api/sessions/{sid}/{action}", json=body or {}, headers=HEADERS
    )


def edit(client, sid: str, *operations, version=None) -> dict:
    body = {"operations": list(operations), "version": version}
    response = post(client, sid, "edits", body)
    assert response.status_code == 200, response.text
    return response.json()


def folder_of(config, sid: str) -> Path:
    return config.state_dir / "sessions" / sid


def records(config, sid: str) -> list[dict]:
    text = (folder_of(config, sid) / "history.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines()]


def copy_bytes(config, sid: str) -> dict[str, bytes]:
    files = folder_of(config, sid) / "files"
    return {
        path.relative_to(files).as_posix(): path.read_bytes()
        for path in sorted(files.rglob("*.yaml"))
    }


def text_of(client, sid: str, label: str) -> str:
    return client.get(f"/api/sessions/{sid}/files/{label}").json()["text"]


def step_hashes(session: dict) -> dict[str, str]:
    return {
        node["id"]: node["hash"]
        for node in session["graph"]["nodes"]
        if node["kind"] == "step"
    }


def restart(config) -> TestClient:
    return TestClient(create_app(config), base_url=BASE_URL)


def test_open_writes_the_copy_and_the_journal(client, config):
    session = open_session(client, "with_sub.yaml")
    folder = folder_of(config, session["id"])
    for label in ("with_sub.yaml", "sub/chain.yaml", "sub/leaf.yaml"):
        assert (folder / "files" / label).read_bytes() == (
            FIXTURES / label
        ).read_bytes()
    (saved,) = records(config, session["id"])
    assert saved["record"] == "saved"
    assert len(list((folder / "texts").iterdir())) == 3

    assert session["read_only"] is False
    assert all(ref["editable"] for ref in session["files"])
    assert session["dirty"] == [] and session["conflicts"] == []
    assert session["history"] == {
        "can_undo": False,
        "can_redo": False,
        "undo_kind": None,
        "redo_kind": None,
        "seq": 0,
        "note": None,
    }
    assert all(step_hashes(session).values())


def test_edit_changes_hashes_downstream(client, config):
    session = open_session(client, "with_sub.yaml")
    sid = session["id"]
    before = records(config, sid)

    answer = edit(client, sid, SET_K, version=0)
    assert answer["changed"] is True
    assert answer["changed_hashes"] == ["leaf_add", "total"]
    changed = answer["session"]
    assert changed["version"] == 1
    assert changed["dirty"] == ["sub/leaf.yaml"]
    assert [ref["dirty"] for ref in changed["files"]] == [False, True, False]
    assert changed["history"]["can_undo"] is True
    assert changed["history"]["undo_kind"] == "set_param"
    hashes = step_hashes(changed)
    assert hashes["seed"] == step_hashes(session)["seed"]
    assert hashes["leaf_add"] != step_hashes(session)["leaf_add"]

    copy = folder_of(config, sid) / "files" / "sub" / "leaf.yaml"
    assert "      k: 7\n" in copy.read_text(encoding="utf-8")
    assert "      k: 7\n" in text_of(client, sid, "sub/leaf.yaml")
    assert (FIXTURES / "sub" / "leaf.yaml").read_text().count("${inputs.k}") == 1

    after = records(config, sid)
    assert len(after) == len(before) + 1
    assert after[-1]["record"] == "entry"
    assert after[-1]["entry"] == answer["entries"][0]
    texts = folder_of(config, sid) / "texts"
    for pair in after[-1]["texts"].values():
        assert (texts / pair["before"]).is_file() and (texts / pair["after"]).is_file()

    again = edit(client, sid, SET_K)
    assert again["changed"] is False
    assert again["entries"] == [] and again["changed_hashes"] == []
    assert again["session"]["version"] == 1
    assert records(config, sid) == after


FLOAT_LEAF = """\
recipe: {name: float_leaf, version: "1.0", schema_version: "1"}
inputs:
  seconds:
    type: float
    default: 0.5
steps:
  - id: start
    op: custom
    custom_spec:
      description: Seven.
      callable_path: _stage9_helpers.const7
      outputs:
        v: {type: int}
      output_map: {v: __return__}
      dependency: {name: pytest, version: ">=7.0", source: pypi}
  - id: nap
    op: custom
    inputs:
      x: ${start.v}
    params:
      seconds: ${inputs.seconds}
    custom_spec:
      description: Sleep.
      callable_path: _stage9_helpers.sleep_for
      inputs:
        x: {type: int}
      params:
        seconds: {type: float, default: 0.2}
      outputs:
        out: {type: int}
      output_map: {out: __return__}
      dependency: {name: pytest, version: ">=7.0", source: pypi}
"""

FLOAT_PARENT = """\
recipe: {name: float_parent, version: "1.0", schema_version: "1"}
steps:
  - include: sub/float_leaf.yaml
    input_overrides:
      seconds: 0.1
"""


def test_a_whole_number_for_a_float_param_is_a_float(client, config, root):
    sid = open_session(client, "slow.yaml")["id"]
    answer = edit(client, sid, {**SET_K, "step": "slow", "name": "seconds", "value": 2})
    assert answer["entries"][0]["op"]["value"] == 2.0
    assert "seconds: 2.0\n" in text_of(client, sid, "slow.yaml")
    default = {
        "kind": "set_recipe_input",
        "document": "slow.yaml",
        "name": "seconds",
        "field": "default",
        "value": 1,
    }
    answer = edit(client, sid, default)
    assert answer["entries"][0]["op"]["value"] == 1.0
    assert "default: 1.0\n" in text_of(client, sid, "slow.yaml")

    (root / "sub" / "float_leaf.yaml").write_text(FLOAT_LEAF, encoding="utf-8")
    (root / "float_parent.yaml").write_text(FLOAT_PARENT, encoding="utf-8")
    sid = open_at(client, root / "float_parent.yaml")["id"]
    override = {
        "kind": "set_include_override",
        "include": "sub/float_leaf.yaml",
        "name": "seconds",
        "value": 3,
    }
    answer = edit(client, sid, override)
    assert answer["entries"][0]["op"]["value"] == 3.0
    assert "seconds: 3.0\n" in text_of(client, sid, "float_parent.yaml")
    answer = edit(client, sid, {**default, "document": "sub/float_leaf.yaml"})
    assert answer["entries"][0]["op"]["value"] == 1.0
    assert "default: 1.0\n" in text_of(client, sid, "sub/float_leaf.yaml")


def test_a_failed_batch_undoes_the_operations_before_it(client, config):
    sid = open_session(client, "with_sub.yaml")["id"]
    before = copy_bytes(config, sid)
    response = post(
        client,
        sid,
        "edits",
        {"operations": [SET_K, {**SET_K, "step": "nope"}]},
    )
    assert response.status_code == 404
    assert response.json()["error"] == "UnknownTarget"
    assert copy_bytes(config, sid) == before
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["dirty"] == [] and session["version"] == 1
    assert session["history"]["can_redo"] and not session["history"]["can_undo"]
    last = records(config, sid)[-2:]
    assert [r["record"] for r in last] == ["entry", "undo"]
    assert last[1]["seq"] == last[0]["entry"]["seq"]


def test_malformed_operations_are_400(client):
    sid = open_session(client, "with_sub.yaml")["id"]
    response = post(client, sid, "edits", {"operations": [{"kind": "explode"}]})
    assert response.status_code == 400
    assert "Unknown operation kind" in response.json()["message"]
    response = post(client, sid, "edits", {"operations": [{"step": "x"}]})
    assert response.status_code == 400
    body = json.dumps({"operations": [set_k(float("nan"))]})
    assert "NaN" in body
    response = client.post(
        f"/api/sessions/{sid}/edits",
        content=body,
        headers={**HEADERS, "Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert "NaN and infinity" in response.json()["message"]


def test_nan_in_a_file_does_not_break_the_responses(client):
    sid = open_session(client, "with_sub.yaml")["id"]
    text = text_of(client, sid, "sub/leaf.yaml").replace("${inputs.k}", ".nan")
    replaced = edit(
        client, sid, {"kind": "replace_text", "document": "sub/leaf.yaml", "text": text}
    )
    nodes = {node["id"]: node for node in replaced["session"]["graph"]["nodes"]}
    params = {param["name"]: param for param in nodes["leaf_add"]["params"]}
    assert params["k"]["value"] == ".nan"
    answer = edit(client, sid, SET_K)
    assert answer["entries"][0]["inverse"]["value"] == ".nan"
    response = post(client, sid, "undo")
    assert response.status_code == 200, response.text
    assert response.json()["entries"][0]["inverse"]["value"] == ".nan"
    assert client.get(f"/api/sessions/{sid}").status_code == 200


def test_checkpoint_removal_a_fan_in_needs_is_422(client, root, helpers):  # noqa: F811
    path = root / "fan_in.yaml"
    path.write_text(_recipe(convert_checkpoint="always"), encoding="utf-8")
    sid = open_at(client, path)["id"]
    response = post(
        client,
        sid,
        "edits",
        {"operations": [{"kind": "set_checkpoint", "step": "convert", "value": None}]},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "EditRejected"
    assert any(
        "set 'checkpoint: always' on 'convert'" in problem
        for problem in body["details"]["problems"]
    )
    assert "checkpoint: always" in text_of(client, sid, "fan_in.yaml")


@pytest.mark.parametrize(
    ("operation", "status"),
    [
        ({"kind": "unset_param", "step": "fetch", "name": "nope"}, 404),
        ({"kind": "unset_param", "step": "fetch", "name": "tags", "path": [0]}, 400),
        ({"kind": "set_checkpoint", "step": "convert", "value": None}, 422),
    ],
    ids=["404", "400", "422"],
)
def test_a_refused_edit_leaves_the_next_edit_minimal(
    client,
    root,
    helpers,  # noqa: F811
    operation,
    status,
):
    path = root / "fan_in.yaml"
    text = (
        _recipe(convert_checkpoint="always")
        .replace("steps:\n", "steps:\n\n  # The segments.\n")
        .replace("      item: ${_item}\n", "      item: ${_item}\n      tags: [1, 2]\n")
    )
    path.write_text(text, encoding="utf-8")
    sid = open_at(client, path)["id"]

    assert post(client, sid, "edits", {"operations": [operation]}).status_code == status
    noop = {"kind": "set_checkpoint", "step": "convert", "value": "always"}
    answer = edit(client, sid, noop)
    assert answer["changed"] is False and answer["entries"] == []
    assert answer["session"]["dirty"] == []
    tags = {"kind": "set_param", "step": "fetch", "name": "tags", "value": [3, 4]}
    edit(client, sid, tags)

    after = text_of(client, sid, "fan_in.yaml")
    assert after == text.replace("tags: [1, 2]", "tags: [3, 4]")


def test_replace_text_with_bad_yaml_is_422(client, config):
    sid = open_session(client, "with_sub.yaml")["id"]
    before = copy_bytes(config, sid)
    bad = {
        "kind": "replace_text",
        "document": "sub/leaf.yaml",
        "text": "steps: [oops\n",
    }
    response = post(client, sid, "edits", {"operations": [bad]})
    assert response.status_code == 422
    assert response.json()["error"] == "EditRejected"
    [problem] = response.json()["details"]["problems"]
    assert problem.startswith("sub/leaf.yaml, line 2: ")
    assert "unicode string" not in problem
    assert "(line: " not in problem
    assert copy_bytes(config, sid) == before
    assert client.get(f"/api/sessions/{sid}").json()["version"] == 0


def test_undo_and_redo_give_back_the_exact_bytes(client, config):
    sid = open_session(client, "with_sub.yaml")["id"]
    original = copy_bytes(config, sid)
    edit(client, sid, SET_K, version=0)
    first = copy_bytes(config, sid)
    edit(client, sid, set_n(4), version=1)
    second = copy_bytes(config, sid)

    undone = post(client, sid, "undo", {"version": 2}).json()
    assert undone["changed"] is True
    assert undone["changed_hashes"] == ["seed", "chain_inc", "leaf_add", "total"]
    assert undone["session"]["version"] == 3
    assert copy_bytes(config, sid) == first
    undone = post(client, sid, "undo", {"version": 3}).json()
    assert copy_bytes(config, sid) == original
    history = undone["session"]["history"]
    assert (history["can_undo"], history["can_redo"]) == (False, True)
    assert history["redo_kind"] == "set_param"
    assert undone["session"]["dirty"] == []
    nothing = post(client, sid, "undo").json()
    assert nothing["changed"] is False and nothing["session"]["version"] == 4

    post(client, sid, "redo", {"version": 4})
    redone = post(client, sid, "redo", {"version": 5}).json()
    assert copy_bytes(config, sid) == second
    assert redone["session"]["version"] == 6
    assert redone["session"]["history"]["can_redo"] is False

    for action, body in (
        ("edits", {"operations": [set_k(9)], "version": 2}),
        ("undo", {"version": 2}),
        ("redo", {"version": 2}),
        ("revert", {"version": 2}),
        ("reload", {"version": 2}),
    ):
        response = post(client, sid, action, body)
        assert response.status_code == 409, action
        assert response.json()["error"] == "StaleVersion"
        assert response.json()["details"] == {"version": 6}
    assert copy_bytes(config, sid) == second


def test_inputs_and_run_config_changes_bump_the_version(client):
    sid = open_session(client, "with_sub.yaml")["id"]
    response = client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"n": 2}}, headers=HEADERS
    )
    assert response.json()["version"] == 1
    response = client.put(
        f"/api/sessions/{sid}/config", json={"executor": "dask"}, headers=HEADERS
    )
    assert response.json()["version"] == 2


def test_revert_discards_the_edits_and_the_history(client, config):
    sid = open_session(client, "with_sub.yaml")["id"]
    original = copy_bytes(config, sid)
    edit(client, sid, SET_K)
    response = post(client, sid, "revert", {"version": 1})
    assert response.status_code == 200, response.text
    session = response.json()
    assert session["dirty"] == [] and session["history"]["can_undo"] is False
    assert copy_bytes(config, sid) == original
    assert [r["record"] for r in records(config, sid)] == ["saved"]


def test_save_writes_the_origins(client, root):
    leaf = root / "sub" / "leaf.yaml"
    original = leaf.read_bytes()
    sid = open_at(client, root / "with_sub.yaml")["id"]

    edit(client, sid, SET_K)
    post(client, sid, "undo")
    response = post(client, sid, "save")
    assert response.status_code == 200, response.text
    assert response.json()["written"] == []
    assert leaf.read_bytes() == original

    edit(client, sid, SET_K)
    saved = post(client, sid, "save").json()
    assert saved["written"] == [leaf.resolve().as_posix()]
    assert saved["reloaded"] == []
    assert saved["session"]["dirty"] == []
    assert b"      k: 7\n" in leaf.read_bytes()

    edit(client, sid, set_k(8))
    leaf.write_text(leaf.read_text().replace("k: 7", "k: 70"))
    response = post(client, sid, "save")
    assert response.status_code == 409
    assert response.json()["error"] == "ChangedOnDisk"
    assert response.json()["details"] == {"paths": [leaf.resolve().as_posix()]}
    assert b"k: 70" in leaf.read_bytes()
    session = client.get(f"/api/sessions/{sid}").json()
    (conflict,) = session["conflicts"]
    assert (conflict["path"], conflict["error"]) == ("sub/leaf.yaml", None)

    response = post(client, sid, "save", {"overwrite": True})
    assert response.status_code == 200, response.text
    assert b"      k: 8\n" in leaf.read_bytes()
    assert response.json()["session"]["conflicts"] == []


def test_sync_reloads_a_clean_file(client, root):
    leaf = root / "sub" / "leaf.yaml"
    sid = open_at(client, root / "with_sub.yaml")["id"]
    old = text_of(client, sid, "sub/leaf.yaml")
    quiet = post(client, sid, "sync").json()
    assert quiet["reloaded"] == [] and quiet["session"]["version"] == 0

    leaf.write_text(leaf.read_text().replace("${inputs.k}", "2"))
    synced = post(client, sid, "sync").json()
    assert synced["reloaded"] == ["sub/leaf.yaml"]
    assert synced["conflicts"] == []
    assert synced["entries"][0]["op"]["kind"] == "replace_text"
    assert synced["entries"][0]["documents"] == ["sub/leaf.yaml"]
    session = synced["session"]
    assert session["version"] == 1
    assert session["dirty"] == [] and session["history"]["can_undo"] is True
    assert "      k: 2\n" in text_of(client, sid, "sub/leaf.yaml")

    undone = post(client, sid, "undo").json()
    assert text_of(client, sid, "sub/leaf.yaml") == old
    assert undone["session"]["dirty"] == ["sub/leaf.yaml"]


def test_sync_reports_conflicts(client, root):
    leaf = root / "sub" / "leaf.yaml"
    chain = root / "sub" / "chain.yaml"
    sid = open_at(client, root / "with_sub.yaml")["id"]
    edit(client, sid, SET_K)

    leaf.write_text(leaf.read_text().replace("${inputs.k}", "2"))
    chain.write_text("steps: [oops\n")
    synced = post(client, sid, "sync").json()
    assert synced["reloaded"] == [] and synced["session"]["version"] == 1
    conflicts = {c["path"]: c for c in synced["conflicts"]}
    assert conflicts["sub/leaf.yaml"]["error"] is None
    leaf_sha = hashlib.sha256(leaf.read_text().encode("utf-8")).hexdigest()
    assert conflicts["sub/leaf.yaml"]["disk_sha256"] == leaf_sha
    assert conflicts["sub/chain.yaml"]["error"]
    assert "      k: 7\n" in text_of(client, sid, "sub/leaf.yaml")
    assert "client_chain" in text_of(client, sid, "sub/chain.yaml")
    session = client.get(f"/api/sessions/{sid}").json()
    assert session["conflicts"] == synced["conflicts"]

    response = post(client, sid, "reload", {"files": ["sub/chain.yaml"]})
    assert response.status_code == 422
    assert response.json()["error"] == "EditRejected"
    assert response.json()["message"].startswith("sub/chain.yaml, line 2: ")

    reloaded = post(client, sid, "reload", {"files": ["sub/leaf.yaml"]}).json()
    assert reloaded["changed"] is True
    assert reloaded["changed_hashes"] == ["leaf_add", "total"]
    assert "      k: 2\n" in text_of(client, sid, "sub/leaf.yaml")
    assert [c["path"] for c in reloaded["session"]["conflicts"]] == ["sub/chain.yaml"]
    response = post(client, sid, "reload", {"files": ["nope.yaml"]})
    assert response.status_code == 404

    chain.write_bytes((FIXTURES / "sub" / "chain.yaml").read_bytes())
    assert post(client, sid, "sync").json()["conflicts"] == []


def test_a_whole_number_edit_hashes_as_the_cli_edit(client, root, tmp_path, cli_runner):
    other = tmp_path / "cli"
    shutil.copytree(root, other)
    sid = open_at(client, root / "slow.yaml")["id"]
    edit(
        client,
        sid,
        {"kind": "set_param", "step": "slow", "name": "seconds", "value": 1},
    )
    assert post(client, sid, "save").status_code == 200

    result = cli_runner.invoke(
        main, ["edit", str(other / "slow.yaml"), "--set", "slow.seconds=1"]
    )

    assert result.exit_code == 0, result.output
    text = (root / "slow.yaml").read_bytes()
    assert b"      seconds: 1.0\n" in text
    assert (other / "slow.yaml").read_bytes() == text
    hashes = [
        {step: h.step_hash for step, h in api.step_hashes(path).items()}
        for path in (root / "slow.yaml", other / "slow.yaml")
    ]
    assert hashes[0] == hashes[1]


def test_save_syncs_first(client, root):
    leaf = root / "sub" / "leaf.yaml"
    sid = open_at(client, root / "with_sub.yaml")["id"]
    leaf.write_text(leaf.read_text().replace("${inputs.k}", "2"))
    disk = leaf.read_bytes()
    saved = post(client, sid, "save").json()
    assert saved["written"] == []
    assert saved["reloaded"] == ["sub/leaf.yaml"]
    assert leaf.read_bytes() == disk
    assert "      k: 2\n" in text_of(client, sid, "sub/leaf.yaml")
    assert saved["session"]["history"]["can_undo"] is True


def test_undo_and_redo_survive_a_restart(client, config):
    session = open_session(client, "with_sub.yaml")
    sid = session["id"]
    original = copy_bytes(config, sid)
    edit(client, sid, SET_K)
    edit(client, sid, set_n(4))
    both = copy_bytes(config, sid)
    undone = post(client, sid, "undo").json()["session"]

    restarted = restart(config)
    reopened = restarted.get(f"/api/sessions/{sid}").json()
    assert reopened["history"]["can_undo"] is True
    assert reopened["history"]["can_redo"] is True
    assert reopened["history"]["note"] is None
    assert reopened["dirty"] == ["sub/leaf.yaml"]
    assert step_hashes(reopened) == step_hashes(undone)
    assert reopened["version"] == undone["version"]

    post(restarted, sid, "undo")
    assert copy_bytes(config, sid) == original
    post(restarted, sid, "redo")
    post(restarted, sid, "redo")
    assert copy_bytes(config, sid) == both


def test_a_missing_text_truncates_the_history_with_a_note(client, config):
    sid = open_session(client, "with_sub.yaml")["id"]
    leaf = folder_of(config, sid) / "files" / "sub" / "leaf.yaml"
    edit(client, sid, set_k(7))
    seven = leaf.read_text(encoding="utf-8")
    edit(client, sid, set_k(8))
    eight = leaf.read_text(encoding="utf-8")
    edit(client, sid, set_k(9))
    (folder_of(config, sid) / "texts" / digest(seven)).unlink()

    restarted = restart(config)
    history = restarted.get(f"/api/sessions/{sid}").json()["history"]
    assert history["can_undo"] is True
    assert "could not be restored" in history["note"]
    undone = post(restarted, sid, "undo").json()
    assert leaf.read_text(encoding="utf-8") == eight
    assert undone["session"]["history"]["can_undo"] is False


def test_changes_on_disk_while_the_server_was_down(client, config, root):
    leaf = root / "sub" / "leaf.yaml"
    chain = root / "sub" / "chain.yaml"
    sid = open_at(client, root / "with_sub.yaml")["id"]
    text = text_of(client, sid, "sub/chain.yaml").replace("Per item", "Per entry")
    edit(
        client,
        sid,
        {"kind": "replace_text", "document": "sub/chain.yaml", "text": text},
    )
    leaf.write_text(leaf.read_text().replace("${inputs.k}", "2"))
    chain.write_text(chain.read_text().replace("Per item", "Each item"))

    restarted = restart(config)
    synced = post(restarted, sid, "sync").json()
    assert synced["reloaded"] == ["sub/leaf.yaml"]
    (conflict,) = synced["conflicts"]
    assert (conflict["path"], conflict["error"]) == ("sub/chain.yaml", None)


def test_reopen_puts_the_journal_text_back(client, config):
    sid = open_session(client, "with_sub.yaml")["id"]
    edit(client, sid, set_n(4))
    root_copy = folder_of(config, sid) / "files" / "with_sub.yaml"
    edited = root_copy.read_bytes()
    root_copy.write_bytes(edited.replace(b"n: 4", b"n: 40"))

    restarted = restart(config)
    session = restarted.get(f"/api/sessions/{sid}").json()
    assert root_copy.read_bytes() == edited
    assert "restored" in session["history"]["note"]


@pytest.mark.parametrize("sub_edited", [False, True])
def test_a_missing_root_copy_is_restored_from_the_journal(client, config, sub_edited):
    sid = open_session(client, "with_sub.yaml")["id"]
    edit(client, sid, set_n(4))
    if sub_edited:
        edit(client, sid, SET_K)
    copies = copy_bytes(config, sid)
    lines = records(config, sid)
    (folder_of(config, sid) / "files" / "with_sub.yaml").unlink()

    restarted = restart(config)
    response = restarted.get(f"/api/sessions/{sid}")

    assert response.status_code == 200, response.text
    session = response.json()
    assert session["history"]["note"] == (
        "with_sub.yaml was missing from the working copy and was restored from "
        "its history."
    )
    assert copy_bytes(config, sid) == copies
    entries = [r for r in records(config, sid) if r["record"] == "entry"]
    assert entries == [r for r in lines if r["record"] == "entry"]
    post(restarted, sid, "undo")
    if sub_edited:
        post(restarted, sid, "undo")
    assert post(restarted, sid, "undo").json()["changed"] is False
    assert restarted.get(f"/api/sessions/{sid}").json()["dirty"] == []


def test_a_missing_saved_text_matching_the_origin_is_clean(client, config, root):
    sid = open_at(client, root / "with_sub.yaml")["id"]
    saved = [r for r in records(config, sid) if r["record"] == "saved"][-1]
    (folder_of(config, sid) / "texts" / saved["saved"]["sub/leaf.yaml"]).unlink()

    restarted = restart(config)
    session = restarted.get(f"/api/sessions/{sid}").json()

    assert session["dirty"] == [] and session["history"]["note"] is None
    assert post(restarted, sid, "sync").json()["conflicts"] == []
    assert post(restarted, sid, "save").status_code == 200


def test_save_as_beside(client, config, root):
    (root / "with_sub.config.yaml").write_text("executor: dask\n", encoding="utf-8")
    sid = open_at(client, root / "with_sub.yaml")["id"]
    edit(client, sid, SET_K)

    preview = post(client, sid, "save-as", {"path": "v2.yaml", "dry_run": True})
    assert preview.status_code == 200, preview.text
    result = preview.json()["result"]
    assert result["dry_run"] is True and result["existing"] == []
    assert (root / "v2.yaml").as_posix() in result["written"]
    assert result["config"] == (root / "v2.config.yaml").as_posix()
    assert not (root / "v2.yaml").exists()
    assert preview.json()["session"]["version"] == 1

    saved = post(client, sid, "save-as", {"path": "v2.yaml"}).json()
    session = saved["session"]
    assert session["origin"] == (root / "v2.yaml").as_posix()
    assert session["title"] == "v2"
    assert session["files"][0]["path"] == "v2.yaml"
    assert session["run_config"]["source"] == (root / "v2.config.yaml").as_posix()
    assert session["dirty"] == []
    assert session["history"]["can_undo"] is True
    assert (root / "v2.yaml").read_bytes() == (root / "with_sub.yaml").read_bytes()
    assert b"      k: 7\n" in (root / "sub" / "leaf.yaml").read_bytes()

    record = json.loads((folder_of(config, sid) / "session.json").read_text())
    assert record["copy_root"] == "with_sub.yaml"
    restarted = restart(config)
    reopened = restarted.get(f"/api/sessions/{sid}").json()
    assert reopened["origin"] == session["origin"]
    assert reopened["history"]["note"] is None
    assert post(restarted, sid, "undo").json()["session"]["dirty"] == ["sub/leaf.yaml"]

    response = post(client, sid, "save-as", {"path": "v3.config.yaml"})
    assert response.status_code == 400
    assert response.json()["error"] == "WorkspaceError"


def test_save_as_onto_the_recipes_own_file_is_refused(client, root):
    sid = open_at(client, root / "with_sub.yaml")["id"]
    edit(client, sid, SET_K)
    target = root / "copies" / "w2.yaml"
    assert post(client, sid, "save-as", {"path": target.as_posix()}).status_code == 200

    for path in (target.as_posix(), "w2.yaml"):
        response = post(client, sid, "save-as", {"path": path})
        assert response.status_code == 400
        body = response.json()
        assert body["error"] == "WorkspaceError"
        assert body["message"] == "'w2.yaml' is the recipe's own file; use Save."


def test_save_as_elsewhere(client, config, root, tmp_path):
    (root / "with_sub.config.yaml").write_text("executor: dask\n", encoding="utf-8")
    sid = open_at(client, root / "with_sub.yaml")["id"]
    edit(client, sid, SET_K)
    target = root / "copies" / "w2.yaml"

    outside = post(client, sid, "save-as", {"path": (tmp_path / "w2.yaml").as_posix()})
    assert outside.status_code == 403
    assert outside.json()["error"] == "PathNotAllowed"

    preview = post(
        client, sid, "save-as", {"path": target.as_posix(), "dry_run": True}
    ).json()["result"]
    assert preview["dry_run"] is True
    assert preview["root"] == target.as_posix()
    assert target.as_posix() in preview["written"]
    assert preview["rehashed"] is not None
    assert not (root / "copies").exists()

    saved = post(client, sid, "save-as", {"path": target.as_posix()})
    assert saved.status_code == 200, saved.text
    session = saved.json()["session"]
    assert session["origin"] == target.as_posix()
    assert all(
        ref["origin"].startswith(target.parent.as_posix()) for ref in session["files"]
    )
    assert (
        session["run_config"]["source"]
        == (root / "copies" / "w2.config.yaml").as_posix()
    )
    assert session["history"]["can_undo"] is False
    assert [r["record"] for r in records(config, sid)] == ["saved"]
    assert b"      k: 7\n" in (root / "copies" / "sub" / "leaf.yaml").read_bytes()
    assert b"${inputs.k}" in (root / "sub" / "leaf.yaml").read_bytes()

    other = open_at(client, root / "with_sub.yaml")["id"]
    existing = post(client, other, "save-as", {"path": target.as_posix()})
    assert existing.status_code == 409
    assert existing.json()["error"] == "TargetExists"
    assert target.as_posix() in existing.json()["details"]["paths"]
    again = post(
        client, other, "save-as", {"path": target.as_posix(), "overwrite": True}
    )
    assert again.status_code == 200, again.text
    assert b"${inputs.k}" in (root / "copies" / "sub" / "leaf.yaml").read_bytes()


def test_recipe_schema(client):
    response = client.get("/api/schema/recipe")
    assert response.status_code == 200
    assert "steps" in response.json()["properties"]
