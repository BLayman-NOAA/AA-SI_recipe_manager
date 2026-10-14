# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the documentation site's recipes and generated data."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from aa_recipe_manager import api
from aa_recipe_manager.docs.notebook_slice import (
    _placeholder_paths,
    cells_to_markdown,
    restore_placeholders,
    slice_steps,
)
from aa_recipe_manager.docs.site_data import (
    build_site_data,
    check_site_data,
    load_manifest,
)
from aa_recipe_manager.exceptions import SpecNotFoundError
from aa_recipe_manager.graph import build_recipe_graph
from aa_recipe_manager.parallel import group_mapped_chains
from aa_recipe_manager.parser.dag_builder import build_dag
from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry.loader import load_builtin_registry

REPO = Path(__file__).parent.parent
DOCS = REPO / "docs"
MANIFEST = DOCS / "site_recipes.yaml"
PIPELINE_EXAMPLES = REPO.parent / "AA-SI_Full_Pipeline_Example" / "example_recipes"

ENTRIES = load_manifest(MANIFEST)
PARENTS = [entry for entry in ENTRIES if entry.parent is None]


@pytest.fixture(scope="module")
def registry():
    return load_builtin_registry()


def validated_dag(path: Path, registry):
    """Build a recipe's DAG the way the site does, without touching local paths."""
    recipe, inputs, _ = _placeholder_paths(load_recipe(path), registry)
    return build_dag(recipe, registry, input_values=inputs, check_versions=False)


# ---------------------------------------------------------------------------
# Manifest and documented recipes
# ---------------------------------------------------------------------------


def test_every_manifest_page_embeds_its_recipes():
    for entry in ENTRIES:
        page = (DOCS / entry.page).read_text(encoding="utf-8")
        assert f'--8<-- "data/partials/{entry.slug}.md"' in page, entry.slug


def write_manifest(tmp_path, entries):
    """A manifest and placeholder files for a list of entry dicts."""
    import yaml

    for entry in entries:
        for key in ("path", "page"):
            target = tmp_path / entry[key]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("", encoding="utf-8")
    manifest = tmp_path / "site_recipes.yaml"
    manifest.write_text(yaml.safe_dump({"recipes": entries}), encoding="utf-8")
    return manifest


def test_manifest_rejects_a_parent_that_is_itself_included(tmp_path):
    entries = [
        {"slug": "a", "path": "a.yaml", "title": "A", "page": "a.md"},
        {"slug": "b", "path": "b.yaml", "title": "B", "page": "b.md", "parent": "a"},
        {"slug": "c", "path": "c.yaml", "title": "C", "page": "c.md", "parent": "b"},
    ]
    with pytest.raises(ValueError, match="'c': parent 'b' is itself included by 'a'"):
        load_manifest(write_manifest(tmp_path, entries))


def test_manifest_requires_a_page(tmp_path):
    entries = [{"slug": "a", "path": "a.yaml", "title": "A", "page": "a.md"}]
    manifest = write_manifest(tmp_path, entries)
    manifest.write_text(
        "recipes:\n  - {slug: a, path: a.yaml, title: A}\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="page"):
        load_manifest(manifest)


@pytest.mark.parametrize("entry", ENTRIES, ids=lambda e: e.slug)
def test_every_documented_recipe_builds_a_graph(entry, registry):
    graph = build_recipe_graph(
        load_recipe(DOCS / entry.path),
        registry,
        slug=entry.slug,
        title=entry.title,
        source=entry.path,
    )
    assert any(node["kind"] == "step" for node in graph["nodes"])


@pytest.mark.parametrize("entry", PARENTS, ids=lambda e: e.slug)
def test_every_full_recipe_validates(entry, registry):
    validated_dag(DOCS / entry.path, registry)


@pytest.mark.parametrize("entry", PARENTS, ids=lambda e: e.slug)
def test_diagram_groups_match_the_executor(entry, registry):
    """Groups drawn in recipe order are the chains the executor runs."""
    dag = validated_dag(DOCS / entry.path, registry)
    chains = sorted(c.member_ids for c in group_mapped_chains(dag) if c.source_ref)
    graph = build_recipe_graph(dag.recipe, registry, slug="x", title="x", source="x")
    assert sorted(g["members"] for g in graph["groups"]) == chains


def test_each_sub_recipe_has_notebook_cells(registry):
    from aa_recipe_manager.docs.notebook_slice import generate_parent_notebook

    recipes = {e.slug: load_recipe(DOCS / e.path) for e in PARENTS}
    notebooks = {}
    for entry in ENTRIES:
        if entry.parent is None:
            continue
        name = Path(entry.path).name
        [block] = [b for b in recipes[entry.parent].include_blocks if b.source == name]
        if entry.parent not in notebooks:
            notebooks[entry.parent] = generate_parent_notebook(
                recipes[entry.parent], registry
            )
        sliced = slice_steps(notebooks[entry.parent], block.step_ids)
        assert any(c.cell_type == "code" for c in sliced.cells), entry.slug


# The HB1603 survey tier: levels 0 to 2a and the dive plan. The docs copy
# changes level 2b on, but these must still hash as the original does, or the
# docs recipe would no longer load the shared survey cache.
HB1603_SURVEY_TIER = [
    "plan_dives",
    "query_ncei",
    "scan_raw_config",
    "record_raw_configs",
    "standardize_cal",
    "build_cal_mapping",
    "read_raw",
    "combine_raw",
    "extract_cal_params",
    "compute_sv",
    "compute_transducer_depth",
    "ep_add_depth",
    "crop_survey_range",
]


@pytest.mark.parametrize(
    "docs_path,original,shared",
    [
        (
            "recipes/HB1603/dive_analysis.yaml",
            "HB1603/UC1/dive_analysis.yaml",
            HB1603_SURVEY_TIER,
        ),
        (
            "recipes/HB2407/Herring_10-15_per_file.yaml",
            "HB2407/Herring_10-15_per_file.yaml",
            None,
        ),
    ],
)
def test_docs_recipes_share_the_originals_cache(docs_path, original, shared, registry):
    """Regrouped steps keep their cache keys: every step, or the shared tier."""
    if not (PIPELINE_EXAMPLES / original).exists():
        pytest.skip("AA-SI_Full_Pipeline_Example is not checked out beside this repo")
    hashes = []
    for path in (DOCS / docs_path, PIPELINE_EXAMPLES / original):
        recipe, inputs, _ = _placeholder_paths(load_recipe(path), registry)
        hashes.append(
            {k: v.step_hash for k, v in api.step_hashes(recipe, inputs=inputs).items()}
        )
    docs, originals = hashes
    if shared is None:
        assert docs == originals
    else:
        assert {k: docs[k] for k in shared} == {k: originals[k] for k in shared}


@pytest.mark.parametrize(
    "docs_path",
    ["recipes/HB1603/dive_analysis.yaml", "recipes/HB2407/Herring_10-15_per_file.yaml"],
)
def test_reference_include_trees_match_the_files(docs_path):
    from ruamel.yaml import YAML

    path = DOCS / docs_path
    written = YAML(typ="safe").load(path.read_text(encoding="utf-8"))["steps"]
    included = [entry["include"] for entry in written if "include" in entry]
    graph = api.recipe_graph(path)
    includes = graph["includes"]
    assert [include["source"] for include in includes] == included
    assert [include["parent"] for include in includes] == [None] * len(included)
    blocks = load_recipe(path).include_blocks
    assert [len(include["steps"]) for include in includes] == [
        len(block.step_ids) for block in blocks
    ]
    steps = {node["id"]: node for node in graph["nodes"] if node["kind"] == "step"}
    for node in steps.values():
        assert (path.parent / node["document"]).is_file(), node["id"]
    if "HB2407" in docs_path:
        assert steps["plot_sv_clean"]["include"] is None
        assert steps["plot_sv_clean"]["document"] == path.name


# ---------------------------------------------------------------------------
# Notebook slicing
# ---------------------------------------------------------------------------


def test_slice_keeps_a_shared_loop_whole():
    import nbformat

    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_markdown_cell("### Step: `a`\n**Op:** `x`"),
        nbformat.v4.new_markdown_cell("### Step: `b`\n**Op:** `y`"),
        nbformat.v4.new_code_cell("for _item in files:\n    a()\n    b()"),
        nbformat.v4.new_markdown_cell("### Step: `c`\n**Op:** `z`"),
        nbformat.v4.new_code_cell("c()"),
    ]
    sliced = slice_steps(nb, ["b"])
    assert [cell.source.splitlines()[0] for cell in sliced.cells] == [
        "### Step: `b`",
        "for _item in files:",
    ]
    assert sliced.shared_with == ["a"]
    assert cells_to_markdown(sliced.cells, heading_shift=1).startswith("#### Step: `b`")


def test_slice_accepts_any_step_id():
    import nbformat

    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_markdown_cell("### Step: `mask-seabed.v2`\n**Op:** `x`"),
        nbformat.v4.new_code_cell("mask()"),
    ]
    sliced = slice_steps(nb, ["mask-seabed.v2"])
    assert [cell.cell_type for cell in sliced.cells] == ["markdown", "code"]


def test_restore_handles_placeholders_that_prefix_each_other():
    import nbformat

    nb = nbformat.v4.new_notebook()
    nb.cells = [
        nbformat.v4.new_code_cell(
            "a = 'memory://x/literal-1'\nb = 'memory://x/literal-10'\n"
            "c = 'memory://x/out'\nd = 'memory://x/out_dir'"
        )
    ]
    restore_placeholders(
        nb,
        {
            "memory://x/literal-1": "./one",
            "memory://x/literal-10": "./ten",
            "memory://x/out": "./o",
            "memory://x/out_dir": "./od",
        },
    )
    assert nb.cells[0].source == "a = './one'\nb = './ten'\nc = './o'\nd = './od'"


def test_placeholders_never_reach_the_notebook(registry):
    from aa_recipe_manager.docs.notebook_slice import generate_parent_notebook

    recipe = load_recipe(DOCS / "recipes/HB1603/dive_analysis.yaml")
    nb = generate_parent_notebook(recipe, registry)
    text = "\n".join(cell.source for cell in nb.cells)
    assert "memory://" not in text
    assert "'./Auxiliary/JSON_Files'" in text


# ---------------------------------------------------------------------------
# Generated data
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("site_data")
    build_site_data(MANIFEST, out, resolve_sources=False)
    return out


def test_build_writes_every_file(built):
    for entry in ENTRIES:
        assert (built / "partials" / f"{entry.slug}.md").is_file()
        assert (built / "recipes" / f"{entry.slug}.json").is_file()
    assert (built / "ops.json").is_file()
    assert (built / "manifest.json").is_file()


def test_build_removes_files_for_recipes_dropped_from_the_manifest(tmp_path):
    out = tmp_path / "data"
    out.mkdir()
    for stale in ("partials/old.md", "recipes/old.json"):
        (out / stale).parent.mkdir(exist_ok=True)
        (out / stale).write_text("old\n", encoding="utf-8")
    result = build_site_data(MANIFEST, out, resolve_sources=False)
    assert sorted(p.name for p in result.removed) == ["old.json", "old.md"]
    assert not (out / "partials" / "old.md").exists()
    assert check_site_data(MANIFEST, out) == []


def test_ops_json_carries_no_machine_paths(built):
    ops = json.loads((built / "ops.json").read_text(encoding="utf-8"))["ops"]
    assert all(
        "file" not in impl["source"] for op in ops for impl in op["implementations"]
    )


def test_committed_ops_json_carries_no_machine_paths():
    ops = json.loads((DOCS / "data" / "ops.json").read_text(encoding="utf-8"))["ops"]
    for op in ops:
        for impl in op["implementations"]:
            source = impl["source"]
            assert "file" not in source, op["op"]
            # A path that names a user's home folder, on either platform.
            for value in source.values():
                if isinstance(value, str):
                    assert not value.startswith(("C:\\Users", "/home/", "/Users/")), op[
                        "op"
                    ]


def test_ops_record_where_they_are_used(built):
    ops = {
        op["op"]: op
        for op in json.loads((built / "ops.json").read_text(encoding="utf-8"))["ops"]
    }
    pages = {use["page"] for use in ops["compute_sv"]["used_by"]}
    assert "levels/level-2.md" in pages
    # Parents are skipped: their steps are listed under the sub-recipes.
    assert not any(
        use["recipe"] == "hb1603-dive-analysis" for use in ops["compute_sv"]["used_by"]
    )


def test_partial_links_are_relative_to_the_embedding_page(built):
    partial = (built / "partials" / "hb1603-level-2.md").read_text(encoding="utf-8")
    assert "(../ops/compute_sv.md)" in partial
    assert "(../recipes/HB1603/sub_recipes/processing_lvl_2_combined.yaml)" in partial


def test_fresh_build_matches_itself(built):
    assert check_site_data(MANIFEST, built) == []


def test_check_reports_a_stale_file(tmp_path):
    out = tmp_path / "data"
    build_site_data(MANIFEST, out, resolve_sources=False)
    (out / "recipes" / "quickstart.json").write_text("{}\n", encoding="utf-8")
    (out / "partials" / "gone.md").write_text("old\n", encoding="utf-8")
    problems = check_site_data(MANIFEST, out)
    assert "out of date: recipes/quickstart.json" in problems
    assert "no longer generated: partials/gone.md" in problems


def test_custom_step_requires_a_custom_spec(registry):
    from aa_recipe_manager.graph import step_spec
    from conftest import make_step

    with pytest.raises(SpecNotFoundError, match="no custom_spec"):
        step_spec(make_step(id="x", op="custom"), registry)


def test_committed_site_data_is_current():
    assert check_site_data(MANIFEST, DOCS / "data") == []


# ---------------------------------------------------------------------------
# How recipes connect
# ---------------------------------------------------------------------------


def load_graph(built, slug):
    return json.loads((built / "recipes" / f"{slug}.json").read_text(encoding="utf-8"))


def test_focus_shows_only_the_named_steps(built):
    graph = load_graph(built, "calibration-mapping")
    steps = [n["id"] for n in graph["nodes"] if n["kind"] == "step"]
    assert steps == ["build_cal_mapping"]
    upstream = {u["step"]: u for u in graph["context"]["upstream"]}
    assert upstream["standardize_cal"]["recipe"] == "calibration-standardize"
    assert upstream["standardize_cal"]["page"] == "calibration/phases/"


def test_sub_recipe_context_traces_inputs_to_other_levels(built):
    context = load_graph(built, "hb1603-level-2")["context"]
    upstream = {(u["target"], u["step"]): u for u in context["upstream"]}
    assert upstream[("in:per_file_source", "query_ncei")]["recipe"] == "hb1603-level-0"
    assert upstream[("ext:combine_raw.echodata", "combine_raw")]["page"] == (
        "levels/level-1/"
    )
    downstream = {(d["source"], d["step"]) for d in context["downstream"]}
    assert ("out:dive_remove_noise.ds_Sv", "dive_mask_sparse") in downstream
    assert context["values"]["in:surface_depth_m"] == "= 10.0"
    assert context["values"]["in:ping_time_bin"] == "(from parent input ping_time_bin)"


def test_overview_draws_one_box_per_level(built):
    graph = load_graph(built, "hb1603-dive-analysis-overview")
    boxes = {n["id"]: n for n in graph["nodes"] if n["kind"] == "level"}
    assert boxes["lvl:hb1603-level-2"]["page"] == "levels/level-2/"
    assert boxes["lvl:hb1603-level-2"]["steps"] == 13
    passed = {
        (e["source"], e["target"]): e["outputs"]
        for e in graph["edges"]
        if e["kind"] == "data"
    }
    assert passed[("lvl:hb1603-level-1", "lvl:hb1603-level-2")] == ["echodata"]
    # Steps the parent declares itself get a box of their own.
    herring = load_graph(built, "hb2407-herring-per-file-overview")
    assert "step:plot_sv_clean" in {n["id"] for n in herring["nodes"]}


def test_full_recipes_get_a_runnable_notebook(built):
    import nbformat

    for entry in PARENTS:
        nb = nbformat.read(built / "notebooks" / f"{entry.slug}.ipynb", as_version=4)
        nbformat.validate(nb)
        assert [cell.id for cell in nb.cells][:2] == ["cell-0", "cell-1"]
    assert not (built / "notebooks" / "hb1603-level-2.ipynb").exists()


def test_partials_link_to_notebooks_and_overviews(built):
    parent = (built / "partials" / "hb1603-dive-analysis.md").read_text(
        encoding="utf-8"
    )
    assert parent.startswith('=== "Levels"')
    assert 'data-recipe="hb1603-dive-analysis-overview"' in parent
    assert "(../data/notebooks/hb1603-dive-analysis.ipynb)" in parent
    child = (built / "partials" / "hb1603-level-2.md").read_text(encoding="utf-8")
    assert "(../data/notebooks/hb1603-dive-analysis.ipynb)" in child


def test_focus_naming_an_unknown_step_is_an_error(tmp_path):
    recipe = DOCS / "recipes" / "quickstart" / "quickstart.yaml"
    manifest = tmp_path / "site_recipes.yaml"
    (tmp_path / "page.md").write_text("", encoding="utf-8")
    manifest.write_text(
        "recipes:\n"
        f"  - {{slug: q, path: '{recipe.as_posix()}', title: Q, page: page.md,"
        " focus: [nope]}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="focus names unknown steps"):
        build_site_data(manifest, tmp_path / "out", resolve_sources=False)


@pytest.mark.parametrize(
    "page,url",
    [
        ("levels/level-2.md", "levels/level-2/"),
        ("calibration/index.md", "calibration/"),
        ("index.md", ""),
        (None, None),
    ],
)
def test_page_url(page, url):
    from aa_recipe_manager.docs.recipe_links import page_url

    assert page_url(page) == url
