# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the generated CLI reference page."""

from __future__ import annotations

import shlex

import click
import pytest

from aa_recipe_manager.cli import main
from aa_recipe_manager.docs.cli_reference import EXAMPLES, build_cli_reference


def leaf_commands(group: click.Group, prefix: str = "") -> dict[str, click.Command]:
    found = {}
    for name, command in group.commands.items():
        path = f"{prefix}{name}"
        if isinstance(command, click.Group):
            found.update(leaf_commands(command, f"{path} "))
        else:
            found[path] = command
    return found


COMMANDS = leaf_commands(main)


def test_every_command_has_a_section():
    page = build_cli_reference(main)
    for path in COMMANDS:
        anchor = path.replace(" ", "-")
        assert f"## {path} {{#{anchor}}}" in page
        assert f"(#{anchor})" in page


def test_every_command_has_an_example():
    assert set(EXAMPLES) == set(COMMANDS)


@pytest.mark.parametrize("path", sorted(EXAMPLES))
def test_examples_use_real_options(path):
    command = COMMANDS[path]
    known = {
        opt for param in command.params for opt in param.opts + param.secondary_opts
    }
    for example in EXAMPLES[path]:
        words = shlex.split(example)
        assert words[: 1 + len(path.split())] == ["aa-recipe", *path.split()]
        for word in words:
            if word.startswith("-"):
                assert word.split("=")[0] in known, f"{example}: unknown option {word}"


def test_excluded_commands_are_left_out():
    page = build_cli_reference(main, exclude={"serve"})
    assert "## serve" not in page
    assert "(#serve)" not in page
    assert "## run {#run}" in page


def test_page_is_stable():
    assert build_cli_reference(main) == build_cli_reference(main)
