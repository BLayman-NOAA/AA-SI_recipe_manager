# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Locate an implementation's callable and link it to its source on GitHub.

A spec names its implementation with a dotted ``callable_path`` and nothing
else, so the file, line, signature, and docstring have to be recovered by
importing the callable and inspecting it. The repository link is then rebuilt
from whichever of these knows the answer: the git checkout the file sits in
(the editable AA-SI packages), the installed distribution's metadata (wheels
such as echopype), or the repository URL the spec itself declares.

Nothing here raises. A callable that cannot be imported comes back with
``resolved`` false and the reason in ``note``, so one missing package never
costs the whole page.
"""

from __future__ import annotations

import functools
import importlib.metadata
import inspect
import json
import logging
import re
import subprocess
import sys
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from collections.abc import Iterator

_GIT_TIMEOUT = 10

#: scp-style remote, e.g. ``git@github.com:owner/repo.git``.
_SCP_REMOTE = re.compile(r"^(?:(?P<user>[^@/]+)@)?(?P<host>[^:/]+):(?P<path>.+)$")

#: Set when the linked file has local edits, so its line number is only right
#: for the working copy and not for the commit the link points at.
DIRTY_NOTE = "uncommitted local edits, line may differ"


@dataclass
class SourceLocation:
    """Where an implementation's callable lives and how to reach it."""

    callable_path: str
    resolved: bool = False
    module: str | None = None
    qualname: str | None = None
    signature: str | None = None
    doc: str | None = None
    file: str | None = None
    line: int | None = None
    github_url: str | None = None
    repo: str | None = None
    ref: str | None = None
    repo_relative_path: str | None = None
    origin: str | None = None
    installed_version: str | None = None
    note: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the location as a plain JSON-serializable dict."""
        return asdict(self)


def unresolved(callable_path: str, note: str) -> SourceLocation:
    """A location for a callable that was not inspected."""
    return SourceLocation(callable_path=callable_path, note=note)


@functools.cache
def resolve_source(
    callable_path: str,
    *,
    distribution: str | None = None,
    fallback_url: str | None = None,
) -> SourceLocation:
    """Import a callable and describe where its source lives.

    Args:
        callable_path: Dotted path from an implementation, for example
            ``echopype.calibrate.compute_Sv``.
        distribution: Installed distribution name, normally the
            implementation's ``dependency.name``.
        fallback_url: Repository URL declared by the spec, used when neither
            the checkout nor the package metadata supplies one.

    Returns:
        A SourceLocation. Failures are reported through ``resolved`` and
        ``note`` rather than raised.
    """
    from aa_recipe_manager.executor.invocation import import_callable

    try:
        with _quiet_import():
            obj = import_callable(callable_path)
    except Exception as exc:
        return unresolved(callable_path, f"{type(exc).__name__}: {exc}")

    target = _unwrap(obj)
    location = SourceLocation(
        callable_path=callable_path,
        resolved=True,
        module=getattr(target, "__module__", None),
        qualname=getattr(target, "__qualname__", None),
        signature=_signature(target),
        doc=inspect.getdoc(target),
    )

    file, line = _source_position(target)
    if file is None:
        location.note = "source file is not available"
        return location

    location.file = file
    location.line = line
    _attach_repository(location, distribution, fallback_url)
    return location


def clear_caches() -> None:
    """Drop every memoized import, git, and metadata lookup."""
    for cached in (resolve_source, _git, _dist_repository, _dist_version):
        cached.cache_clear()


@contextmanager
def _quiet_import() -> Iterator[None]:
    """Import third-party packages without keeping their global side effects.

    echopype mutes logging process-wide on import and the visualization stack
    pulls in matplotlib, neither of which should outlive a documentation build.
    """
    from aa_recipe_manager.executor.engine.mplbackend import preserve_backend

    disable_level = logging.root.manager.disable
    with preserve_backend():
        try:
            yield
        finally:
            logging.root.manager.disable = disable_level


def _unwrap(obj: Any) -> Any:
    """Follow bound methods, partials, and decorators to the real definition."""
    obj = getattr(obj, "__func__", obj)
    if isinstance(obj, functools.partial):
        obj = obj.func
    try:
        return inspect.unwrap(obj)
    except ValueError:
        return obj


def _signature(target: Any) -> str | None:
    """The callable's name and parameter list, or None for C-level callables."""
    try:
        return f"{getattr(target, '__name__', '')}{inspect.signature(target)}"
    except (ValueError, TypeError):
        return None


def _source_position(target: Any) -> tuple[str | None, int | None]:
    """Absolute file and first line of a callable's definition."""
    try:
        file = inspect.getsourcefile(target)
        line = inspect.getsourcelines(target)[1]
    except (OSError, TypeError):
        return None, None
    if not file:
        return None, None
    return str(Path(file).resolve()), line


def _attach_repository(
    location: SourceLocation, distribution: str | None, fallback_url: str | None
) -> None:
    """Fill in the GitHub link fields, leaving a note when that is not possible."""
    path = Path(location.file or "")
    if distribution:
        location.installed_version = _dist_version(distribution)

    if not _in_site_packages(path) and _attach_from_checkout(
        location, path, fallback_url
    ):
        return
    if _attach_from_distribution(location, path, distribution, fallback_url):
        return
    if location.note is None:
        location.note = "no GitHub repository could be determined"


def _in_site_packages(path: Path) -> bool:
    """Whether a file was installed into an environment rather than checked out.

    The virtual environment commonly lives inside a git repository, so asking
    git about an installed file would answer with the wrong repository.
    """
    return any(part in ("site-packages", "dist-packages") for part in path.parts)


def _attach_from_checkout(
    location: SourceLocation, path: Path, fallback_url: str | None
) -> bool:
    """Link a file that sits in a git working tree, as editable installs do."""
    root = _git_toplevel(str(path.parent))
    if root is None:
        return False
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return False

    https = _github_https(_git_remote_url(root) or fallback_url)
    if https is None:
        location.note = "checkout has no GitHub remote"
        return False

    location.repo_relative_path = relative
    location.ref = _git_ref(root)
    location.origin = "git"
    if _git(root, "status", "--porcelain", "--", relative):
        location.note = DIRTY_NOTE
    _set_link(location, https)
    return True


def _attach_from_distribution(
    location: SourceLocation,
    path: Path,
    distribution: str | None,
    fallback_url: str | None,
) -> bool:
    """Link a file installed from a wheel, using its distribution metadata."""
    url, commit = _dist_repository(distribution) if distribution else (None, None)
    https = _github_https(url or fallback_url)
    if https is None:
        return False

    relative = _package_relative_path(path, location.module)
    if relative is None:
        return False

    ref = commit
    if ref is None and location.installed_version:
        ref = f"v{location.installed_version}"
    if ref is None:
        return False

    location.repo_relative_path = relative
    location.ref = ref
    location.origin = "metadata" if url else "spec-url"
    _set_link(location, https)
    return True


def _set_link(location: SourceLocation, https: str) -> None:
    """Assemble the blob URL from the parts collected so far."""
    location.repo = urlsplit(https).path.strip("/")
    if location.ref and location.repo_relative_path and location.line:
        location.github_url = (
            f"{https}/blob/{location.ref}/{location.repo_relative_path}"
            f"#L{location.line}"
        )


def _package_relative_path(path: Path, module: str | None) -> str | None:
    """Path of a source file relative to the root its top-level package sits in.

    A wheel records nothing about the project's own layout, so this is the
    repository path only for a flat-layout project. A src-layout project would
    need its prefix back, which no installed metadata can supply.
    """
    top = (module or "").partition(".")[0]
    package_file = getattr(sys.modules.get(top), "__file__", None)
    if not package_file:
        return None
    root = Path(package_file).resolve().parent.parent
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _github_https(url: str | None) -> str | None:
    """Normalize a git remote to ``https://github.com/owner/repo``.

    Returns None for anything that is not a GitHub repository, since other
    forges do not share the ``/blob/<ref>/<path>`` URL shape.
    """
    if not url:
        return None

    url = url.strip().removeprefix("git+")
    if url.startswith("ssh://"):
        url = "https://" + url[len("ssh://") :]
    elif "://" not in url:
        match = _SCP_REMOTE.match(url)
        if match is None:
            return None
        url = f"https://{match['host']}/{match['path']}"

    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        return None
    if parts.netloc.rpartition("@")[2].lower() != "github.com":
        return None

    path = parts.path.strip("/").removesuffix(".git")
    if path.count("/") != 1:
        return None
    return f"https://github.com/{path}"


def _git_toplevel(directory: str) -> str | None:
    """Root of the git working tree containing a directory."""
    root = _git(directory, "rev-parse", "--show-toplevel")
    return str(Path(root).resolve()) if root else None


def _git_remote_url(root: str) -> str | None:
    """Fetch URL of origin, or of the first remote when there is no origin."""
    url = _git(root, "remote", "get-url", "origin")
    if url:
        return url
    remotes = _git(root, "remote")
    if not remotes:
        return None
    return _git(root, "remote", "get-url", remotes.splitlines()[0].strip())


def _git_ref(root: str) -> str | None:
    """Current branch name, falling back to the commit when HEAD is detached."""
    ref = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if ref and ref != "HEAD":
        return ref
    return _git(root, "rev-parse", "HEAD")


@functools.cache
def _git(directory: str, *args: str) -> str | None:
    """Run a git command, returning its stdout or None if it did not succeed."""
    try:
        result = subprocess.run(
            ["git", "-C", directory, *args],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


@functools.cache
def _dist_repository(distribution: str) -> tuple[str | None, str | None]:
    """Repository URL and pinned commit recorded for an installed distribution."""
    try:
        dist = importlib.metadata.distribution(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None, None

    direct_url = dist.read_text("direct_url.json")
    if direct_url:
        try:
            data = json.loads(direct_url)
        except ValueError:
            data = {}
        vcs_info = data.get("vcs_info") or {}
        if vcs_info:
            return data.get("url"), vcs_info.get("commit_id")

    project_urls = dist.metadata.get_all("Project-URL") or []
    labelled = {}
    for entry in project_urls:
        label, _, value = str(entry).partition(",")
        labelled[label.strip().lower()] = value.strip()
    for label in ("repository", "source", "source code", "homepage"):
        if labelled.get(label):
            return labelled[label], None

    return dist.metadata.get("Home-page"), None


@functools.cache
def _dist_version(distribution: str) -> str | None:
    """Installed version of a distribution, or None if it is not installed."""
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None
