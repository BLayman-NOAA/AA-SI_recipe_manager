# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Server settings from flags, server.yaml and the environment."""

from __future__ import annotations

from pathlib import Path

import pytest

from aa_recipe_manager.server.config import ENV_VAR, ServerConfig


def test_flags_win_over_server_yaml(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / "server.yaml").write_text(
        "port: 9000\nrecipe_roots: [recipes]\ndocs_url: http://docs/\n",
        encoding="utf-8",
    )
    config = ServerConfig.load(state, port=9100, recipe_roots=[], docs_url=None)
    assert config.port == 9100
    assert config.recipe_roots == [(state / "recipes").resolve()]
    assert config.docs_url == "http://docs/"
    assert config.state_dir == state.resolve()


def test_unknown_server_yaml_key_is_an_error(tmp_path):
    (tmp_path / "server.yaml").write_text("dev: true\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown key"):
        ServerConfig.load(tmp_path)


def test_environment_round_trip(tmp_path, monkeypatch):
    config = ServerConfig(
        state_dir=tmp_path,
        recipe_roots=[tmp_path / "a"],
        run_config=tmp_path / "run.yaml",
        dev=True,
    )
    for key, value in config.to_env().items():
        monkeypatch.setenv(key, value)
    assert ServerConfig.from_env() == config
    monkeypatch.delenv(ENV_VAR)
    assert ServerConfig.from_env().port == 8765


def test_allowed_hosts_and_origins():
    config = ServerConfig(port=8000)
    assert config.allowed_hosts() == {
        "localhost:8000",
        "127.0.0.1:8000",
        "[::1]:8000",
    }
    assert "http://localhost:5173" not in config.allowed_origins()
    assert "http://127.0.0.1:8000" in config.allowed_origins()
    dev = ServerConfig(host="10.0.0.5", port=8000, dev=True)
    assert "10.0.0.5:8000" in dev.allowed_hosts()
    assert "http://localhost:5173" in dev.allowed_origins()
    assert Path(dev.state_dir).name == "client"


def test_docs_url_always_ends_in_a_slash(tmp_path):
    config = ServerConfig.load(tmp_path, docs_url="https://docs.example/site")
    assert config.docs_url == "https://docs.example/site/"
    assert ServerConfig(docs_url="https://docs.example/").docs_url == (
        "https://docs.example/"
    )


@pytest.mark.parametrize(
    "text",
    [
        "recipe_roots: recipes\n",
        "data_roots: [1]\n",
        "host: 5\n",
        "docs_url: [a]\n",
        "run_config: 3\n",
        "port: '8000'\n",
        "port: true\n",
    ],
)
def test_server_yaml_values_are_type_checked(tmp_path, text):
    (tmp_path / "server.yaml").write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="must be"):
        ServerConfig.load(tmp_path)


def test_empty_recipe_roots_are_refused(tmp_path):
    (tmp_path / "server.yaml").write_text("recipe_roots: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="recipe root"):
        ServerConfig.load(tmp_path)
