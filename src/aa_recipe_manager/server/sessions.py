# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Sessions: an opened recipe, its edits, and the input and run settings set.

Each session is a folder ``<state_dir>/sessions/<id>/`` holding
``session.json``, ``files/`` (the working copy edits are made in),
``history.jsonl`` and ``texts/`` (the copy's journal, so undo survives a
restart) and ``runs/``. A session survives a browser reload and a server
restart. The recipe files change only on Save and Save As.
"""

from __future__ import annotations

import re
import secrets
import shutil
import threading
import warnings
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aa_recipe_manager import api
from aa_recipe_manager.editing import (
    HistoryEntry,
    Journal,
    RecipeDocument,
    RecipeWorkspace,
    operation_from_dict,
    run_config,
    typed_operations,
    typed_value,
)
from aa_recipe_manager.server.errors import (
    PathNotAllowed,
    RecipeNotFound,
    SessionNotFound,
    StaleVersion,
)
from aa_recipe_manager.server.files import read_json, write_json
from aa_recipe_manager.server.recipes import folder_recipes

if TYPE_CHECKING:
    from aa_recipe_manager.editing import SyncResult
    from aa_recipe_manager.server.config import ServerConfig
    from aa_recipe_manager.server.recipes import ScanCache

SESSION_SCHEMA = 2
SESSION_FILE = "session.json"
FILES_FOLDER = "files"
RUNS_FOLDER = "runs"
#: Run settings that name a folder, resolved beside the recipe for the server.
PATH_FIELDS = ("user_cache_dir", "survey_cache_dir", "outputs_dir", "temp_dir")
RUN_CONFIG_FIELDS = run_config.SETTINGS
#: Run settings that decide which cached results a run finds.
CACHE_FIELDS = ("user_cache_dir", "survey_cache_dir", "storage_options")
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
        workspace: The recipe's files as edited, kept in ``files/``.
        hashes: Step hashes by step id, None when they cannot be computed.
        notice: What reopening the working copy could not restore, or None.
        conflicts: Files the last sync found changed on disk and left alone.
        fingerprint_cache: File and folder fingerprints kept between previews,
            so remote folders are listed once per session.
        scan_cache: Parses of the recipes beside this one, for the recipes
            that share its run config.
    """

    def __init__(
        self,
        record: dict[str, Any],
        folder: Path,
        config: ServerConfig,
        workspace: RecipeWorkspace,
    ):
        self.record = record
        self.folder = folder
        self.config = config
        self.workspace = workspace
        self.hashes: dict[str, str] | None = None
        self.notice: str | None = None
        self.conflicts: list[dict[str, Any]] = []
        self._graph: dict[str, Any] | None = None
        self._run_config: dict[str, Any] | None = None
        self._config_error: str | None = None
        self._validation: dict[str, Any] | None = None
        self._required: set[str] = set()
        self.fingerprint_cache: dict[tuple[str, str], Any] = {}
        self.scan_cache: ScanCache = {}
        self._shared: tuple[tuple[Any, ...], list[str]] | None = None

    @property
    def origin(self) -> Path:
        return Path(self.record["origin"])

    @property
    def origin_dir(self) -> Path:
        return Path(self.record["origin_dir"])

    def prepare(self) -> None:
        """Compute the run config, hashes, graph and validation not cached yet."""
        if self._run_config is None:
            self._load_run_config()
        if self._graph is None:
            self.hashes = self._step_hashes()
            self._graph = api.recipe_graph(
                self.workspace.recipe,
                source=self.workspace.root.origin.name,
                hashes=self.hashes,
                ops_base=self.config.docs_url + "ops/",
                allow_unknown_ops=True,
            )
        if self._validation is None:
            self._validation = self._validate()

    def refresh(self) -> None:
        """Compute everything again, after a change."""
        self._run_config = self._graph = self._validation = None
        self.prepare()

    def declared_inputs(self) -> list[dict[str, Any]]:
        """The recipe's declared inputs, as graph input nodes."""
        return [
            node
            for node in self._graph["nodes"]
            if node["kind"] == "input" and node["declared"]
        ]

    def effective_inputs(self) -> dict[str, Any]:
        """Input values shown: recipe defaults, then the run config, then the session.

        For display only. A run gets :meth:`explicit_inputs`, since a default
        passed as a value changes the step hashes.
        """
        values = {
            node["id"].removeprefix("in:"): node["default"]
            for node in self.declared_inputs()
            if node["default"] is not None
        }
        values.update(self._run_config["inputs"])
        values.update(self.record["inputs"])
        return values

    def explicit_inputs(self) -> dict[str, Any]:
        """Input values a run passes, as the CLI does: run config, then session.

        Declared defaults are left out. The recipe applies them itself, and
        passing one as a value would give different step hashes from the CLI's.
        """
        return {**self._run_config["inputs"], **self.record["inputs"]}

    def effective_run_config(self) -> dict[str, Any]:
        """Run settings: the run config file, then the session's overrides."""
        values = {name: self._run_config[name] for name in RUN_CONFIG_FIELDS}
        values.update(self.record["run_config"])
        values["source"] = self._run_config["source"]
        values["overrides"] = dict(self.record["run_config"])
        return values

    def run_config_block(self) -> dict[str, Any]:
        """The run settings, with the config file as written and who shares it."""
        found = self._run_config
        contents = found["contents"]
        written = contents["inputs"] if contents else found["inputs"]
        declared = {node["id"].removeprefix("in:") for node in self.declared_inputs()}
        return {
            **self.effective_run_config(),
            "path": found["path"],
            "chosen": self.record.get("config_path"),
            "candidate": run_config.config_candidate(self.origin).as_posix(),
            "error": self._config_error,
            "contents": contents,
            "shared_with": found["shared_with"],
            "unused_inputs": [name for name in written if name not in declared],
        }

    def resolved_run_config(self) -> dict[str, Any]:
        """Run settings with relative local folders joined onto the recipe's folder.

        The run process works in the recipe's folder, so these are the folders
        it reads and writes. An unset user cache is the CLI default,
        ``recipe_cache`` beside the recipe.
        """
        effective = self.effective_run_config()
        values = {name: effective[name] for name in RUN_CONFIG_FIELDS}
        if values["user_cache_dir"] is None:
            values["user_cache_dir"] = f"./{api.DEFAULT_OUTPUT_ROOT}"
        for name in PATH_FIELDS:
            value = values[name]
            if value is None or "://" in str(value) or Path(value).is_absolute():
                continue
            values[name] = (self.origin_dir / value).resolve().as_posix()
        return values

    def document(self) -> dict[str, Any]:
        """The session as the API returns it."""
        self.prepare()
        workspace = self.workspace
        dirty = workspace.dirty()
        return {
            "id": self.record["id"],
            "created": self.record["created"],
            "title": self.record["title"],
            "origin": self.record["origin"],
            "read_only": self.record["read_only"],
            "version": self.record["version"],
            "files": [
                {
                    "path": label,
                    "origin": workspace.document(label).origin.as_posix(),
                    "editable": True,
                    "dirty": label in dirty,
                }
                for label in workspace.files()
            ],
            "inputs": {
                "declared": self._input_fields(),
                "values": self.effective_inputs(),
            },
            "run_config": self.run_config_block(),
            "graph": self._graph,
            "validation": self._validation,
            "dirty": dirty,
            "conflicts": list(self.conflicts),
            "history": self._history_block(),
        }

    def _history_block(self) -> dict[str, Any]:
        undo = self.workspace.history.undo_stack
        redo = self.workspace.history.redo_stack
        return {
            "can_undo": bool(undo),
            "can_redo": bool(redo),
            "undo_kind": undo[-1].op.kind if undo else None,
            "redo_kind": redo[-1].op.kind if redo else None,
            "seq": undo[-1].seq if undo else 0,
            "note": self.notice,
        }

    def _step_hashes(self) -> dict[str, str] | None:
        """Step hashes as a run with the session's inputs gets them, or None."""
        options = self.effective_run_config()["storage_options"]
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                found = api.step_hashes(
                    self.workspace.recipe,
                    inputs=self.explicit_inputs() or None,
                    storage_options=options,
                    fingerprint_cache=self.fingerprint_cache,
                    base_dir=self.origin_dir,
                )
        except Exception:
            return None
        return {step_id: step.step_hash for step_id, step in found.items()}

    def save(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        write_json(self.folder / SESSION_FILE, self.record)

    def _load_run_config(self) -> None:
        """Find the run config as the CLI would when run from the recipe's folder.

        A file chosen for the session is used as ``--config`` uses one. A
        missing or malformed config file leaves the settings empty and is
        reported as a validation problem; a chosen file's text is still read
        when it is YAML, so it can be shown and fixed.
        """
        chosen = self.record.get("config_path")
        path = Path(chosen) if chosen else None
        try:
            found = api.run_config(
                self.origin,
                explicit=chosen or self.config.run_config,
                cwd=self.origin_dir,
            )
        except (FileNotFoundError, ValueError) as exc:
            self._run_config = {name: None for name in RUN_CONFIG_FIELDS}
            self._run_config.update(inputs={}, source=None)
            self._config_error = str(exc)
        else:
            self._run_config = {
                name: getattr(found, name) for name in RUN_CONFIG_FIELDS
            }
            self._run_config["inputs"] = dict(found.inputs)
            source = found.source
            self._run_config["source"] = source.as_posix() if source else None
            self._config_error = None
            path = source
        self._run_config.update(self._config_file(path))

    def _config_file(self, path: Path | None) -> dict[str, Any]:
        """The config file's ``path``, ``contents`` as written and sharers."""
        if path is None:
            return {"path": None, "contents": None, "shared_with": []}
        path = Path(path).absolute()
        try:
            contents = run_config.contents(RecipeDocument.load(path))
        except api.RecipeParseError:
            contents = None
        return {
            "path": path.as_posix(),
            "contents": contents,
            "shared_with": self._shared_with(path),
        }

    def _shared_with(self, path: Path) -> list[str]:
        """The other recipes in this recipe's folder whose runs use the file.

        Kept until the file, the recipe or the folder's modification time
        changes, which adding, removing or replacing a file there does.
        """
        target = path.resolve()
        try:
            key = (self.origin, target, self.origin_dir.stat().st_mtime_ns)
            if self._shared is not None and self._shared[0] == key:
                return list(self._shared[1])
            recipes = folder_recipes(self.origin_dir, self.scan_cache)
        except OSError:
            return []
        shared = []
        for recipe in recipes:
            if recipe.resolve() == self.origin.resolve():
                continue
            try:
                found = api.run_config(
                    recipe, explicit=self.config.run_config, cwd=self.origin_dir
                )
            except (FileNotFoundError, ValueError):
                continue
            if found.source is not None and found.source.resolve() == target:
                shared.append(recipe.absolute().as_posix())
        self._shared = (key, shared)
        return list(shared)

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
            required = name in self._required
            if value is None and required:
                source = "missing"
            result.append(
                {
                    "name": name,
                    "type": node["type"],
                    "description": node["description"],
                    "default": node["default"],
                    "required": required,
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
        explicit = self.explicit_inputs()
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            report = self.workspace.validate(explicit or None, check_versions=True)
        self._required = set(report.required_inputs)
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

        The recipe's files are copied into the session's ``files/`` folder,
        where edits are made until Save.

        Raises:
            PathNotAllowed: The file is not under a recipe root.
            RecipeNotFound: The file does not exist.
            RecipeParseError: The file is not a valid recipe.
            WorkspaceError: An include lies outside the recipe's folder.
        """
        origin = self._locate(path)
        with self._lock:
            sid = self._new_id()
            folder = self.root / sid
            try:
                workspace = RecipeWorkspace.open(
                    origin, copy_to=folder / FILES_FOLDER, journal=folder
                )
            except BaseException:
                shutil.rmtree(folder, ignore_errors=True)
                raise
            now = datetime.now(timezone.utc)
            record = {
                "schema": SESSION_SCHEMA,
                "id": sid,
                "created": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "title": origin.stem,
                "origin": origin.as_posix(),
                "origin_dir": origin.parent.as_posix(),
                "copy_root": workspace.root.path.name,
                "files": [{"path": label} for label in workspace.files()],
                "inputs": {},
                "run_config": {},
                "read_only": False,
                "version": 0,
            }
            session = Session(record, folder, self.config, workspace)
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
                record = read_json(folder / SESSION_FILE)
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
            types = {
                node["id"].removeprefix("in:"): node["type"]
                for node in session.declared_inputs()
            }
            session.record["inputs"] = {
                name: typed_value(value, types[name]) for name, value in values.items()
            }
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

    def choose_config(
        self, sid: str, path: str | None, version: int | None
    ) -> dict[str, Any]:
        """Use a run config file for the session, as ``--config`` does.

        Args:
            sid: The session.
            path: The file; a relative one is taken from the recipe's folder.
                None goes back to finding the file as the CLI does.
            version: The session version the request was made against.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
            PathNotAllowed: The file is not under a recipe root.
            FileNotFoundError: The file does not exist.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            chosen = None
            if path is not None:
                target = self._within_roots(session, path)
                if not target.is_file():
                    raise FileNotFoundError(f"Config file not found: {path}")
                chosen = target.as_posix()
            if chosen == session.record.get("config_path"):
                return session.document()
            session.record["config_path"] = chosen
            return self._changed(session)

    def create_config(self, sid: str, version: int | None) -> dict[str, Any]:
        """Write ``<recipe stem>.config.yaml`` beside the recipe from the template.

        The template lists every run setting as null, each with a comment,
        and each required input with a null value to fill in. The session
        uses the new file afterwards: by discovery when that finds it, else
        as its chosen file.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
            PathNotAllowed: The file would not be under a recipe root.
            TargetExists: The file exists already.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            self._within_roots(session, run_config.config_candidate(session.origin))
            target = run_config.create(session.workspace.recipe, session.origin)
            found = self._discovered(session)
            target = target.resolve()
            discovered = found is not None and found.resolve() == target
            session.record["config_path"] = None if discovered else target.as_posix()
            return self._changed(session)

    def save_config(
        self,
        sid: str,
        inputs: dict[str, Any] | None,
        settings: dict[str, Any],
        version: int | None,
        scope: str = "shared",
    ) -> dict[str, Any]:
        """Write edits to the session's run config file, keeping its comments.

        Args:
            sid: The session.
            inputs: The file's whole ``inputs:`` after the save; None leaves
                them as they are.
            settings: Run settings to write; see ``run_config.apply``.
            version: The session version the request was made against.
            scope: ``"shared"`` edits the file in use. ``"recipe"`` writes
                a copy of it, with the edits, as the recipe's own
                ``<stem>.config.yaml``, which the session uses afterwards.

        Returns:
            ``session``, ``changed``, ``shared_with`` (the other recipes in
            the folder that use the file), ``changed_hashes`` (as for an
            edit) and ``cache_changed``: whether ``user_cache_dir``,
            ``survey_cache_dir`` or ``storage_options`` changed in the
            settings a run gets, which moves steps between cache tiers
            without changing their hashes.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
            ValueError: The session uses no config file, a setting is not a
                run setting, or the file does not hold a mapping.
            PathNotAllowed: The file is not under a recipe root.
            RecipeParseError: The file is not valid YAML.
            TargetExists: ``scope`` is ``"recipe"`` and the recipe's own
                config file exists already.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            path = session._run_config["path"]
            if path is None:
                raise ValueError(
                    "The session uses no run config file; create or choose one."
                )
            document = RecipeDocument.load(self._within_roots(session, path))
            if scope == "recipe":
                target = run_config.config_candidate(session.origin)
                document = run_config.copy_to(
                    document, self._within_roots(session, target)
                )
            if inputs is not None:
                types = {f["name"]: f["type"] for f in session._input_fields()}
                inputs = {
                    name: typed_value(value, types.get(name))
                    for name, value in inputs.items()
                }
            run_config.apply(document, inputs, settings)
            if scope != "recipe" and not document.dirty:
                return {
                    "session": session.document(),
                    "changed": False,
                    "shared_with": session._run_config["shared_with"],
                    "changed_hashes": [],
                    "cache_changed": False,
                }
            before = session.effective_run_config()
            hashes = session.hashes
            target = document.write().resolve()
            if scope == "recipe":
                found = self._discovered(session)
                discovered = found is not None and found.resolve() == target
                session.record["config_path"] = (
                    None if discovered else target.as_posix()
                )
            result = self._changed(session)
            after = session.effective_run_config()
            return {
                "session": result,
                "changed": True,
                "shared_with": session._run_config["shared_with"],
                "changed_hashes": _changed_hashes(hashes, session.hashes),
                "cache_changed": any(
                    before.get(name) != after.get(name) for name in CACHE_FIELDS
                ),
            }

    def file_text(self, sid: str, relative: str) -> str:
        """The text of one of the session's recipe files, as edited.

        Raises:
            SessionNotFound: No such session.
            PathNotAllowed: The file is not one of the session's files.
        """
        with self._lock:
            workspace = self._get(sid).workspace
            if relative not in workspace.files():
                raise PathNotAllowed(f"{relative!r} is not a file of this session.")
            return workspace.document(relative).text

    def edit(
        self, sid: str, operations: list[dict[str, Any]], version: int | None
    ) -> dict[str, Any]:
        """Apply operations in order, all or none.

        A whole number for a ``float`` param, override or input default
        becomes a float, as for inputs. When one fails, the ones before it in
        the request are undone, so they stay on the redo stack, and the
        session gets a new version.

        Returns:
            ``session``, ``changed``, ``changed_hashes`` and ``entries``.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
            ValueError: An operation is malformed, or asks for something
                unsupported (NaN, removing a list entry).
            UnknownTarget: An operation names something that does not exist.
            EditRejected: An operation was rolled back; ``problems`` says why.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            workspace = session.workspace
            parsed = typed_operations(
                workspace,
                [operation_from_dict(op) for op in operations],
                session._graph,
            )
            inputs = session.explicit_inputs() or None
            entries: list[HistoryEntry] = []
            try:
                for op in parsed:
                    entry = workspace.apply(op, inputs=inputs)
                    if entry is not None:
                        entries.append(entry)
            except BaseException:
                for _ in entries:
                    workspace.undo()
                workspace.flush()
                if entries:
                    self._changed(session)
                raise
            return self._edited(session, entries)

    def undo(self, sid: str, version: int | None) -> dict[str, Any]:
        """Undo the newest edit; ``changed`` is False when there is none.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            entry = session.workspace.undo()
            return self._edited(session, [entry] if entry else [])

    def redo(self, sid: str, version: int | None) -> dict[str, Any]:
        """Apply the newest undone edit again; ``changed`` is False when none.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            entry = session.workspace.redo()
            return self._edited(session, [entry] if entry else [])

    def revert(self, sid: str, version: int | None) -> dict[str, Any]:
        """Discard every unsaved edit and the history; read the files again.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
            RecipeParseError: An origin no longer parses into a recipe.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            session.workspace.revert()
            session.notice = None
            session.conflicts = []
            return self._changed(session)

    def reload(
        self, sid: str, files: list[str] | None, version: int | None
    ) -> dict[str, Any]:
        """Take files' texts from disk, as undoable edits.

        Args:
            sid: The session.
            files: Labels of the files; every file changed on disk when None.
            version: The session version the request was made against.

        Returns:
            ``session``, ``changed``, ``changed_hashes`` and ``entries``.

        Raises:
            SessionNotFound: No such session.
            StaleVersion: ``version`` is set and not the session's.
            UnknownTarget: A label names no file of the recipe.
            EditRejected: A file's disk text does not load, naming it; the
                files before it in the list are reloaded and kept.
        """
        with self._lock:
            session = self._get(sid)
            _check_version(session, version)
            workspace = session.workspace
            labels = workspace.check_disk() if files is None else list(files)
            entries: list[HistoryEntry] = []
            try:
                for label in labels:
                    entries += workspace.reload([label])
            except BaseException:
                if entries:
                    self._edited(session, entries)
                raise
            return self._edited(session, entries)

    def sync(self, sid: str) -> dict[str, Any]:
        """Reload the files changed on disk that have no unsaved edits here.

        The others are kept as conflicts on the session until the next sync.
        The version changes only when a file was reloaded.

        Returns:
            ``session``, ``reloaded`` (labels), ``conflicts`` and ``entries``.

        Raises:
            SessionNotFound: No such session.
        """
        with self._lock:
            session = self._get(sid)
            result = self._sync(session)
            return {
                "session": session.document(),
                "reloaded": _labels(result),
                "conflicts": list(session.conflicts),
                "entries": [entry.to_dict() for entry in result.reloaded],
            }

    def save(self, sid: str, overwrite: bool) -> dict[str, Any]:
        """Sync with the disk, then write every dirty file to its origin.

        Returns:
            ``session``, ``written`` (the origins written) and ``reloaded``
            (the labels of the files the sync reloaded).

        Raises:
            SessionNotFound: No such session.
            ChangedOnDisk: A dirty file's origin changed on disk and
                ``overwrite`` is False; nothing is written.
        """
        with self._lock:
            session = self._get(sid)
            synced = self._sync(session)
            written = session.workspace.save(overwrite=overwrite)
            document = self._changed(session) if written else session.document()
            return {
                "session": document,
                "written": [path.as_posix() for path in written],
                "reloaded": _labels(synced),
            }

    def save_as(
        self,
        sid: str,
        path: str,
        *,
        keep_paths: bool,
        copy_config: bool,
        overwrite: bool,
        dry_run: bool,
    ) -> dict[str, Any]:
        """Save the recipe under a new name under a recipe root; Save follows it.

        Args:
            sid: The session.
            path: The new root file; a relative one is taken from the
                recipe's folder.
            keep_paths: Rewrite only the relative paths no hash contains, so
                the copy keeps its caches.
            copy_config: Copy the run config.
            overwrite: Replace existing files.
            dry_run: Work out the result and write nothing.

        Returns:
            ``session`` and ``result`` (``SaveAsResult.to_dict()``).

        Raises:
            SessionNotFound: No such session.
            PathNotAllowed: The new file is not under a recipe root.
            TargetExists: Files to write exist and ``overwrite`` is False.
            ChangedOnDisk: A dirty sub-recipe saved in place changed on disk.
            WorkspaceError: The new file would replace a file of the recipe
                or a run config.
        """
        with self._lock:
            session = self._get(sid)
            target = self._within_roots(session, path)
            result = session.workspace.save_as(
                target,
                paths="fingerprinted" if keep_paths else "all",
                copy_config=copy_config,
                config=session.record.get("config_path") or self.config.run_config,
                inputs=session.record["inputs"],
                overwrite=overwrite,
                dry_run=dry_run,
            )
            if dry_run:
                return {"session": session.document(), "result": result.to_dict()}
            origin = session.workspace.root.origin
            session.record.update(
                origin=origin.as_posix(),
                origin_dir=origin.parent.as_posix(),
                title=origin.stem,
            )
            if result.config is not None or not copy_config:
                session.record["config_path"] = None
            session.fingerprint_cache.clear()
            session.conflicts = []
            if not session.workspace.history.can_undo:
                session.notice = None
            return {"session": self._changed(session), "result": result.to_dict()}

    def runs_dir(self, sid: str) -> Path:
        """The folder holding a session's runs, which may not exist yet.

        Raises:
            SessionNotFound: No such session.
        """
        folder = self._folder(sid)
        if not (folder / SESSION_FILE).is_file():
            raise SessionNotFound(f"No session {sid!r}.")
        return folder / RUNS_FOLDER

    def preview_args(
        self,
        sid: str,
        *,
        targets: list[str],
        only: bool,
        force: bool,
        force_steps: list[str],
    ) -> dict[str, Any]:
        """Keyword arguments for ``api.plan_preview`` of a run of this session.

        Relative paths are resolved as the run process resolves them from the
        recipe's folder.

        Raises:
            SessionNotFound: No such session.
        """
        return {
            **self.cache_args(sid),
            "targets": targets or None,
            "only": only,
            "force": force,
            "force_steps": force_steps,
        }

    def cache_args(self, sid: str) -> dict[str, Any]:
        """The recipe, inputs and folders a session's runs read and write.

        Returns:
            ``recipe``, ``inputs``, ``user_cache_dir``, ``survey_cache_dir``,
            ``outputs_dir``, ``storage_options``, ``fingerprint_cache`` and
            ``base_dir``, with relative folders resolved from the recipe's
            folder as the run process resolves them.

        Raises:
            SessionNotFound: No such session.
        """
        with self._lock:
            return _cache_args(self._get(sid))

    def run_job(self, sid: str, snapshot: Path) -> dict[str, Any]:
        """What a run of this session needs, with its recipe written to a folder.

        Files changed on disk with no unsaved edits here are reloaded first,
        so the run and its checks see the disk's text. Then the recipe's files
        are written under ``snapshot`` as they are now, under the same lock as
        the validation, so the run reads the recipe that was checked and edits
        made while it runs do not reach it.

        Args:
            sid: The session.
            snapshot: The folder the recipe's files are written to.

        Returns:
            ``recipe`` (the root's copy in ``snapshot``), ``origin`` and
            ``cwd`` (absolute), explicit ``inputs``, the ``run_config``
            fields as written, ``files`` (the labels of the recipe's files),
            ``step_ids``, ``validation`` and ``reloaded`` (the labels of the
            files the sync reloaded).

        Raises:
            SessionNotFound: No such session.
        """
        with self._lock:
            session = self._get(sid)
            synced = self._sync(session)
            document = session.document()
            settings = session.effective_run_config()
            recipe = session.workspace.snapshot(snapshot)
            return {
                "recipe": recipe.as_posix(),
                "reloaded": _labels(synced),
                "origin": session.origin.as_posix(),
                "cwd": session.origin_dir.as_posix(),
                "inputs": session.explicit_inputs(),
                "run_config": {name: settings[name] for name in RUN_CONFIG_FIELDS},
                "files": session.workspace.files(),
                "step_ids": [
                    node["id"]
                    for node in document["graph"]["nodes"]
                    if node["kind"] == "step"
                ],
                "validation": document["validation"],
            }

    def _changed(self, session: Session) -> dict[str, Any]:
        """Record a change: a new version, saved, computed again; the document."""
        workspace = session.workspace
        record = session.record
        record["version"] += 1
        record["files"] = [{"path": label} for label in workspace.files()]
        differ = set(workspace.check_disk())
        session.conflicts = [c for c in session.conflicts if c["path"] in differ]
        session.save()
        session.refresh()
        return session.document()

    def _edited(self, session: Session, entries: list[HistoryEntry]) -> dict[str, Any]:
        """Flush and record the entries an edit, undo, redo or reload made.

        Returns:
            ``session``, ``changed``, ``changed_hashes`` and ``entries``.
        """
        session.workspace.flush()
        if not entries:
            return {
                "session": session.document(),
                "changed": False,
                "changed_hashes": [],
                "entries": [],
            }
        before = session.hashes
        document = self._changed(session)
        return {
            "session": document,
            "changed": True,
            "changed_hashes": _changed_hashes(before, session.hashes),
            "entries": [entry.to_dict() for entry in entries],
        }

    def _sync(self, session: Session) -> SyncResult:
        """Reload the clean files changed on disk; note the others as conflicts."""
        result = session.workspace.sync_disk()
        session.conflicts = [
            {"path": c.label, "error": c.error, "disk_sha256": c.disk_digest}
            for c in result.conflicts
        ]
        if result.reloaded:
            self._edited(session, result.reloaded)
        return result

    def _get(self, sid: str) -> Session:
        """A session, reopened from its folder after a restart.

        A session with a journal continues its working copy, which restores
        copy files gone missing from the journal. A schema 1 session (from
        before editing) has neither, and gets a working copy of the files as
        they are now.
        """
        if sid in self._sessions:
            return self._sessions[sid]
        folder = self._folder(sid)
        record = read_json(folder / SESSION_FILE)
        if record is None:
            raise SessionNotFound(f"No session {sid!r}.")
        record = _migrate(record)
        origin = Path(record["origin"])
        root_copy = folder / FILES_FOLDER / record.get("copy_root", origin.name)
        if Journal(folder).exists() or root_copy.is_file():
            workspace = RecipeWorkspace.reopen(root_copy, origin, journal=folder)
        else:
            workspace = RecipeWorkspace.open(
                origin, copy_to=folder / FILES_FOLDER, journal=folder
            )
        record["copy_root"] = workspace.root.path.name
        session = Session(record, folder, self.config, workspace)
        session.notice = "\n".join(workspace.warnings) or None
        session.save()
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

    def _within_roots(self, session: Session, path: str | Path) -> Path:
        """A path resolved from the recipe's folder, refused outside the roots.

        Raises:
            PathNotAllowed: The path is not under a recipe root.
        """
        target = Path(path).expanduser()
        if not target.is_absolute():
            target = session.origin_dir / target
        target = target.resolve()
        roots = [Path(root).resolve() for root in self.config.recipe_roots]
        if not any(target.is_relative_to(root) for root in roots):
            raise PathNotAllowed(f"{path} is not under a recipe root.")
        return target

    def _discovered(self, session: Session) -> Path | None:
        """The run config file the CLI would find for the recipe, or None."""
        try:
            found = api.run_config(
                session.origin, explicit=self.config.run_config, cwd=session.origin_dir
            )
        except (FileNotFoundError, ValueError):
            return None
        return found.source

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


def _migrate(record: dict[str, Any]) -> dict[str, Any]:
    """A ``session.json`` record brought to the current schema.

    Schema 1 listed the files as labels and was read only.
    """
    if record.get("schema", 1) < 2:
        files = [{"path": label} for label in record.get("files", [])]
        record.update(schema=2, files=files, read_only=False)
    return record


def _check_version(session: Session, version: int | None) -> None:
    """Refuse a request made against another version of the session.

    Raises:
        StaleVersion: ``version`` is set and differs from the session's.
    """
    current = session.record["version"]
    if version is not None and version != current:
        raise StaleVersion(
            f"The session is at version {current}, not {version}; it changed "
            "since the request was made.",
            current,
        )


def _cache_args(session: Session) -> dict[str, Any]:
    """The recipe, inputs and folders a session's runs read and write."""
    config = session.resolved_run_config()
    return {
        "recipe": session.workspace.recipe,
        "inputs": session.explicit_inputs() or None,
        "user_cache_dir": config["user_cache_dir"],
        "survey_cache_dir": config["survey_cache_dir"],
        "outputs_dir": config["outputs_dir"],
        "storage_options": config["storage_options"],
        "fingerprint_cache": session.fingerprint_cache,
        "base_dir": session.origin_dir,
    }


def _labels(result: SyncResult) -> list[str]:
    """The labels of the files a sync reloaded."""
    return [entry.documents[0] for entry in result.reloaded]


def _changed_hashes(
    before: dict[str, str] | None, after: dict[str, str] | None
) -> list[str] | None:
    """Steps whose hash changed in run order, then the steps removed."""
    if before is None or after is None:
        return None
    changed = [step for step, value in after.items() if before.get(step) != value]
    return changed + [step for step in before if step not in after]


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
