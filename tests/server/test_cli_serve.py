# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The serve command and the CLI forms of the graph and catalog."""

from __future__ import annotations

import json

from aa_recipe_manager.cli import main

from .conftest import FIXTURES


def test_serve_help(cli_runner):
    result = cli_runner.invoke(main, ["serve", "--help"])
    assert result.exit_code == 0, result.output
    assert "--recipe-root" in result.output
    assert "--state-dir" in result.output


def test_serve_rejects_unknown_server_yaml_keys(cli_runner, tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "server.yaml").write_text("colour: blue\n", encoding="utf-8")
    result = cli_runner.invoke(main, ["serve", "--state-dir", str(state)])
    assert result.exit_code == 1
    assert "unknown key" in result.output


def test_graph_json(cli_runner):
    result = cli_runner.invoke(main, ["graph", str(FIXTURES / "flat.yaml"), "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["schema"] == 2


def test_ops_json(cli_runner):
    result = cli_runner.invoke(main, ["ops", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["counts"]["ops"] > 0
