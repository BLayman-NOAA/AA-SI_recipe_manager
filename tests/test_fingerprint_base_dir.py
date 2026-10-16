# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Fingerprinting relative paths under a base directory."""

from __future__ import annotations

import json
from pathlib import Path

from aa_recipe_manager import api
from aa_recipe_manager.api import _load_dag
from aa_recipe_manager.cli import main
from aa_recipe_manager.executor.checkpoint import compute_step_hashes

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"
RECIPE = FIXTURES / "paths.yaml"


def _hashes(**kwargs) -> dict[str, str]:
    return {k: v.step_hash for k, v in api.step_hashes(RECIPE, **kwargs).items()}


def _data_folder(root: Path, content: str) -> Path:
    (root / "data").mkdir(parents=True)
    (root / "data" / "sample.txt").write_text(content, encoding="utf-8")
    return root


def test_base_dir_equals_running_from_that_folder(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with_base_dir = _hashes(base_dir=FIXTURES)
    monkeypatch.chdir(FIXTURES)
    assert with_base_dir == _hashes()

    dag = _load_dag(RECIPE)
    monkeypatch.chdir(tmp_path)
    from_elsewhere = compute_step_hashes(dag)
    assert from_elsewhere["start"] != with_base_dir["start"]
    assert compute_step_hashes(dag, base_dir=FIXTURES) == with_base_dir


def test_base_dir_is_the_default_when_absent(monkeypatch):
    monkeypatch.chdir(FIXTURES)
    dag = _load_dag(RECIPE)
    default = compute_step_hashes(dag)
    assert compute_step_hashes(dag, base_dir=None) == default
    assert compute_step_hashes(dag, base_dir=FIXTURES) == default


def test_fingerprint_cache_keys_on_the_effective_path(tmp_path):
    first = _data_folder(tmp_path / "a", "1")
    second = _data_folder(tmp_path / "b", "12345")
    cache: dict = {}
    dag = _load_dag(RECIPE, base_dir=first)
    one = compute_step_hashes(dag, path_fingerprint_cache=cache, base_dir=first)
    two = compute_step_hashes(dag, path_fingerprint_cache=cache, base_dir=second)
    assert one["start"] != two["start"]
    assert set(cache) == {
        (str(first / "data"), "size"),
        (str(second / "data"), "size"),
    }
    again = compute_step_hashes(dag, path_fingerprint_cache=cache, base_dir=first)
    assert again == one


def test_plan_preview_base_dir(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.chdir(FIXTURES)
    result = api.execute(
        RECIPE,
        user_cache_dir=cache,
        outputs_dir=tmp_path / "outputs",
        checkpoint_mode="eager",
    )
    manifest = json.loads(Path(result.manifest_file).read_text(encoding="utf-8"))
    written = {step: entry["step_hash"] for step, entry in manifest["steps"].items()}

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    preview = api.plan_preview(
        RECIPE, user_cache_dir=cache, checkpoint_mode="eager", base_dir=FIXTURES
    )
    assert {s.step_id: s.step_hash for s in preview.steps} == written
    assert {s.step_id: s.held_by for s in preview.steps} == {
        "start": ["user"],
        "bump": ["user"],
    }


def test_cli_preview_base_dir(cli_runner, tmp_path, monkeypatch):
    monkeypatch.delenv("AA_RECIPE_CONFIG", raising=False)
    monkeypatch.chdir(tmp_path)
    args = ["preview", str(RECIPE), "--user-cache-dir", str(tmp_path / "cache")]
    result = cli_runner.invoke(main, [*args, "--base-dir", str(FIXTURES), "--json"])
    assert result.exit_code == 0, result.output
    steps = {s["step_id"]: s["step_hash"] for s in json.loads(result.output)["steps"]}
    assert steps["start"] == _hashes(base_dir=FIXTURES)["start"]

    text = cli_runner.invoke(main, [*args, "--base-dir", str(FIXTURES)])
    assert text.exit_code == 0, text.output
    assert steps["start"][:8] in text.output

    missing = cli_runner.invoke(main, args)
    assert missing.exit_code != 0
    assert "./data" in missing.output
