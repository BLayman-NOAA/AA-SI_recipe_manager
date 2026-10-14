# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Sessions: an opened recipe and the input and run settings the client set.

Each session is a folder ``<state_dir>/sessions/<id>/`` holding
``session.json``, so a session survives a browser reload and a server restart.
In this milestone a session is read only: it never changes the recipe files.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager import api
from aa_recipe_manager.server.errors import (
    PathNotAllowed,
    RecipeNotFound,
    SessionNotFound,
)

if TYPE_CHECKING:
    from aa_recipe_manager.server.config import ServerConfig

SESSION_SCHEMA = 1
SESSION_FILE = "session.json"
RUN_CONFIG_FIELDS = (
    "user_cache_dir",
    "survey_cache_dir",
    "outputs_dir",
    "temp_dir",
    "executor",
    "dask_scheduler",
    "dask_workers",
    "storage_options",
)
_SESSION_ID = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
_STEP = re.compile(r"^Step '([^']+)'(: )?")
_ERROR_HEADER = re.compile(r"^\d+ validation error\(s\):$")
_WARNING_HEADER = re.compile(r"^\d+ warning\(s\):$")


class Session:
    """One opened recipe, with its graph, run config and validation cached.

    Call :meth:`prepare` before reading anything computed; the store does.

    Attributes:
        record: The contents of ``session.json``.
        folder: The session's folder.
        config: The server config.
    """

    def __init__(self, record: dict[str, Any], folder: Path, config: ServerConfig):
        self.record = record
        self.folder = folder
        self.config = config
        self._graph: dict[str, Any] | None = None
        self._run_config: dict[str, Any] | None = None
        self._config_error: str | None = None
        self._validation: dict[str, Any] | None = None

    @property
    def origin(self) -> Path:
        return Path(self.record["origin"])

    @property
    def origin_dir(self) -> Path:
        return Path(self.record["origin_dir"])

    def prepare(self) -> None:
        """Compute the graph, run config and validation not cached yet.

        Raises:
            RecipeParseError: The recipe file is not a valid recipe.
        """
        if self._graph is None:
            self._graph = api.recipe_graph(
                self.origin,
                ops_base=self.config.docs_url + "ops/",
                allow_unknown_ops=True,
            )
        if self._run_config is None:
            self._load_run_config()
        if self._validation is None:
            self._validation = self._validate()

    def refresh(self) -> None:
        """Read the run config and validate again, after a change."""
        self._load_run_config()
        self._validation = self._validate()

    def declared_inputs(self) -> list[dict[str, Any]]:
        """The recipe's declared inputs, as graph input nodes."""
        return [
            node
            for node in self._graph["nodes"]
            if node["kind"] == "input" and node["declared"]
        ]

    def effective_inputs(self) -> dict[str, Any]:
        """Input values: recipe defaults, then the run config, then the session."""
        values = {
            node["id"].removeprefix("in:"): node["default"]
            for node in self.declared_inputs()
            if node["default"] is not None
        }
        values.update(self._run_config["inputs"])
        values.update(self.record["inputs"])
        return values

    def effective_run_config(self) -> dict[str, Any]:
        """Run settings: the run config file, then the session's overrides."""
        values = {name: self._run_config[name] for name in RUN_CONFIG_FIELDS}
        values.update(self.record["run_config"])
        values["source"] = self._run_config["source"]
        values["overrides"] = dict(self.record["run_config"])
        return values

    def document(self) -> dict[str, Any]:
        """The session as the API returns it."""
        self.prepare()
        return {
            "id": self.record["id"],
            "created": self.record["created"],
            "title": self.record["title"],
            "origin": self.record["origin"],
            "read_only": self.record["read_only"],
            "version": self.record["version"],
            "files": [
                {
                    "path": name,
                    "origin": (self.origin_dir / name).resolve().as_posix(),
                    "editable": False,
                }
                for name in self.record["files"]
            ],
            "inputs": {
                "declared": self._input_fields(),
                "values": self.effective_inputs(),
            },
            "run_config": self.effective_run_config(),
            "graph": self._graph,
            "validation": self._validation,
        }

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        path = self.folder / SESSION_FILE
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.record, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    def _load_run_config(self) -> None:
        """Find the run config as the CLI would when run from the recipe's folder.

        A missing or malformed config file leaves the settings empty and is
        reported as a validation problem.
        """
        try:
            found = api.run_config(
                self.origin, explicit=self.config.run_config, cwd=self.origin_dir
            )
        except (FileNotFoundError, ValueError) as exc:
            self._run_config = {name: None for name in RUN_CONFIG_FIELDS}
            self._run_config.update(inputs={}, source=None)
            self._config_error = str(exc)
            return
        self._run_config = {name: getattr(found, name) for name in RUN_CONFIG_FIELDS}
        self._run_config["inputs"] = dict(found.inputs)
        self._run_config["source"] = found.source.as_posix() if found.source else None
        self._config_error = None

    def _input_fields(self) -> list[dict[str, Any]]:
        config_inputs = self._run_config["inputs"]
        session_inputs = self.record["inputs"]
        result = []
        for node in self.declared_inputs():
            name = node["id"].removeprefix("in:")
            if name in session_inputs:
                source, value = "session", session_inputs[name]
            elif name in config_inputs:
                source, value = "config", config_inputs[name]
            elif node["default"] is not None:
                source, value = "default", node["default"]
            else:
                source, value = "unset", None
            result.append(
                {
                    "name": name,
                    "type": node["type"],
                    "description": node["description"],
                    "default": node["default"],
                    "required": bool(node["required"]),
                    "value": value,
                    "source": source,
                }
            )
        return result

    def _validate(self) -> dict[str, Any]:
        """Dry run problems, with relative paths checked beside the recipe."""
        problems: list[dict[str, Any]] = []
        if self._config_error is not None:
            problems.append(_problem("error", f"Run config: {self._config_error}"))
        explicit = {**self._run_config["inputs"], **self.record["inputs"]}
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            try:
                report = api.dry_run(
                    self.origin,
                    inputs=explicit or None,
                    check_versions=True,
                    base_dir=self.origin_dir,
                )
            except Exception as exc:
                problems.append(_problem("error", f"{type(exc).__name__}: {exc}"))
                return {"valid": False, "problems": problems}
        for text in report.errors:
            problems.extend(_split_problems(text, "error"))
        for text in report.warnings:
            problems.extend(_split_problems(text, "warning"))
        for caught_warning in caught:
            problems.append(_problem("warning", str(caught_warning.message)))
        for problem in problems:
            self._note_relative_path(problem)
        unique = {(p["severity"], p["step"], p["message"]): p for p in problems}
        valid = report.is_valid and self._config_error is None
        return {"valid": valid, "problems": list(unique.values())}


    def _note_relative_path(self, problem: dict[str, Any]) -> None:
        """Say where a missing relative path was looked for."""
        head, found, path = problem["message"].rpartition(" does not exist: ")
        if found and "://" not in path and not Path(path).is_absolute():
            folder = self.origin_dir.as_posix()
            problem["message"] += f" (relative to {folder})"


class SessionStore:
    """The sessions under ``<state_dir>/sessions``, loaded on first use.

    Every method holds one lock and returns plain data built under it: the
    server has one user, and dry runs record warnings through process wide
    state.
    """

    def __init__(self, config: ServerConfig) -> None:
        self.root = Path(config.state_dir) / "sessions"
        self.config = config
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def create(self, path: str | Path) -> dict[str, Any]:
        """Open a recipe file in a new session and return its document.

        Raises:
            PathNotAllowed: The file is not under a recipe root.
            RecipeNotFound: The file does not exist.
            RecipeParseError: The file is not a valid recipe.
        """
        origin = self._locate(path)
        with self._lock:
            sid = self._new_id()
            now = datetime.now(timezone.utc)
            record = {
                "schema": SESSION_SCHEMA,
                "id": sid,
                "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "title": origin.stem,
                "origin": origin.as_posix(),
                "origin_dir": origin.parent.as_posix(),
                "files": [],
                "inputs": {},
                "run_config": {},
                "read_only": True,
                "version": 0,
            }
            session = Session(record, self.root / sid, self.config)
            session.prepare()
            record["files"] = _session_files(origin, session._graph)
            document = session.document()
            session.save()
            self._sessions[sid] = session
            return document

    def get(self, sid: str) -> dict[str, Any]:
        """The document of a session, read from disk after a restart.

        Raises:
            SessionNotFound: No such session.
        """
        with self._lock:
            return self._get(sid).document()

    def list(self) -> list[dict[str, str]]:
        """Summaries of every saved session, newest first."""
        with self._lock:
            summaries = []
            folders = self.root.iterdir() if self.root.is_dir() else []
            for folder in folders:
                record = _read_record(folder / SESSION_FILE)
                if record is None:
                    continue
                summaries.append(
                    {key: record[key] for key in ("id", "title", "origin", "created")}
                )
            return sorted(summaries, key=lambda item: item["id"], reverse=True)

    def delete(self, sid: str) -> None:
        """Remove a session and its folder.

        Raises:
            SessionNotFound: No such session.
        """
        with self._lock:
            folder = self._folder(sid)
            if not folder.is_dir():
                raise SessionNotFound(f"No session {sid!r}.")
            shutil.rmtree(folder)
            self._sessions.pop(sid, None)

    def set_inputs(self, sid: str, values: dict[str, Any]) -> dict[str, Any]:
        """Replace the input values the session sets.

        Raises:
            SessionNotFound: No such session.
            ValueError: A value names an input the recipe does not declare.
        """
        with self._lock:
            session = self._get(sid)
            declared = {
                node["id"].removeprefix("in:") for node in session.declared_inputs()
            }
            unknown = sorted(set(values) - declared)
            if unknown:
                raise ValueError(f"The recipe declares no input {', '.join(unknown)}.")
            session.record["inputs"] = dict(values)
            return self._changed(session)

    def set_run_config(self, sid: str, overrides: dict[str, Any]) -> dict[str, Any]:
        """Replace the run settings the session overrides; ``None`` clears one.

        Raises:
            SessionNotFound: No such session.
        """
        with self._lock:
            session = self._get(sid)
            session.record["run_config"] = {
                key: value for key, value in overrides.items() if value is not None
            }
            return self._changed(session)

    def file_text(self, sid: str, relative: str) -> str:
        """The text of one of the session's recipe files.

        Raises:
            SessionNotFound: No such session.
            PathNotAllowed: The file is not one of the session's files, or it
                lies outside both the recipe's folder and the recipe roots.
        """
        with self._lock:
            session = self._get(sid)
            if relative not in session.record["files"]:
                raise PathNotAllowed(f"{relative!r} is not a file of this session.")
            target = (session.origin_dir / relative).resolve()
            if not any(target.is_relative_to(base) for base in self._readable(session)):
                raise PathNotAllowed(f"{relative!r} is outside the recipe roots.")
            return target.read_text(encoding="utf-8")

    def _readable(self, session: Session) -> list[Path]:
        """Folders a session's files may be read from."""
        roots = [Path(root).resolve() for root in self.config.recipe_roots]
        return [session.origin_dir.resolve(), *roots]

    def _changed(self, session: Session) -> dict[str, Any]:
        session.save()
        session.refresh()
        return session.document()

    def _get(self, sid: str) -> Session:
        if sid in self._sessions:
            return self._sessions[sid]
        folder = self._folder(sid)
        record = _read_record(folder / SESSION_FILE)
        if record is None:
            raise SessionNotFound(f"No session {sid!r}.")
        session = Session(record, folder, self.config)
        session.prepare()
        self._sessions[sid] = session
        return session

    def _folder(self, sid: str) -> Path:
        if not _SESSION_ID.match(sid):
            raise SessionNotFound(f"No session {sid!r}.")
        return self.root / sid

    def _new_id(self) -> str:
        while True:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            sid = f"{stamp}-{secrets.token_hex(3)}"
            if not (self.root / sid).exists() and sid not in self._sessions:
                return sid

    def _locate(self, path: str | Path) -> Path:
        """Resolve a requested recipe path, relative ones under the roots."""
        roots = [Path(root).resolve() for root in self.config.recipe_roots]
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            under = [root / candidate for root in roots]
            candidate = next((p for p in under if p.is_file()), under[0])
        resolved = candidate.resolve()
        if not any(resolved.is_relative_to(root) for root in roots):
            raise PathNotAllowed(f"{path} is not under a recipe root.")
        if not resolved.is_file():
            raise RecipeNotFound(f"Recipe not found: {path}")
        return resolved


def _session_files(origin: Path, graph: dict[str, Any]) -> list[str]:
    """The root file name, then every included file, without repeats."""
    files = [origin.name]
    for include in graph["includes"]:
        if include["source"] not in files:
            files.append(include["source"])
    return files


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def _split_problems(text: str, severity: str) -> list[dict[str, Any]]:
    """Split a dry run message into one problem per listed item.

    A validation failure arrives as one text with an ``N validation error(s):``
    header and ``  - `` items, possibly followed by a warnings section.
    """
    problems: list[dict[str, Any]] = []
    current = severity
    for line in text.splitlines():
        if _ERROR_HEADER.match(line):
            current = "error"
        elif _WARNING_HEADER.match(line):
            current = "warning"
        elif line.startswith("  - "):
            problems.append(_problem(current, line[4:]))
        elif problems:
            problems[-1]["message"] += "\n" + line
        else:
            problems.append(_problem(current, line))
    return problems


def _problem(severity: str, message: str) -> dict[str, Any]:
    """A problem, with a leading ``Step '<id>': `` moved into ``step``."""
    match = _STEP.match(message)
    step = match[1] if match else None
    if match and match[2]:
        message = message[match.end() :]
    return {"severity": severity, "message": message, "step": step}
