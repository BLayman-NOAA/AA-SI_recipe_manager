# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the generated HTML op reference."""

from __future__ import annotations

import importlib.metadata
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.cli import main
from aa_recipe_manager.docs import sources
from aa_recipe_manager.docs.html import render_html
from aa_recipe_manager.docs.payload import build_payload
from aa_recipe_manager.registry.loader import load_builtin_registry
from aa_recipe_manager.registry.registry import Registry
from conftest import make_implementation, make_spec

_DATA_BLOCK = re.compile(
    r'<script id="op-data" type="application/json">(.*?)</script>', re.DOTALL
)


@pytest.fixture(autouse=True)
def clear_source_caches():
    """Start every test with empty memoized git and metadata lookups.

    Clearing only on the way in keeps this away from any function a test has
    monkeypatched, which is still in place when fixtures tear down.
    """
    sources.clear_caches()


@pytest.fixture(scope="module")
def payload():
    return build_payload(resolve_sources=False)


def embedded_payload(html: str) -> dict:
    """Parse the JSON block the page carries its data in."""
    match = _DATA_BLOCK.search(html)
    assert match is not None, "rendered page has no op-data block"
    return json.loads(match.group(1))


def find_op(payload: dict, name: str) -> dict:
    return next(op for op in payload["ops"] if op["op"] == name)


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def test_every_op_appears_in_payload(payload):
    names = [op["op"] for op in payload["ops"]]
    assert names == load_builtin_registry().list_ops()
    assert payload["counts"]["ops"] == len(names)


def test_payload_is_json_serializable(payload):
    assert json.loads(json.dumps(payload)) == payload


def test_payload_preserves_declaration_order(payload):
    compute_sv = find_op(payload, "compute_sv")
    assert [port["name"] for port in compute_sv["inputs"]] == [
        "echodata",
        "cal_params",
        "env_params",
    ]


def test_known_op_payload_fields(payload):
    compute_sv = find_op(payload, "compute_sv")
    assert compute_sv["category"] == "level 2"
    assert compute_sv["cache_key"] == "compute_sv"
    assert compute_sv["sink"] is False
    impl = compute_sv["implementations"][0]
    assert impl["callable_path"] == "echopype.calibrate.compute_Sv"
    assert impl["dependency"]["name"] == "echopype"
    assert impl["output_map"] == [{"spec": "ds_Sv", "expression": "__return__"}]


def test_sink_op_has_no_outputs(payload):
    assert find_op(payload, "plot_sv_echogram")["sink"] is True
    assert find_op(payload, "plot_sv_echogram")["outputs"] == []


def test_op_without_implementation_renders(payload):
    assert find_op(payload, "create_sv_mask")["implementations"] == []
    assert "create_sv_mask" in render_html(payload)


def test_untyped_param_survives(payload):
    ep_add_depth = find_op(payload, "ep_add_depth")
    params = {param["name"]: param for param in ep_add_depth["params"]}
    assert params["depth_offset"]["type"] is None


def test_search_blob_covers_name_and_description(payload):
    blob = find_op(payload, "compute_sv")["search"]
    assert "compute_sv" in blob
    assert "backscattering" in blob
    assert blob == blob.lower()


def test_disabled_sources_are_reported_as_unresolved(payload):
    for op in payload["ops"]:
        for impl in op["implementations"]:
            assert impl["source"]["resolved"] is False
            assert impl["source"]["note"]


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def test_html_is_self_contained(payload):
    html = render_html(payload)
    assert html.startswith("<!doctype html")
    assert not re.search(r"<script[^>]+\ssrc=", html)
    assert not re.search(r'<link[^>]+href="https?:', html)
    assert "fetch(" not in html


def test_html_embeds_the_payload(payload):
    assert embedded_payload(render_html(payload)) == payload


def test_payload_escaping_of_script_tag():
    registry = Registry()
    registry.register_spec(
        make_spec(
            op="hostile",
            description="closes early </script><script>alert(1)</script>",
        )
    )
    registry.register_implementation(make_implementation(op="hostile", key="default"))

    html = render_html(build_payload(registry, resolve_sources=False))
    assert html.count("<script") == 2
    assert "alert(1)" in embedded_payload(html)["ops"][0]["description"]


def test_regeneration_is_deterministic(payload):
    first = api.export_op_docs(resolve_sources=False).html
    second = api.export_op_docs(resolve_sources=False).html
    assert first == second
    # Nothing time-varying is recorded, so two builds of the same specs agree.
    assert not {"generated_at", "timestamp", "date"} & set(payload)


# ---------------------------------------------------------------------------
# Source resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "remote,expected",
    [
        ("git@github.com:owner/repo.git", "https://github.com/owner/repo"),
        ("ssh://git@github.com/owner/repo.git", "https://github.com/owner/repo"),
        ("https://github.com/owner/repo.git", "https://github.com/owner/repo"),
        ("git+https://github.com/owner/repo", "https://github.com/owner/repo"),
        ("https://gitlab.com/owner/repo.git", None),
        ("git@bitbucket.org:owner/repo.git", None),
        ("https://github.com/owner", None),
        ("", None),
        (None, None),
    ],
)
def test_github_https_normalization(remote, expected):
    assert sources._github_https(remote) == expected


def test_git_helpers_tolerate_missing_git(monkeypatch, tmp_path):
    def explode(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", explode)
    assert sources._git_toplevel(str(tmp_path)) is None
    assert sources._git_remote_url(str(tmp_path)) is None
    assert sources._git_ref(str(tmp_path)) is None


def test_resolve_source_missing_module():
    location = sources.resolve_source("definitely_not_a_module.fn")
    assert location.resolved is False
    assert location.github_url is None
    assert "ModuleNotFoundError" in location.note


def test_site_packages_never_uses_git_toplevel(monkeypatch):
    """An installed file must not be attributed to the repo the venv sits in."""
    called = []

    def fake_toplevel(directory):
        called.append(directory)
        return "C:/repo"

    monkeypatch.setattr(sources, "_git_toplevel", fake_toplevel)
    location = sources.SourceLocation(
        callable_path="pkg.fn",
        resolved=True,
        module="pkg",
        file="C:/repo/.venv/Lib/site-packages/pkg/mod.py",
        line=3,
    )
    sources._attach_repository(location, None, None)

    assert called == []
    assert location.github_url is None


def test_checkout_link_flags_uncommitted_edits(monkeypatch, tmp_path):
    monkeypatch.setattr(sources, "_git_toplevel", lambda directory: str(tmp_path))
    monkeypatch.setattr(
        sources, "_git_remote_url", lambda root: "git@github.com:owner/repo.git"
    )
    monkeypatch.setattr(sources, "_git_ref", lambda root: "main")
    monkeypatch.setattr(sources, "_git", lambda *args: " M pkg/mod.py")

    location = sources.SourceLocation(
        callable_path="pkg.fn",
        resolved=True,
        module="pkg",
        file=str(tmp_path / "pkg" / "mod.py"),
        line=12,
    )
    sources._attach_repository(location, None, None)

    assert location.note == sources.DIRTY_NOTE
    assert location.github_url == "https://github.com/owner/repo/blob/main/pkg/mod.py#L12"


def test_unresolved_location_is_json_safe():
    location = sources.unresolved("pkg.fn", "not tried")
    assert location.to_dict()["resolved"] is False
    assert json.loads(json.dumps(location.to_dict()))["note"] == "not tried"


@pytest.mark.slow
def test_source_resolution_for_editable_package():
    pytest.importorskip("aa_si_utils")
    location = sources.resolve_source(
        "aa_si_utils.utils.read_raw_files_to_stores",
        distribution="aa-si-utils",
        fallback_url="https://github.com/BLayman-NOAA/AA-SI_Utils.git",
    )
    assert location.resolved is True
    assert Path(location.file).exists()
    assert location.line > 0
    assert location.signature.startswith("read_raw_files_to_stores(")
    assert location.doc
    assert location.repo_relative_path == "src/aa_si_utils/utils.py"
    assert re.match(
        r"^https://github\.com/[^/]+/[^/]+/blob/[^/]+/.+#L\d+$", location.github_url
    )


@pytest.mark.slow
def test_source_resolution_for_installed_package():
    pytest.importorskip("echopype")
    location = sources.resolve_source(
        "echopype.calibrate.compute_Sv", distribution="echopype"
    )
    assert location.resolved is True
    assert location.repo_relative_path == "echopype/calibrate/api.py"
    assert location.ref == f"v{importlib.metadata.version('echopype')}"
    assert location.github_url.startswith(
        "https://github.com/OSOceanAcoustics/echopype/blob/"
    )


@pytest.mark.slow
def test_no_source_links_imports_nothing(tmp_path):
    script = tmp_path / "check.py"
    script.write_text(
        "import sys\n"
        "from aa_recipe_manager.docs.payload import build_payload\n"
        "build_payload(resolve_sources=False)\n"
        "assert 'echopype' not in sys.modules\n"
        "assert 'aa_si_ml' not in sys.modules\n",
        encoding="utf-8",
    )
    subprocess.run([sys.executable, str(script)], check=True)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_docs_writes_file(cli_runner, tmp_path):
    out = tmp_path / "out.html"
    result = cli_runner.invoke(main, ["docs", "--no-source-links", "-o", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text(encoding="utf-8").startswith("<!doctype html")
    assert f"{len(load_builtin_registry().list_ops())} ops" in result.output


def test_cli_docs_writes_stdout(cli_runner, tmp_path):
    with cli_runner.isolated_filesystem(temp_dir=tmp_path):
        result = cli_runner.invoke(main, ["docs", "--no-source-links", "-o", "-"])
        assert result.exit_code == 0, result.output
        assert result.output.startswith("<!doctype html")
        assert list(Path(".").iterdir()) == []
