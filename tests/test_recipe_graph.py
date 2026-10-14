# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the flow-diagram graph built from a recipe or sub-recipe."""

from __future__ import annotations

import json

import pytest

from aa_recipe_manager.exceptions import SpecNotFoundError
from aa_recipe_manager.graph import build_recipe_graph
from aa_recipe_manager.model.types import (
    CustomSpec,
    Dependency,
    Implementation,
    IncludeBlock,
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
        "many": False,
        "semantic": None,
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


def test_schema_is_2_and_includes_is_a_list(registry):
    g = graph(make_recipe(steps=[make_step(id="r", op="read")]), registry)
    assert g["schema"] == 2
    assert g["includes"] == []
    node = nodes(g)["r"]
    assert (node["include"], node["document"], node["editable"]) == (
        None,
        "t.yaml",
        False,
    )


def test_include_tree_from_include_blocks(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="x", op="read"),
            make_step(id="y", op="combine", inputs={"stores": "${x.stores}"}),
            make_step(id="z", op="sv", inputs={"echodata": "${y.echodata}"}),
        ],
        include_blocks=[
            IncludeBlock(source="b.yaml", path="sub/b.yaml", step_ids=["y"]),
            IncludeBlock(
                source="a.yaml",
                path="sub/a.yaml",
                step_ids=["x", "y"],
                overrides={"k": 1},
                bindings={"k": [{"step": "x", "slot": "params.k"}]},
            ),
        ],
    )
    g = graph(recipe, registry)
    b, a = g["includes"]
    assert a["bindings"] == {"k": [{"step": "x", "slot": "params.k"}]}
    assert b["bindings"] == {}
    assert (b["id"], b["parent"], b["position"]) == (
        "inc:sub/b.yaml",
        "inc:sub/a.yaml",
        1,
    )
    assert (a["id"], a["parent"], a["position"]) == ("inc:sub/a.yaml", None, 0)
    assert a["title"] == "a"
    assert a["overrides"] == {"k": 1}
    assert a["consumed"] == [{"step": "y", "output": "echodata", "by": ["z"]}]
    assert b["consumed"] == [{"step": "y", "output": "echodata", "by": ["z"]}]
    by_id = nodes(g)
    assert (by_id["x"]["include"], by_id["x"]["document"]) == (
        "inc:sub/a.yaml",
        "sub/a.yaml",
    )
    assert (by_id["y"]["include"], by_id["y"]["document"]) == (
        "inc:sub/b.yaml",
        "sub/b.yaml",
    )
    assert (by_id["z"]["include"], by_id["z"]["document"]) == (None, "t.yaml")


def test_wrapper_include_with_the_same_steps_is_the_parent(registry):
    recipe = make_recipe(
        steps=[make_step(id="x", op="read")],
        include_blocks=[
            IncludeBlock(source="leaf.yaml", path="leaf.yaml", step_ids=["x"]),
            IncludeBlock(source="mid.yaml", path="mid.yaml", step_ids=["x"]),
            IncludeBlock(source="top.yaml", path="top.yaml", step_ids=["x"]),
        ],
    )
    g = graph(recipe, registry)
    parents = {include["id"]: include["parent"] for include in g["includes"]}
    assert parents == {
        "inc:leaf.yaml": "inc:mid.yaml",
        "inc:mid.yaml": "inc:top.yaml",
        "inc:top.yaml": None,
    }
    assert nodes(g)["x"]["include"] == "inc:leaf.yaml"


def test_unreferenced_declared_input_gets_a_node(registry):
    recipe = make_recipe(
        steps=[make_step(id="x", op="read")],
        inputs={"unused": InputDeclaration(type="int", default=2)},
    )
    node = nodes(graph(recipe, registry))["in:unused"]
    assert (node["kind"], node["declared"], node["default"]) == ("input", True, 2)


def test_include_without_steps_in_the_recipe_is_dropped(registry):
    recipe = make_recipe(
        steps=[make_step(id="x", op="read")],
        include_blocks=[IncludeBlock(source="gone.yaml", step_ids=["focused_out"])],
    )
    assert graph(recipe, registry)["includes"] == []


def test_hashes_are_attached_when_given(registry):
    recipe = make_recipe(
        steps=[make_step(id="r", op="read"), make_step(id="s", op="sv")]
    )
    plain = nodes(graph(recipe, registry), "step")
    assert [n["hash"] for n in plain.values()] == [None, None]
    g = build_recipe_graph(
        recipe, registry, slug="t", title="T", source="t.yaml", hashes={"r": "abc"}
    )
    steps = nodes(g, "step")
    assert (steps["r"]["hash"], steps["s"]["hash"]) == ("abc", None)


def test_ports_carry_many_and_semantic(registry):
    recipe = make_recipe(
        steps=[
            make_step(id="s", op="sv"),
            make_step(
                id="m",
                op="merge",
                inputs={"datasets": "${s.ds_Sv}", "extra": "${s.log}"},
            ),
        ]
    )
    merge = nodes(graph(recipe, registry))["m"]
    ports = {p["name"]: p for p in merge["inputs"]}
    assert (ports["datasets"]["many"], ports["datasets"]["semantic"]) == (True, None)
    assert (ports["extra"]["many"], ports["extra"]["semantic"]) == (False, None)
    [out] = merge["outputs"]
    assert (out["many"], out["semantic"]) == (False, None)


def test_step_node_carries_category_and_implementation(registry):
    registry.register_spec(make_spec(op="tagged", category="processing"))
    registry.register_implementation(
        Implementation(op="tagged", key="b", callable_path="pkg.b")
    )
    registry.register_implementation(
        Implementation(
            op="tagged",
            key="a",
            callable_path="pkg.a",
            default=True,
            dependency=Dependency(name="pkg", version=">=1", source="pypi"),
        )
    )
    recipe = make_recipe(
        steps=[
            make_step(id="t", op="tagged"),
            make_step(id="r", op="read"),
            make_step(
                id="c",
                op="custom",
                custom_spec=CustomSpec(description="Mine.", callable_path="pkg.c"),
            ),
        ]
    )
    by_id = nodes(graph(recipe, registry))
    assert by_id["t"]["category"] == "processing"
    assert by_id["t"]["implementation"] == {
        "key": "a",
        "callable_path": "pkg.a",
        "dependency": "pkg",
    }
    assert by_id["r"]["implementation"] is None
    assert by_id["c"]["implementation"] is None
