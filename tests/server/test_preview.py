# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""What a run would do, predicted by the server as the CLI predicts it."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from click.testing import CliRunner

from aa_recipe_manager import api
from aa_recipe_manager.cli import main

from .conftest import FIXTURES, HEADERS, run_events, run_session, start, wait_for

ENDED = {"completed", "failed", "cancelled", "interrupted"}


def steps(preview: dict) -> dict[str, dict]:
    return {step["step_id"]: step for step in preview["steps"]}


def test_cold_preview_computes_everything(run_client, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    response = run_client.get(f"/api/sessions/{sid}/preview")
    assert response.status_code == 200
    preview = response.json()
    assert preview["mode"] == "all"
    assert preview["tiers"] == {"user": (tmp_path / "cache").as_posix()}
    assert [step["step_id"] for step in preview["steps"]] == ["start", "bump", "scaled"]
    assert all(step["planned"] == "compute" for step in preview["steps"])
    assert all(step["held_by"] == [] for step in preview["steps"])


def test_preview_after_a_run_to_a_target(run_client, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    run = wait_for(
        run_client, start(run_client, sid, mode="to", targets=["bump"])["id"], ENDED
    )
    assert run["status"] == "completed"

    url = f"/api/sessions/{sid}/preview"
    preview = run_client.get(url, params={"targets": ["bump"]}).json()
    assert preview["mode"] == "to" and preview["targets"] == ["bump"]
    bump = steps(preview)["bump"]
    assert bump["planned"] == "load-user"
    assert bump["held_by"] == ["user"]
    assert bump["pinned"] is True
    assert steps(preview)["scaled"]["planned"] == "untargeted"

    forced = run_client.get(url, params={"targets": ["bump"], "force_steps": ["bump"]})
    assert steps(forced.json())["bump"]["planned"] == "compute"
    only = run_client.get(url, params={"targets": ["scaled"], "only": True}).json()
    assert steps(only)["bump"]["planned"] == "load-user"
    assert steps(only)["scaled"]["planned"] == "compute"


def test_cold_only_preview_is_409(run_client, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    response = run_client.get(
        f"/api/sessions/{sid}/preview", params={"targets": ["scaled"], "only": True}
    )
    assert response.status_code == 409
    assert response.json()["details"]["missing"] == ["bump"]


def test_preview_errors(run_client, tmp_path):
    sid = run_session(run_client, "flat.yaml", tmp_path / "cache")
    url = f"/api/sessions/{sid}/preview"
    assert run_client.get(url, params={"targets": ["nope"]}).status_code == 400
    assert run_client.get(url, params={"only": True}).status_code == 400
    assert (
        run_client.get("/api/sessions/20260101-000000-abcdef/preview").status_code
        == 404
    )


def cli_hashes(monkeypatch, folder, *args) -> dict[str, str]:
    """Step hashes from ``aa-recipe preview`` run in ``folder``."""
    previous = Path.cwd()
    monkeypatch.chdir(folder)
    try:
        result = CliRunner().invoke(main, ["preview", *args, "--json"])
    finally:
        monkeypatch.chdir(previous)
    assert result.exit_code == 0, result.output
    preview = json.loads(result.output[result.output.index("{") :])
    return {step["step_id"]: step["step_hash"] for step in preview["steps"]}


def hashes(preview: dict) -> dict[str, str]:
    return {step["step_id"]: step["step_hash"] for step in preview["steps"]}


def plan_hashes(config, run: dict) -> dict[str, str]:
    plan = next(e for e in run_events(config, run) if e["kind"] == "plan")
    return {name: step["hash"] for name, step in plan["data"]["steps"].items()}


def test_defaulted_inputs_hash_as_the_cli_does(
    run_client, config, tmp_path, monkeypatch
):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache")
    cache = (tmp_path / "cli_cache").as_posix()
    cli = cli_hashes(monkeypatch, FIXTURES, "slow.yaml", "--user-cache-dir", cache)
    served = run_client.get(f"/api/sessions/{sid}/preview").json()
    assert hashes(served) == cli

    run = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    assert run["status"] == "completed"
    assert plan_hashes(config, run) == cli


def test_session_input_hashes_as_a_typed_config_input(
    run_client, config, tmp_path, monkeypatch
):
    sid = run_session(run_client, "slow.yaml", tmp_path / "cache")
    response = run_client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"seconds": 1}}, headers=HEADERS
    )
    assert response.json()["inputs"]["values"]["seconds"] == 1.0
    run_config = tmp_path / "run.yaml"
    run_config.write_text("inputs:\n  seconds: 1.0\n", encoding="utf-8")
    cli = cli_hashes(
        monkeypatch,
        FIXTURES,
        "slow.yaml",
        "--config",
        run_config.as_posix(),
        "--user-cache-dir",
        (tmp_path / "cli_cache").as_posix(),
    )
    assert (
        cli["slow"]
        == api.step_hashes(FIXTURES / "slow.yaml", inputs={"seconds": 1.0})[
            "slow"
        ].step_hash
    )
    assert cli["slow"] != api.step_hashes(FIXTURES / "slow.yaml")["slow"].step_hash

    served = run_client.get(f"/api/sessions/{sid}/preview").json()
    assert hashes(served) == cli
    run = wait_for(run_client, start(run_client, sid)["id"], ENDED)
    assert run["status"] == "completed"
    assert plan_hashes(config, run) == cli


def test_relative_paths_resolve_beside_the_recipe(
    run_client, config, tmp_path, monkeypatch
):
    project = tmp_path / "recipes" / "project"
    shutil.copytree(FIXTURES / "data", project / "data")
    shutil.copy(FIXTURES / "paths.yaml", project / "paths.yaml")
    (project / "paths.config.yaml").write_text(
        "user_cache_dir: ./cache\n", encoding="utf-8"
    )
    config.recipe_roots.append(tmp_path / "recipes")
    response = run_client.post(
        "/api/sessions",
        json={"path": (project / "paths.yaml").as_posix()},
        headers=HEADERS,
    )
    assert response.status_code == 201, response.text
    sid = response.json()["id"]
    assert response.json()["run_config"]["user_cache_dir"] == "./cache"

    served = run_client.get(f"/api/sessions/{sid}/preview").json()
    assert served["tiers"] == {"user": (project / "cache").as_posix()}

    monkeypatch.chdir(project)
    result = CliRunner().invoke(main, ["preview", "paths.yaml", "--json"])
    assert result.exit_code == 0, result.output
    cli = json.loads(result.output[result.output.index("{") :])
    monkeypatch.chdir(tmp_path)
    assert {step["step_id"]: step["step_hash"] for step in served["steps"]} == {
        step["step_id"]: step["step_hash"] for step in cli["steps"]
    }

    run = start(run_client, sid, mode="to", targets=["bump"])
    run = wait_for(run_client, run["id"], ENDED)
    assert run["status"] == "completed"
    assert (project / "cache").is_dir()
    assert not (tmp_path / "cache").exists()
    plan = next(e for e in run_events(config, run) if e["kind"] == "plan")
    assert {name: step["hash"] for name, step in plan["data"]["steps"].items()} == {
        step["step_id"]: step["step_hash"] for step in served["steps"]
    }
    preview = run_client.get(
        f"/api/sessions/{sid}/preview", params={"targets": ["bump"]}
    ).json()
    assert steps(preview)["bump"]["planned"] == "load-user"
