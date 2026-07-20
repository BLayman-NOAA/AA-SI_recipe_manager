# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the per-user run-config loader and its CLI integration."""

from __future__ import annotations

import yaml
import pytest
from click.testing import CliRunner

from aa_recipe_manager import cli, config


def _write(path, data) -> "object":
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _isolate_discovery(monkeypatch):
    """Neutralize the real environment: no env var, no ambient cwd/home config."""
    monkeypatch.delenv(config.ENV_VAR, raising=False)
    monkeypatch.setattr(config, "default_config_search_paths", lambda: [])
    yield


# ---------------------------------------------------------------------------
# discovery
# ---------------------------------------------------------------------------


def test_no_config_returns_empty():
    cfg = config.load_run_config()
    assert cfg.source is None
    assert cfg.output_dir is None
    assert cfg.temp_dir is None
    assert cfg.storage_options is None
    assert cfg.inputs == {}


def test_explicit_path(tmp_path):
    p = _write(tmp_path / "c.yaml", {"output_dir": "gs://b/cache"})
    cfg = config.load_run_config(str(p))
    assert cfg.source == p
    assert cfg.output_dir == "gs://b/cache"


def test_explicit_missing_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="Config file not found"):
        config.load_run_config(str(tmp_path / "nope.yaml"))


def test_env_var_discovery(monkeypatch, tmp_path):
    p = _write(tmp_path / "e.yaml", {"temp_dir": "gs://b/tmp"})
    monkeypatch.setenv(config.ENV_VAR, str(p))
    assert config.load_run_config().temp_dir == "gs://b/tmp"


def test_env_var_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setenv(config.ENV_VAR, str(tmp_path / "missing.yaml"))
    with pytest.raises(FileNotFoundError, match=config.ENV_VAR):
        config.discover_config_path()


def test_explicit_beats_env(monkeypatch, tmp_path):
    envp = _write(tmp_path / "env.yaml", {"output_dir": "env"})
    argp = _write(tmp_path / "arg.yaml", {"output_dir": "arg"})
    monkeypatch.setenv(config.ENV_VAR, str(envp))
    assert config.load_run_config(str(argp)).output_dir == "arg"


def test_autodiscovery_first_found_wins(monkeypatch, tmp_path):
    first = _write(tmp_path / "first.yaml", {"output_dir": "first"})
    second = _write(tmp_path / "second.yaml", {"output_dir": "second"})
    monkeypatch.setattr(
        config, "default_config_search_paths", lambda: [first, second]
    )
    assert config.load_run_config().output_dir == "first"


def test_autodiscovery_skips_missing(monkeypatch, tmp_path):
    missing = tmp_path / "missing.yaml"
    present = _write(tmp_path / "present.yaml", {"output_dir": "present"})
    monkeypatch.setattr(
        config, "default_config_search_paths", lambda: [missing, present]
    )
    assert config.load_run_config().output_dir == "present"


def test_recipe_config_candidate_path():
    candidate = config.recipe_config_candidate("example_recipes/hb1603_gcs.yaml")
    assert candidate.name == "hb1603_gcs.config.yaml"
    assert candidate.parent.name == "example_recipes"


def test_per_recipe_discovery(tmp_path):
    recipe = tmp_path / "survey_a.yaml"
    recipe.write_text("recipe: {}\n", encoding="utf-8")
    _write(tmp_path / "survey_a.config.yaml", {"output_dir": "gs://a/cache"})

    cfg = config.load_run_config(recipe_path=recipe)
    assert cfg.output_dir == "gs://a/cache"
    assert cfg.source == tmp_path / "survey_a.config.yaml"


def test_per_recipe_beats_generic_cwd(monkeypatch, tmp_path):
    generic = _write(tmp_path / "generic.yaml", {"output_dir": "generic"})
    monkeypatch.setattr(config, "default_config_search_paths", lambda: [generic])

    recipe = tmp_path / "survey_b.yaml"
    recipe.write_text("recipe: {}\n", encoding="utf-8")
    _write(tmp_path / "survey_b.config.yaml", {"output_dir": "per-recipe"})

    assert config.load_run_config(recipe_path=recipe).output_dir == "per-recipe"
    # Without a per-recipe file, the generic fallback is used.
    other = tmp_path / "survey_c.yaml"
    other.write_text("recipe: {}\n", encoding="utf-8")
    assert config.load_run_config(recipe_path=other).output_dir == "generic"


def test_env_var_beats_per_recipe(monkeypatch, tmp_path):
    envp = _write(tmp_path / "env.yaml", {"output_dir": "env"})
    monkeypatch.setenv(config.ENV_VAR, str(envp))

    recipe = tmp_path / "survey_d.yaml"
    recipe.write_text("recipe: {}\n", encoding="utf-8")
    _write(tmp_path / "survey_d.config.yaml", {"output_dir": "per-recipe"})

    assert config.load_run_config(recipe_path=recipe).output_dir == "env"


def test_two_recipes_same_dir_get_different_configs(tmp_path):
    for name, bucket in [("survey_a", "gs://a/cache"), ("survey_b", "gs://b/cache")]:
        (tmp_path / f"{name}.yaml").write_text("recipe: {}\n", encoding="utf-8")
        _write(tmp_path / f"{name}.config.yaml", {"output_dir": bucket})

    assert (
        config.load_run_config(recipe_path=tmp_path / "survey_a.yaml").output_dir
        == "gs://a/cache"
    )
    assert (
        config.load_run_config(recipe_path=tmp_path / "survey_b.yaml").output_dir
        == "gs://b/cache"
    )


def test_cwd_autodiscovery(monkeypatch, tmp_path):
    # default_config_search_paths() must actually include the cwd file.
    monkeypatch.undo()  # restore the real default_config_search_paths
    monkeypatch.delenv(config.ENV_VAR, raising=False)
    monkeypatch.chdir(tmp_path)
    _write(tmp_path / config.CONFIG_FILENAME, {"output_dir": "from-cwd"})
    assert config.load_run_config().output_dir == "from-cwd"


# ---------------------------------------------------------------------------
# loading / validation
# ---------------------------------------------------------------------------


def test_all_fields(tmp_path):
    p = _write(
        tmp_path / "c.yaml",
        {
            "output_dir": "gs://b/c",
            "temp_dir": "gs://b/t",
            "outputs_dir": "./out",
            "storage_options": {"token": "x"},
            "inputs": {"raw_input_folder": "gs://b/raw"},
        },
    )
    cfg = config.load_run_config(str(p))
    assert cfg.output_dir == "gs://b/c"
    assert cfg.temp_dir == "gs://b/t"
    assert cfg.outputs_dir == "./out"
    assert cfg.storage_options == {"token": "x"}
    assert cfg.inputs == {"raw_input_folder": "gs://b/raw"}


def test_empty_file(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("", encoding="utf-8")
    cfg = config.load_run_config(str(p))
    assert cfg.source == p
    assert cfg.output_dir is None


def test_unknown_key_raises(tmp_path):
    p = _write(tmp_path / "c.yaml", {"outupt_dir": "typo"})
    with pytest.raises(ValueError, match="unknown key"):
        config.load_run_config(str(p))


def test_non_mapping_top_level_raises(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ValueError, match="mapping at the top level"):
        config.load_run_config(str(p))


def test_bad_output_dir_type_raises(tmp_path):
    p = _write(tmp_path / "c.yaml", {"output_dir": ["a"]})
    with pytest.raises(ValueError, match="output_dir"):
        config.load_run_config(str(p))


def test_bad_storage_options_type_raises(tmp_path):
    p = _write(tmp_path / "c.yaml", {"storage_options": "nope"})
    with pytest.raises(ValueError, match="storage_options"):
        config.load_run_config(str(p))


def test_bad_inputs_type_raises(tmp_path):
    p = _write(tmp_path / "c.yaml", {"inputs": "nope"})
    with pytest.raises(ValueError, match="inputs"):
        config.load_run_config(str(p))


# ---------------------------------------------------------------------------
# CLI integration (execute stubbed to capture resolved kwargs)
# ---------------------------------------------------------------------------


class _FakeResult:
    executed_steps: list = []
    skipped_steps: list = []
    step_dispositions: dict = {}
    output_dir = None
    outputs_dir = None
    log_file = None
    manifest_file = None
    console_log = ""


def _stub_execute(monkeypatch) -> dict:
    captured: dict = {}

    def fake_execute(recipe, **kwargs):
        captured["recipe"] = recipe
        captured.update(kwargs)
        return _FakeResult()

    monkeypatch.setattr(cli.api, "execute", fake_execute)
    return captured


def _dummy_recipe(tmp_path):
    p = tmp_path / "recipe.yaml"
    p.write_text("recipe: {}\n", encoding="utf-8")
    return p


def test_cli_config_supplies_dirs_and_inputs(monkeypatch, tmp_path):
    captured = _stub_execute(monkeypatch)
    cfg = _write(
        tmp_path / "run.yaml",
        {
            "output_dir": "gs://b/cache",
            "temp_dir": "gs://b/tmp",
            "outputs_dir": "./outputs",
            "storage_options": {"token": "x"},
            "inputs": {"raw_input_folder": "gs://b/raw"},
        },
    )
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(
        cli.main, ["run", str(recipe), "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == "gs://b/cache"
    assert captured["temp_dir"] == "gs://b/tmp"
    assert captured["outputs_dir"] == "./outputs"
    assert captured["storage_options"] == {"token": "x"}
    assert captured["inputs"] == {"raw_input_folder": "gs://b/raw"}
    assert "Using run config" in result.output


def test_cli_flags_override_config(monkeypatch, tmp_path):
    captured = _stub_execute(monkeypatch)
    cfg = _write(
        tmp_path / "run.yaml",
        {"output_dir": "gs://b/cache", "inputs": {"raw_input_folder": "gs://b/raw"}},
    )
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(
        cli.main,
        [
            "run",
            str(recipe),
            "--config",
            str(cfg),
            "--output-dir",
            "./local_cache",
            "--input",
            "raw_input_folder=gs://other/raw",
        ],
    )
    assert result.exit_code == 0, result.output
    # CLI flag wins over config output_dir; CLI --input wins over config input.
    assert captured["output_dir"] == "./local_cache"
    assert captured["inputs"] == {"raw_input_folder": "gs://other/raw"}


def test_cli_no_config_uses_builtin_default(monkeypatch, tmp_path):
    captured = _stub_execute(monkeypatch)
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(cli.main, ["run", str(recipe)])
    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == "./recipe_cache"
    assert captured["outputs_dir"] is None
    assert captured["temp_dir"] is None
    assert captured["storage_options"] is None
    assert captured["inputs"] is None
    assert "Using run config" not in result.output


def test_cli_per_recipe_config_autodiscovered(monkeypatch, tmp_path):
    captured = _stub_execute(monkeypatch)
    recipe = _dummy_recipe(tmp_path)  # recipe.yaml
    _write(tmp_path / "recipe.config.yaml", {"output_dir": "gs://mine/cache"})

    result = CliRunner().invoke(cli.main, ["run", str(recipe)])
    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == "gs://mine/cache"
    assert "Using run config" in result.output
    assert "recipe.config.yaml" in result.output


def test_cli_missing_config_errors(monkeypatch, tmp_path):
    _stub_execute(monkeypatch)
    recipe = _dummy_recipe(tmp_path)
    result = CliRunner().invoke(
        cli.main, ["run", str(recipe), "--config", str(tmp_path / "nope.yaml")]
    )
    assert result.exit_code != 0


# ---------------------------------------------------------------------------
# Tiered cache plumbing: survey_cache_dir + --cache-write-tier
# ---------------------------------------------------------------------------


def test_survey_cache_dir_parsed_from_config(tmp_path):
    p = _write(
        tmp_path / "c.yaml",
        {"output_dir": "gs://b/users/me/cache", "survey_cache_dir": "gs://b/surveys/HB1603/cache"},
    )
    cfg = config.load_run_config(str(p))
    assert cfg.survey_cache_dir == "gs://b/surveys/HB1603/cache"


def test_survey_cache_dir_must_be_string(tmp_path):
    p = _write(tmp_path / "c.yaml", {"survey_cache_dir": ["not", "a", "string"]})
    with pytest.raises(ValueError, match="survey_cache_dir"):
        config.load_run_config(str(p))


def test_cli_survey_cache_dir_from_config(monkeypatch, tmp_path):
    captured = _stub_execute(monkeypatch)
    cfg = _write(
        tmp_path / "run.yaml",
        {"output_dir": "gs://b/cache", "survey_cache_dir": "gs://b/surveys/X/cache"},
    )
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(cli.main, ["run", str(recipe), "--config", str(cfg)])
    assert result.exit_code == 0, result.output
    assert captured["survey_cache_dir"] == "gs://b/surveys/X/cache"
    assert captured["cache_write_tier"] == "user"  # default


def test_cli_survey_flag_overrides_config(monkeypatch, tmp_path):
    captured = _stub_execute(monkeypatch)
    cfg = _write(
        tmp_path / "run.yaml",
        {"output_dir": "gs://b/cache", "survey_cache_dir": "gs://b/surveys/X/cache"},
    )
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(
        cli.main,
        [
            "run",
            str(recipe),
            "--config",
            str(cfg),
            "--survey-cache-dir",
            "gs://b/surveys/Y/cache",
        ],
    )
    assert result.exit_code == 0, result.output
    assert captured["survey_cache_dir"] == "gs://b/surveys/Y/cache"


def test_cli_cache_write_tier_forwarded(monkeypatch, tmp_path):
    captured = _stub_execute(monkeypatch)
    cfg = _write(
        tmp_path / "run.yaml",
        {"output_dir": "gs://b/cache", "survey_cache_dir": "gs://b/surveys/X/cache"},
    )
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(
        cli.main,
        ["run", str(recipe), "--config", str(cfg), "--cache-write-tier", "survey"],
    )
    assert result.exit_code == 0, result.output
    assert captured["cache_write_tier"] == "survey"


def test_cli_write_tier_survey_without_dir_fails(monkeypatch, tmp_path):
    _stub_execute(monkeypatch)
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(
        cli.main, ["run", str(recipe), "--cache-write-tier", "survey"]
    )
    assert result.exit_code != 0
    assert "survey cache root" in result.output


def test_cli_clean_honors_config(monkeypatch, tmp_path):
    """clean resolves output_dir and storage_options from the run config."""
    captured: dict = {}

    def fake_clean(recipe, output_dir, **kwargs):
        captured["output_dir"] = output_dir
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli.api, "clean", fake_clean)
    cfg = _write(
        tmp_path / "run.yaml",
        {"output_dir": "gs://b/users/me/cache", "storage_options": {"token": "x"}},
    )
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(
        cli.main, ["clean", str(recipe), "--config", str(cfg)]
    )
    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == "gs://b/users/me/cache"
    assert captured["storage_options"] == {"token": "x"}


def test_cli_clean_flag_overrides_config(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_clean(recipe, output_dir, **kwargs):
        captured["output_dir"] = output_dir
        captured.update(kwargs)
        return []

    monkeypatch.setattr(cli.api, "clean", fake_clean)
    cfg = _write(tmp_path / "run.yaml", {"output_dir": "gs://b/users/me/cache"})
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(
        cli.main,
        ["clean", str(recipe), "--config", str(cfg), "--output-dir", "./local"],
    )
    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == "./local"


def test_cli_clean_default_without_config(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_clean(recipe, output_dir, **kwargs):
        captured["output_dir"] = output_dir
        return []

    monkeypatch.setattr(cli.api, "clean", fake_clean)
    recipe = _dummy_recipe(tmp_path)

    result = CliRunner().invoke(cli.main, ["clean", str(recipe)])
    assert result.exit_code == 0, result.output
    assert captured["output_dir"] == "./recipe_cache"
