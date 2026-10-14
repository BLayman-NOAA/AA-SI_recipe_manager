# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Listing the recipes under the recipe roots."""

from __future__ import annotations

import shutil
from dataclasses import replace

from fastapi.testclient import TestClient

from aa_recipe_manager.server import recipes as recipes_module
from aa_recipe_manager.server.app import create_app
from aa_recipe_manager.server.recipes import scan_recipes

from .conftest import BASE_URL, FIXTURES


def test_fixture_recipes_are_listed(client):
    response = client.get("/api/recipes")
    assert response.status_code == 200
    body = response.json()
    assert body["roots"] == [FIXTURES.resolve().as_posix()]
    entries = {entry["relative"]: entry for entry in body["recipes"]}
    assert list(entries) == sorted(entries)
    assert set(entries) == {
        "flat.yaml",
        "with_sub.yaml",
        "mapped.yaml",
        "custom_no_cache_key.yaml",
        "sub/chain.yaml",
        "sub/leaf.yaml",
    }
    assert entries["flat.yaml"]["name"] == "client_flat"
    assert entries["flat.yaml"]["version"] == "1.0"
    assert entries["flat.yaml"]["modified"].endswith("Z")
    assert entries["with_sub.yaml"]["included_by"] == []
    with_sub = entries["with_sub.yaml"]["path"]
    chain = entries["sub/chain.yaml"]["path"]
    assert entries["sub/chain.yaml"]["included_by"] == [with_sub]
    assert entries["sub/leaf.yaml"]["included_by"] == [chain]


def test_scan_skips_config_files_hidden_folders_and_non_recipes(tmp_path):
    root = tmp_path / "root"
    shutil.copytree(FIXTURES, root)
    (root / "flat.config.yaml").write_text("inputs: {factor: 4}\n", encoding="utf-8")
    for folder in (".hidden", "outputs", "node_modules"):
        (root / folder).mkdir()
        shutil.copy(FIXTURES / "flat.yaml", root / folder / "flat.yaml")
    (root / "notes.yaml").write_text("just: a mapping\n", encoding="utf-8")
    for folder in (".hidden", "outputs"):
        (root / folder / "broken.yaml").write_text("recipe: [", encoding="utf-8")

    found = {entry.relative for entry in scan_recipes([root])}
    assert found == {
        "flat.yaml",
        "with_sub.yaml",
        "mapped.yaml",
        "custom_no_cache_key.yaml",
        "sub/chain.yaml",
        "sub/leaf.yaml",
    }


def test_overlapping_roots_list_a_file_once(config):
    roots = [FIXTURES.resolve(), (FIXTURES / "sub").resolve()]
    app = create_app(replace(config, recipe_roots=roots))
    client = TestClient(app, base_url=BASE_URL)
    paths = [entry["path"] for entry in client.get("/api/recipes").json()["recipes"]]
    assert len(paths) == len(set(paths)) == 6


def test_unparsable_yaml_is_listed_with_its_error(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    shutil.copy(FIXTURES / "flat.yaml", root / "flat.yaml")
    (root / "typo.yaml").write_text("recipe: [unclosed\nsteps: []\n")
    entries = {entry.relative: entry for entry in scan_recipes([root])}
    assert entries["flat.yaml"].error is None
    typo = entries["typo.yaml"]
    assert typo.name == "typo"
    assert "line" in typo.error
    assert (typo.description, typo.version, typo.included_by) == (None, None, [])


def test_scan_cache_rereads_only_changed_files(tmp_path, monkeypatch):
    root = tmp_path / "root"
    root.mkdir()
    recipe = root / "flat.yaml"
    shutil.copy(FIXTURES / "flat.yaml", recipe)
    cache = {}
    assert scan_recipes([root], cache)[0].name == "client_flat"
    reads = []
    original = recipes_module.YAML
    monkeypatch.setattr(
        recipes_module, "YAML", lambda **kw: reads.append(1) or original(**kw)
    )
    scan_recipes([root], cache)
    assert reads == []
    recipe.write_text(recipe.read_text().replace("client_flat", "renamed_flat"))
    assert scan_recipes([root], cache)[0].name == "renamed_flat"
    assert reads == [1]
