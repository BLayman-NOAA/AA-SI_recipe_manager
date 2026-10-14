# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Settings for the client server, from flags and ``<state_dir>/server.yaml``."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML

ENV_VAR = "AA_RECIPE_SERVER_CONFIG"
DEFAULT_STATE_DIR = Path.home() / ".aa-recipe" / "client"
FILE_KEYS = {"recipe_roots", "data_roots", "port", "host", "run_config", "docs_url"}
LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")
VITE_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


@dataclass
class ServerConfig:
    """Everything the server needs to know about where it runs and what it reads.

    Attributes:
        host: Interface to bind.
        port: Port to bind.
        state_dir: Folder for sessions and ``server.yaml``.
        recipe_roots: Folders whose recipes can be listed and opened.
        data_roots: Folders the file browser may show (unused until M7).
        run_config: An explicit ``aa-recipe`` run config file, as ``--config``.
        docs_url: Base URL of the documentation site.
        dev: Accept the Vite dev server origin and reload on code changes.
    """

    host: str = "127.0.0.1"
    port: int = 8765
    state_dir: Path = DEFAULT_STATE_DIR
    recipe_roots: list[Path] = field(default_factory=lambda: [Path.cwd()])
    data_roots: list[Path] = field(default_factory=lambda: [Path.home()])
    run_config: Path | None = None
    docs_url: str = "https://blayman-noaa.github.io/AA-SI_recipe_manager/"
    dev: bool = False

    def __post_init__(self) -> None:
        # Op links are built as docs_url + "ops/...", so it ends in a slash.
        self.docs_url = self.docs_url.rstrip("/") + "/"

    @classmethod
    def load(cls, state_dir: str | Path | None = None, **flags: Any) -> ServerConfig:
        """Merge ``<state_dir>/server.yaml`` under the given flags.

        Args:
            state_dir: State folder. Defaults to ``~/.aa-recipe/client``.
            **flags: Field values from the command line. ``None`` and empty
                lists mean the flag was not given.

        Returns:
            The config with every path resolved.

        Raises:
            ValueError: ``server.yaml`` is not a mapping, has unknown keys or
                values of the wrong type, or no recipe root is left.
        """
        state = DEFAULT_STATE_DIR
        if state_dir is not None:
            state = Path(state_dir).expanduser().resolve()
        values = {
            key: _resolve_paths(key, value, state)
            for key, value in _read_file(state / "server.yaml").items()
        }
        for key, value in flags.items():
            if value is None or (isinstance(value, (list, tuple)) and not value):
                continue
            values[key] = _resolve_paths(key, value, Path.cwd())
        config = cls(state_dir=state, **values)
        if not config.recipe_roots:
            raise ValueError("At least one recipe root is needed.")
        return config

    def to_env(self) -> dict[str, str]:
        """The environment that lets :meth:`from_env` rebuild this config."""
        data = {}
        for item in fields(self):
            value = getattr(self, item.name)
            if isinstance(value, Path):
                value = value.as_posix()
            elif isinstance(value, list):
                value = [Path(path).as_posix() for path in value]
            data[item.name] = value
        return {ENV_VAR: json.dumps(data)}

    @classmethod
    def from_env(cls) -> ServerConfig:
        """Rebuild the config the ``serve`` command stored in the environment."""
        text = os.environ.get(ENV_VAR)
        if not text:
            return cls()
        data = json.loads(text)
        for key in ("state_dir", "run_config"):
            if data.get(key) is not None:
                data[key] = Path(data[key])
        for key in ("recipe_roots", "data_roots"):
            data[key] = [Path(path) for path in data[key]]
        return cls(**data)

    def allowed_hosts(self) -> set[str]:
        """Host header values the server answers to."""
        hosts = {f"{name}:{self.port}" for name in LOOPBACK_HOSTS}
        if self.host not in ("localhost", "127.0.0.1", "::1"):
            hosts.add(f"{self.host}:{self.port}")
        return hosts

    def allowed_origins(self) -> set[str]:
        """Origin header values accepted on requests that change state."""
        origins = {f"http://{host}" for host in self.allowed_hosts()}
        if self.dev:
            origins.update(VITE_ORIGINS)
        return origins


def _read_file(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with open(path, encoding="utf-8") as handle:
        data = YAML(typ="safe").load(handle)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a mapping at the top level.")
    unknown = set(data) - FILE_KEYS
    if unknown:
        raise ValueError(
            f"{path} has unknown key(s): {', '.join(sorted(unknown))}. "
            f"Allowed keys: {', '.join(sorted(FILE_KEYS))}."
        )
    for key, value in data.items():
        if key in ("recipe_roots", "data_roots"):
            expected = "a list of folders"
            valid = isinstance(value, list) and all(isinstance(v, str) for v in value)
        elif key == "port":
            expected = "a whole number"
            valid = isinstance(value, int) and not isinstance(value, bool)
        else:
            expected = "a string"
            valid = isinstance(value, str) or (key == "run_config" and value is None)
        if not valid:
            raise ValueError(f"{path}: '{key}' must be {expected}, got {value!r}.")
    return data


def _resolve_paths(key: str, value: Any, base: Path) -> Any:
    """Turn the path fields into absolute paths, relative ones under ``base``."""
    if key in ("recipe_roots", "data_roots"):
        return [_absolute(path, base) for path in value]
    if key == "run_config" and value is not None:
        return _absolute(value, base)
    return value


def _absolute(path: str | Path, base: Path) -> Path:
    path = Path(path).expanduser()
    return (path if path.is_absolute() else base / path).resolve()
