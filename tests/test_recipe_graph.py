# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the flow-diagram graph built from a recipe or sub-recipe."""

from __future__ import annotations

import json

import pytest

from aa_recipe_manager.docs.recipe_graph import build_recipe_graph
from aa_recipe_manager.exceptions import SpecNotFoundError
from aa_recipe_manager.model.types import (
    CustomSpec,
    InputDeclaration,
    OutputDeclaration,
    ParamDeclaration,
    PortDeclaration,
)
from aa_recipe_manager.registry.registry import Registry
from conftest import make_recipe, make_spec, make_step


def port(type_: str, **kwargs) -> PortDeclaration:
    return PortDeclaration(type=type_, **kwargs)


@pytest.fixture
def registry() -> Registry:
    registry = Registry()
    registry.register_spec(
        make_spec(
            op="read",
            outputs={"stores": port("list")},
            params={
                "paths": ParamDeclaration(type="list"),
                "folder": ParamDeclaration(type="path", required=False),
            },
        )
    )
    registry.register_spec(
        make_spec(
            op="combine",
            inputs={"stores": port("list")},
            outputs={
                "echodata": port("EchoData"),
                "scratch": port("path", disposable=True),
            },
        )
    )
    registry.register_spec(
        make_spec(
            op="sv",
            inputs={"echodata": port("EchoData")},
            outputs={"ds_Sv": port("Dataset"), "log": port("dict")},
            params={"bin": ParamDeclaration(type="str", units="s", required=False)},
        )
    )
    registry.register_spec(
        make_spec(
            op="merge",
            inputs={"datasets": port("Dataset", many=True)},
            outputs={"merged": port("Dataset")},
        )
    )
    registry.register_spec(
        make_spec(op="plot", sink=True, inputs={"ds": port("Dataset")})
    )
    return registry


def graph(recipe, registry):
    return build_recipe_graph(recipe, registry, slug="t", title="T", source="t.yaml")


def nodes(g, kind=None):
    return {n["id"]: n for n in g["nodes"] if kind is None or n["kind"] == kind}


def edges(g, kind=None):
    return [e for e in g["edges"] if kind is None or e["kind"] == kind]


def test_input_ref_inside_a_string_becomes_an_entry_node(registry):
    recipe = make_recipe(
        inputs={"root": InputDeclaration(type="path", default="gs://b")},
        steps=[make_step(id="r", op="read", params={"folder": "${inputs.root}/raw"})],
    )
    g = graph(recipe, registry)
    entry = nodes(g)["in:root"]
    assert entry["kind"] == "input"
    assert entry["type"] == "path"
    assert entry["default"] == "gs://b"
    [edge] = edges(g, "param")
    assert (edge["source"], edge["target"], edge["label"]) == (
        "in:root",
        "r",
        "root -> folder",
    )


def test_reference_to_a_step_outside_the_file_is_external(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="s", op="sv", inputs={"echodata": "${combine_raw.echodata}"})
        ]
    )
    g = graph(recipe, registry)
    entry = nodes(g)["ext:combine_raw.echodata"]
    assert entry["kind"] == "external"
    assert entry["type"] == "EchoData"
    [edge] = edges(g, "data")
    assert edge["source"] == "ext:combine_raw.echodata"
    assert edge["label"] == "echodata"


def test_consecutive_mapped_steps_form_one_group(registry):
    recipe = make_recipe(
        steps=[
            make_step(
                id="r",
                op="read",
                map_over="${inputs.files}",
                params={"paths": ["${_item}"]},
            ),
            make_step(
                id="c",
                op="combine",
                map_over="${inputs.files}",
                inputs={"stores": "${r.stores}"},
            ),
        ]
    )
    g = graph(recipe, registry)
    [group] = g["groups"]
    assert group["members"] == ["r", "c"]
    assert group["source"] == "in:files"
    assert nodes(g)["r"]["group"] == group["id"]

    # The first step reads the item, so one edge carries the whole story.
    [map_edge] = edges(g, "map_over")
    assert (map_edge["source"], map_edge["target"], map_edge["group"]) == (
        "in:files",
        "r",
        group["id"],
    )
    assert map_edge["label"] == "files -> _item -> paths"
    assert edges(g, "item") == []
    # The list a group maps over stays outside it.
    assert "group" not in nodes(g)["in:files"]


def test_item_read_by_a_later_member_gets_its_own_edge(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="r", op="read", map_over="${inputs.files}"),
            make_step(
                id="r2",
                op="read",
                map_over="${inputs.files}",
                params={"paths": ["${_item}"]},
            ),
        ]
    )
    g = graph(recipe, registry)
    assert edges(g, "map_over")[0]["label"] == "files -> _item"
    [item] = edges(g, "item")
    assert (item["source"], item["target"], item["label"]) == (
        "in:files",
        "r2",
        "_item -> paths",
    )


def test_inputs_and_outputs_of_a_group_are_placed_inside_it(registry):
    recipe = make_recipe(
        steps=[
            make_step(
                id="s",
                op="sv",
                map_over="${inputs.files}",
                params={"bin": "${inputs.bin}"},
            ),
            make_step(id="p", op="sv", params={"bin": "${inputs.shared}"}),
            make_step(
                id="s2",
                op="sv",
                map_over="${inputs.other}",
                params={"bin": "${inputs.shared}"},
            ),
        ]
    )
    g = graph(recipe, registry)
    by_id = nodes(g)
    assert by_id["in:bin"]["group"] == "group:0"
    assert "group" not in by_id["in:shared"]  # read inside and outside groups
    assert by_id["out:s.ds_Sv"]["group"] == "group:0"
    assert "group" not in by_id["out:p.ds_Sv"]
    assert all("edge" not in group for group in g["groups"])


def test_mapped_steps_split_by_another_step_form_two_groups(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="r", op="read", map_over="${inputs.files}"),
            make_step(id="x", op="read"),
            make_step(id="r2", op="read", map_over="${inputs.files}"),
        ]
    )
    assert [g["members"] for g in graph(recipe, registry)["groups"]] == [["r"], ["r2"]]


def test_collect_edge_folds_the_matching_input(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="s", op="sv", map_over="${inputs.files}"),
            make_step(
                id="m",
                op="merge",
                collect="${s.ds_Sv}",
                inputs={"datasets": "${s.ds_Sv}"},
            ),
        ]
    )
    g = graph(recipe, registry)
    into_merge = [e for e in g["edges"] if e["target"] == "m"]
    assert [(e["kind"], e["target_input"]) for e in into_merge] == [
        ("collect", "datasets")
    ]
    assert nodes(g)["m"]["collector"] is True


def test_depends_on_adds_an_unlabelled_edge(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="a", op="read"),
            make_step(id="b", op="read", depends_on=["a"]),
        ]
    )
    [edge] = edges(graph(recipe, registry), "depends")
    assert (edge["source"], edge["target"], edge["label"]) == ("a", "b", "")


def test_exits_cover_declared_and_unconsumed_outputs(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="r", op="read"),
            make_step(id="c", op="combine", inputs={"stores": "${r.stores}"}),
            make_step(id="s", op="sv", inputs={"echodata": "${c.echodata}"}),
            make_step(id="p", op="plot", inputs={"ds": "${s.ds_Sv}"}),
        ],
        outputs={"sv": OutputDeclaration(step_id="s", output_name="ds_Sv")},
    )
    g = graph(recipe, registry)
    exits = nodes(g, "output")
    # Declared output first; s.log is unconsumed; c.scratch is disposable;
    # the plot step is a sink.
    assert list(exits) == ["out:sv", "out:s.log"]
    assert exits["out:sv"]["declared"] is True
    assert exits["out:s.log"]["declared"] is False
    assert {(e["source"], e["target"]) for e in edges(g, "exit")} == {
        ("s", "out:sv"),
        ("s", "out:s.log"),
    }


def test_dispose_outputs_hides_an_exit(registry):
    recipe = make_recipe(
        steps=[make_step(id="s", op="sv", checkpoint="never", dispose_outputs=["log"])]
    )
    assert list(nodes(graph(recipe, registry), "output")) == ["out:s.ds_Sv"]


def test_list_input_yields_one_edge_per_element(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="a", op="sv"),
            make_step(id="b", op="sv"),
            make_step(
                id="m", op="merge", inputs={"datasets": ["${a.ds_Sv}", "${b.ds_Sv}"]}
            ),
        ]
    )
    sources = [e["source"] for e in edges(graph(recipe, registry), "data")]
    assert sources == ["a", "b"]


def test_step_node_lists_ports_and_params_as_written(registry):
    recipe = make_recipe(steps=[make_step(id="s", op="sv", params={"bin": "10s"})])
    node = nodes(graph(recipe, registry))["s"]
    assert node["op_url"] == "ops/sv/"
    assert node["inputs"][0] == {
        "name": "echodata",
        "type": "EchoData",
        "required": True,
        "description": None,
        "value": None,
    }
    [param] = node["params"]
    assert (
        param["name"],
        param["type"],
        param["units"],
        param["value"],
        param["set"],
    ) == ("bin", "str", "s", "10s", True)
    assert [o["name"] for o in node["outputs"]] == ["ds_Sv", "log"]


def test_custom_step_types_come_from_its_inline_spec(registry):
    custom = CustomSpec(
        description="Plan dives.",
        callable_path="pkg.mod.plan",
        outputs={"windows": port("list")},
    )
    recipe = make_recipe(steps=[make_step(id="plan", op="custom", custom_spec=custom)])
    g = graph(recipe, registry)
    node = nodes(g)["plan"]
    assert node["custom"] is True
    assert node["op_url"] is None
    assert node["callable_path"] == "pkg.mod.plan"
    assert nodes(g, "output")["out:plan.windows"]["type"] == "list"


def test_custom_step_extending_an_op_links_to_it(registry):
    custom = CustomSpec(extends="sv", description="Sv, my way.", callable_path="pkg.sv")
    recipe = make_recipe(steps=[make_step(id="s", op="custom", custom_spec=custom)])
    node = nodes(graph(recipe, registry))["s"]
    assert node["op_url"] == "ops/sv/"
    assert [p["name"] for p in node["params"]] == ["bin"]


def test_custom_spec_on_a_registered_op_is_ignored_like_the_executor_does(registry):
    custom = CustomSpec(
        extends="sv", description="Unused.", callable_path="pkg.never_runs"
    )
    recipe = make_recipe(steps=[make_step(id="s", op="sv", custom_spec=custom)])
    node = nodes(graph(recipe, registry))["s"]
    assert node["custom"] is False
    assert node["callable_path"] is None
    assert node["op_url"] == "ops/sv/"


def test_unknown_op_raises(registry):
    recipe = make_recipe(steps=[make_step(id="x", op="nope")])
    with pytest.raises(SpecNotFoundError):
        graph(recipe, registry)


def test_graph_is_json_safe_and_stable(registry):
    recipe = make_recipe(
        inputs={"files": InputDeclaration(type="list")},
        steps=[
            make_step(
                id="r",
                op="read",
                map_over="${inputs.files}",
                params={"paths": ["${_item}"]},
            ),
            make_step(
                id="c",
                op="combine",
                map_over="${inputs.files}",
                inputs={"stores": "${r.stores}"},
            ),
        ],
    )
    first = graph(recipe, registry)
    assert json.loads(json.dumps(first)) == first
    assert graph(recipe, registry) == first
