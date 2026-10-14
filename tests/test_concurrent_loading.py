"""Loading specs and recipes from several threads at once, as the client server does."""

from __future__ import annotations

import threading
from pathlib import Path

from aa_recipe_manager.parser.yaml_reader import load_recipe
from aa_recipe_manager.registry import loader

FIXTURES = Path(__file__).parent / "fixtures" / "client_recipes"


def _run_in_threads(target, count: int = 6) -> list[str]:
    errors: list[str] = []

    def call() -> None:
        try:
            target()
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(repr(exc))

    threads = [threading.Thread(target=call) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return errors


def test_builtin_registry_builds_from_many_threads(monkeypatch):
    for _ in range(5):
        monkeypatch.setattr(loader, "_parsed_specs", {})
        assert _run_in_threads(loader.load_builtin_registry) == []
        assert all(isinstance(raw, dict) for _, raw in loader._parsed_specs.values())


def test_recipes_load_from_many_threads():
    path = FIXTURES / "with_sub.yaml"
    expected = load_recipe(path).model_dump()
    results = []

    def load() -> None:
        results.append(load_recipe(path).model_dump())

    for _ in range(5):
        assert _run_in_threads(load) == []
    assert all(result == expected for result in results)
