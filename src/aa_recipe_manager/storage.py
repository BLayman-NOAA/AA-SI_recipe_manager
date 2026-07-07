# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""StorageLocation: one path seam for local directories and fsspec URLs.

Every directory the engine reads or writes (checkpoint cache, exe_temp
scratch, user-facing outputs) is parsed through :class:`StorageLocation`.
Local values stay plain-``pathlib`` in behavior: ``as_context_value()``
returns a real ``Path`` so local runs are byte-identical to the
pre-StorageLocation engine. Remote values (``gs://``, ``memory://``, ...)
route through fsspec, and consumers that only understand local paths fail
loudly at ``Path(...)`` coercion via :meth:`StorageLocation.__fspath__`
instead of silently writing to a mangled local directory.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import IO, Any, Mapping

# Two or more characters before "://" so Windows drive forms ("C:\", "C:/",
# and the degenerate "C://") are never mistaken for URL schemes.
_URL_SCHEME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]+://")

_LOCAL_PROTOCOLS = frozenset({"file", "local"})


def is_remote_url(value: Any) -> bool:
    """Return True when ``value`` is a string-like with a non-local URL scheme."""
    if isinstance(value, StorageLocation):
        return not value.is_local
    if isinstance(value, Path):
        return False
    if not isinstance(value, str):
        return False
    match = _URL_SCHEME_RE.match(value)
    if match is None:
        return False
    scheme = value[: match.end() - 3].lower()
    return scheme not in _LOCAL_PROTOCOLS


class StorageLocation:
    """A directory or file location on the local filesystem or an fsspec store."""

    __slots__ = ("url", "storage_options", "_fs", "_fs_path")

    def __init__(
        self,
        url: str,
        *,
        storage_options: Mapping[str, Any] | None = None,
        _fs: Any = None,
        _fs_path: str | None = None,
    ) -> None:
        self.url = url
        self.storage_options: dict[str, Any] = dict(storage_options or {})
        self._fs = _fs
        self._fs_path = _fs_path

    # -- construction -----------------------------------------------------

    @classmethod
    def parse(
        cls,
        value: str | Path | StorageLocation,
        storage_options: Mapping[str, Any] | None = None,
    ) -> StorageLocation:
        """Parse a path string, Path, or existing location into a StorageLocation.

        Remote URLs are resolved through ``fsspec.core.url_to_fs`` eagerly so a
        missing driver (e.g. gcsfs) fails at configuration time, not mid-run.
        """
        if isinstance(value, StorageLocation):
            return value
        raw = str(value)
        if not is_remote_url(raw):
            return cls(raw, storage_options=storage_options)
        try:
            import fsspec.core
        except ImportError as exc:  # pragma: no cover - fsspec is a hard dep
            raise ImportError(
                "fsspec is required for URL storage locations; "
                "pip install aa-recipe-manager"
            ) from exc
        try:
            fs, fs_path = fsspec.core.url_to_fs(raw, **(dict(storage_options or {})))
        except ImportError as exc:
            scheme = raw.split("://", 1)[0]
            raise ImportError(
                f"{scheme}:// URLs require an fsspec driver that is not "
                f"installed ({exc}). For Google Cloud Storage: "
                "pip install aa-recipe-manager[gcs]"
            ) from exc
        return cls(raw, storage_options=storage_options, _fs=fs, _fs_path=fs_path)

    # -- identity ----------------------------------------------------------

    @property
    def is_local(self) -> bool:
        return self._fs is None

    @property
    def fs(self) -> Any:
        if self._fs is None:
            raise ValueError(f"{self.url!r} is a local path; it has no fsspec filesystem")
        return self._fs

    @property
    def fs_path(self) -> str:
        if self._fs_path is None:
            raise ValueError(f"{self.url!r} is a local path; it has no fsspec path")
        return self._fs_path

    @property
    def name(self) -> str:
        if self.is_local:
            return Path(self.url).name
        return self.fs_path.rstrip("/").rsplit("/", 1)[-1]

    @property
    def parent(self) -> StorageLocation:
        if self.is_local:
            return StorageLocation.parse(
                str(Path(self.url).parent), self.storage_options
            )
        scheme, rest = self.url.split("://", 1)
        rest = rest.rstrip("/")
        parent_rest = rest.rsplit("/", 1)[0] if "/" in rest else rest
        return StorageLocation.parse(
            f"{scheme}://{parent_rest}", self.storage_options
        )

    def __truediv__(self, segment: str) -> StorageLocation:
        segment = str(segment)
        if self.is_local:
            return StorageLocation.parse(
                str(Path(self.url) / segment), self.storage_options
            )
        joined = self.url.rstrip("/") + "/" + segment.lstrip("/")
        return StorageLocation.parse(joined, self.storage_options)

    # -- filesystem operations ----------------------------------------------

    def exists(self) -> bool:
        if self.is_local:
            return Path(self.url).exists()
        return bool(self.fs.exists(self.fs_path))

    def mkdir(self) -> None:
        """Create the directory (and parents). Best-effort no-op on object stores."""
        if self.is_local:
            Path(self.url).mkdir(parents=True, exist_ok=True)
            return
        try:
            self.fs.makedirs(self.fs_path, exist_ok=True)
        except (NotImplementedError, OSError):
            # Object stores have no real directories; writes create prefixes.
            pass

    def open(self, mode: str = "rb", **kwargs: Any) -> IO[Any]:
        if self.is_local:
            return open(self.url, mode, **kwargs)
        return self.fs.open(self.fs_path, mode, **kwargs)

    def read_text(self, encoding: str = "utf-8") -> str:
        if self.is_local:
            return Path(self.url).read_text(encoding=encoding)
        with self.fs.open(self.fs_path, "r", encoding=encoding) as fh:
            return fh.read()

    def write_text(self, data: str, encoding: str = "utf-8") -> None:
        if self.is_local:
            Path(self.url).write_text(data, encoding=encoding)
            return
        with self.fs.open(self.fs_path, "w", encoding=encoding) as fh:
            fh.write(data)

    def rm(self, recursive: bool = True) -> None:
        """Remove the file or directory tree; missing targets are a no-op."""
        if self.is_local:
            path = Path(self.url)
            if not path.exists():
                return
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
            return
        try:
            self.fs.rm(self.fs_path, recursive=recursive)
        except FileNotFoundError:
            pass

    def glob(self, pattern: str) -> list[StorageLocation]:
        if self.is_local:
            return [
                StorageLocation.parse(str(p), self.storage_options)
                for p in sorted(Path(self.url).glob(pattern))
            ]
        matches = self.fs.glob(self.fs_path.rstrip("/") + "/" + pattern)
        return [
            StorageLocation.parse(
                self.fs.unstrip_protocol(m), self.storage_options
            )
            for m in sorted(matches)
        ]

    # -- conversions ---------------------------------------------------------

    def as_local_path(self) -> Path:
        """Return the location as a ``Path``; raises with guidance when remote."""
        if not self.is_local:
            raise ValueError(
                f"{self.url!r} is a remote storage location, but this operation "
                "only supports local paths. Pass a local directory instead, or "
                "use a format/consumer that supports fsspec URLs."
            )
        return Path(self.url)

    def as_context_value(self) -> Path | StorageLocation:
        """Value to publish to step code: real ``Path`` when local, self when remote."""
        if self.is_local:
            return Path(self.url)
        return self

    def __fspath__(self) -> str:
        if self.is_local:
            return self.url
        raise TypeError(
            f"{self.url!r} is a remote storage location; this consumer only "
            "supports local paths. Pass a local --temp-dir/--outputs-dir, or "
            "upgrade the consumer to open the location via fsspec using "
            "str(location) and its .storage_options."
        )

    def __str__(self) -> str:
        return self.url

    def __repr__(self) -> str:
        return f"StorageLocation({self.url!r})"

    def __eq__(self, other: object) -> bool:
        if isinstance(other, StorageLocation):
            return self.url == other.url
        return NotImplemented

    def __hash__(self) -> int:
        return hash(self.url)
