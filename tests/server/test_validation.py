# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Dry run problems in the session document."""

from __future__ import annotations

from pathlib import Path

from aa_recipe_manager.exceptions import SpecNotFoundError
from aa_recipe_manager.server.errors import translate_error
from aa_recipe_manager.server.sessions import _split_problems

from .conftest import HEADERS

QUICKSTART = (
    Path(__file__).parents[2] / "docs" / "recipes" / "quickstart" / "quickstart.yaml"
)

STEP = """\
  - id: {id}
    op: custom
    params: {params}
    custom_spec:
      description: Return the constant 7.
      callable_path: _stage9_helpers.const7
      params:
        needed: {{type: int}}
      dependency: {{name: pytest, version: ">=7.0", source: pypi}}
"""


def write_recipe(folder, name, inputs, steps):
    path = folder / name
    text = (
        "recipe: {name: checks, version: '1.0', schema_version: '1'}\n"
        f"inputs:\n{inputs}\n"
        "steps:\n" + "".join(STEP.format(**step) for step in steps)
    )
    path.write_text(text, encoding="utf-8")
    return path


def open_path(client, path):
    response = client.post(
        "/api/sessions", json={"path": path.as_posix()}, headers=HEADERS
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_missing_required_param_is_an_error_on_its_step(client, config, tmp_path):
    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    path = write_recipe(
        root,
        "missing.yaml",
        "  size: {type: int, default: 1}",
        [
            {"id": "fine", "params": '{needed: "${inputs.size}"}'},
            {"id": "broken", "params": "{}"},
        ],
    )
    validation = open_path(client, path)["validation"]
    assert validation["valid"] is False
    errors = [p for p in validation["problems"] if p["severity"] == "error"]
    assert [p["step"] for p in errors] == ["broken"]
    assert errors[0]["message"] == "required param 'needed' is not provided."


def test_an_input_feeding_a_required_param_with_no_value_refuses_a_run(
    client, config, tmp_path
):
    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    path = write_recipe(
        root,
        "unset.yaml",
        "  size: {type: int}\n  window: {type: str}",
        [{"id": "fine", "params": '{needed: "${inputs.size}"}'}],
    )
    session = open_path(client, path)
    sid = session["id"]
    fields = {f["name"]: f for f in session["inputs"]["declared"]}
    assert (fields["size"]["source"], fields["size"]["required"]) == ("missing", True)
    assert (fields["window"]["source"], fields["window"]["required"]) == (
        "unset",
        False,
    )
    validation = session["validation"]
    assert validation["valid"] is False
    (error,) = [p for p in validation["problems"] if p["severity"] == "error"]
    assert error["message"].startswith(
        "step 'fine' param 'needed' needs recipe input 'size', which has no value"
    )

    response = client.post(f"/api/sessions/{sid}/runs", json={}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"] == "InvalidRecipe"
    assert error in response.json()["details"]

    response = client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"size": 2}}, headers=HEADERS
    )
    assert response.json()["validation"]["valid"] is True


def test_an_input_with_no_default_feeding_an_optional_param_is_unset(
    client, config, tmp_path
):
    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    path = tmp_path / "recipes" / "window.yaml"
    path.write_text(
        "recipe: {name: window, version: '1.0', schema_version: '1'}\n"
        "inputs:\n  time_min: {type: str}\n"
        "steps:\n"
        "  - id: plot\n"
        "    op: custom\n"
        "    params: {time_min: '${inputs.time_min}'}\n"
        "    custom_spec:\n"
        "      description: Return the constant 7.\n"
        "      callable_path: _stage9_helpers.const7\n"
        "      params:\n"
        "        time_min: {type: str, required: false}\n"
        "      dependency: {name: pytest, version: '>=7.0', source: pypi}\n",
        encoding="utf-8",
    )
    session = open_path(client, path)
    (field,) = session["inputs"]["declared"]
    assert (field["source"], field["required"], field["value"]) == (
        "unset",
        False,
        None,
    )
    assert session["validation"]["valid"] is True, session["validation"]


def test_an_explicitly_required_input_with_no_value_refuses_a_run(
    client, config, tmp_path
):
    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    path = write_recipe(
        root,
        "unset.yaml",
        "  size: {type: float, required: true}\n  window: {type: str, default: null}",
        [{"id": "fine", "params": "{needed: 1}"}],
    )
    session = open_path(client, path)
    sid = session["id"]
    fields = {f["name"]: f for f in session["inputs"]["declared"]}
    assert (fields["size"]["source"], fields["size"]["required"]) == ("missing", True)
    assert fields["window"]["source"] == "unset"
    validation = session["validation"]
    assert validation["valid"] is False
    (error,) = [p for p in validation["problems"] if p["severity"] == "error"]
    assert error["message"].startswith("Recipe input 'size' is required and has no")

    response = client.post(f"/api/sessions/{sid}/runs", json={}, headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"] == "InvalidRecipe"
    assert error in response.json()["details"]

    response = client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"size": None}}, headers=HEADERS
    )
    assert response.status_code == 200, response.text
    messages = [p["message"] for p in response.json()["validation"]["problems"]]
    assert any(
        "'size' is required, and the inputs given set it to null" in m for m in messages
    )

    response = client.put(
        f"/api/sessions/{sid}/inputs", json={"values": {"size": 2}}, headers=HEADERS
    )
    assert response.json()["validation"]["valid"] is True


def test_warn_if_set_input_is_a_warning(client, config, tmp_path):
    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    path = write_recipe(
        root,
        "window.yaml",
        "  size:\n    type: int\n    default: 1\n    warn_if_set: Debugging only.",
        [{"id": "fine", "params": '{needed: "${inputs.size}"}'}],
    )
    validation = open_path(client, path)["validation"]
    assert validation["valid"] is True
    (warning,) = validation["problems"]
    assert warning["severity"] == "warning"
    assert "Debugging only." in warning["message"]
    assert warning["step"] is None


def test_split_problems_reads_the_validation_error_text():
    text = (
        "2 validation error(s):\n"
        "  - Step 'a': first\n"
        "  - second\n"
        "    continued\n"
        "1 warning(s):\n"
        "  - Step 'b': careful"
    )
    problems = _split_problems(text, "error")
    assert problems == [
        {"severity": "error", "message": "first", "step": "a"},
        {"severity": "error", "message": "second\n    continued", "step": None},
        {"severity": "warning", "message": "careful", "step": "b"},
    ]
    assert _split_problems("plain text", "error") == [
        {"severity": "error", "message": "plain text", "step": None}
    ]


def test_missing_relative_path_names_the_recipe_folder(client, config, tmp_path):
    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    path = root / "paths.yaml"
    path.write_text(
        "recipe: {name: paths, version: '1.0', schema_version: '1'}\n"
        "steps:\n"
        "  - id: read\n"
        "    op: custom\n"
        "    params: {folder: ./Auxiliary}\n"
        "    custom_spec:\n"
        "      description: Read a folder.\n"
        "      callable_path: _stage9_helpers.const7\n"
        "      params: {folder: {type: path}}\n",
        encoding="utf-8",
    )
    (problem,) = open_path(client, path)["validation"]["problems"]
    assert problem["step"] == "read"
    assert problem["message"] == (
        "path param 'folder' does not exist: ./Auxiliary "
        f"(relative to {root.resolve().as_posix()})"
    )


def test_unknown_op_opens_with_a_problem_on_its_step(client, config, tmp_path):
    root = tmp_path / "recipes"
    root.mkdir()
    config.recipe_roots.append(root)
    text = QUICKSTART.read_text(encoding="utf-8")
    path = root / "quickstart.yaml"
    path.write_text(text.replace("op: compute_sv", "op: compute_svv"))
    session = open_path(client, path)
    step = next(n for n in session["graph"]["nodes"] if n["id"] == "compute_sv")
    assert step["op"] == "compute_svv"
    assert step["description"] == "Unknown op 'compute_svv'."
    assert session["validation"]["valid"] is False
    expected = {
        "severity": "error",
        "message": "unknown op 'compute_svv'.",
        "step": "compute_sv",
    }
    assert expected in session["validation"]["problems"]


def test_unknown_op_error_maps_to_422_with_the_step():
    status, body = translate_error(SpecNotFoundError("Step 'x': unknown op 'y'."))
    assert status == 422
    assert (body["error"], body["step_id"]) == ("SpecNotFoundError", "x")
