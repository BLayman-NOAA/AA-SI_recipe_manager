# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Environment report behind ``aa-recipe doctor``.

Answers the questions that come up when a recipe runs on one machine and fails
on another: which build of each AA-SI package is installed, how much memory the
box has (echopype swaps ``backscatter_r`` to disk based on it), and whether the
configured directories can actually take the nested-directory writes a zarr
store performs.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import stat
import sys
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

from aa_recipe_manager.fsutil import rmtree

#: Reported in this order: the AA-SI stack first, then what it stands on.
REPORTED_PACKAGES = (
    "aa-recipe-manager",
    "aa-si-utils",
    "aa-si-calibration",
    "aa-si-visualization",
    "aa-si-ml",
    "echopype",
    "zarr",
    "xarray",
    "numpy",
    "dask",
    "fsspec",
    "gcsfs",
    "psutil",
)

#: Depth of the probe tree, mirroring ``<store>.zarr/Sonar/Beam_group1/array``.
_PROBE_TREE = ("aa_doctor_probe.zarr", "Sonar", "Beam_group1", "backscatter_r")


def _fmt_bytes(count: float) -> str:
    """Format a byte count in binary units."""
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(count) < 1024 or unit == "TiB":
            return f"{count:.1f} {unit}"
        count /= 1024
    return f"{count:.1f} TiB"


def _package_line(name: str) -> str:
    """One package's version, install location, and VCS commit if any."""
    try:
        dist = distribution(name)
    except PackageNotFoundError:
        return f"  {name}: not installed"

    parts = [f"{name}: {dist.version}"]
    # A pip install from a URL records where it came from; for a git install
    # that includes the exact commit, which is the only way to tell two
    # same-version builds of an unversioned package apart.
    try:
        raw = dist.read_text("direct_url.json")
    except OSError:
        raw = None
    if raw:
        try:
            info = json.loads(raw)
        except ValueError:
            info = {}
        vcs = info.get("vcs_info") or {}
        commit = vcs.get("commit_id")
        if commit:
            requested = vcs.get("requested_revision")
            rev = f"{commit[:12]}" + (f" ({requested})" if requested else "")
            parts.append(f"commit {rev}")
        elif info.get("dir_info", {}).get("editable"):
            parts.append("editable")
        if info.get("url"):
            parts.append(f"from {info['url']}")
    location = getattr(dist, "_path", None)
    if location is not None:
        parts.append(f"at {Path(location).parent}")
    return "  " + "\n    ".join(parts)


def _packages_section(out: list[str]) -> None:
    out.append("packages")
    for name in REPORTED_PACKAGES:
        out.append(_package_line(name))


def _platform_section(out: list[str]) -> None:
    out.append("")
    out.append("platform")
    out.append(f"  python: {sys.version.splitlines()[0]}")
    out.append(f"  executable: {sys.executable}")
    out.append(f"  platform: {platform.platform()}")
    out.append(f"  cwd: {Path.cwd()}")
    # umask is only readable by setting it, so put it straight back.
    current = os.umask(0o022)
    os.umask(current)
    out.append(f"  umask: {current:04o}")
    if hasattr(os, "getuid"):
        out.append(f"  uid/gid: {os.getuid()}/{os.getgid()}")
    for var in ("CONDA_DEFAULT_ENV", "VIRTUAL_ENV", "GOOGLE_CLOUD_PROJECT"):
        if os.environ.get(var):
            out.append(f"  {var}: {os.environ[var]}")


def _memory_section(out: list[str]) -> None:
    out.append("")
    out.append("memory")
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        out.append("  psutil not installed")
        return
    mem = psutil.virtual_memory()
    out.append(f"  total: {_fmt_bytes(mem.total)}")
    out.append(f"  available: {_fmt_bytes(mem.available)}")
    out.append(f"  used: {_fmt_bytes(mem.used)} ({mem.percent:.1f}%)")
    # echopype's ParseEK.__should_use_swap compares projected demand against
    # this threshold; below it backscatter_r stays in memory, above it becomes
    # a dask array backed by a temporary zarr store. Two machines with
    # different RAM take different paths through the same conversion.
    out.append(
        f"  echopype swap threshold (total * 0.4): {_fmt_bytes(mem.total * 0.4)}"
    )


def _filesystem_of(path: Path) -> str | None:
    """Best-effort mount point and filesystem type from /proc/mounts."""
    try:
        raw = Path("/proc/mounts").read_text()
    except OSError:
        return None
    resolved = str(path.resolve())
    best: tuple[int, str] | None = None
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        mount, fstype = fields[1], fields[2]
        if resolved == mount or resolved.startswith(mount.rstrip("/") + "/"):
            if best is None or len(mount) > best[0]:
                best = (len(mount), f"{fstype} on {mount}")
    return best[1] if best else None


def _probe_local_dir(path: Path, out: list[str]) -> None:
    """Create, write, stat, and remove a nested tree under ``path``.

    Reproduces the shape a zarr store writes (nested group directories holding
    an array directory) so a permission or quota problem shows up here rather
    than several minutes into a conversion.
    """
    existed = path.exists()
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        out.append(f"    probe: cannot create directory: {exc}")
        return
    if not existed:
        out.append("    (did not exist; created for this probe)")

    try:
        usage = shutil.disk_usage(path)
        out.append(
            f"    free: {_fmt_bytes(usage.free)} of {_fmt_bytes(usage.total)}"
        )
    except OSError as exc:
        out.append(f"    free: unavailable ({exc})")

    fstype = _filesystem_of(path)
    if fstype:
        out.append(f"    filesystem: {fstype}")

    probe_root = path / _PROBE_TREE[0]
    try:
        if probe_root.exists():
            rmtree(probe_root)
        leaf = path.joinpath(*_PROBE_TREE)
        leaf.mkdir(parents=True)
        chunk = leaf / "0.0.0"
        chunk.write_bytes(b"aa-recipe doctor probe\n")
        modes = " ".join(
            f"{part}={stat.filemode(path.joinpath(*_PROBE_TREE[:i + 1]).stat().st_mode)}"
            for i, part in enumerate(_PROBE_TREE)
        )
        out.append(f"    probe: wrote {chunk.relative_to(path)}")
        out.append(f"    modes: {modes}")
        rmtree(probe_root)
        out.append("    probe: removed cleanly")
    except OSError as exc:
        out.append(f"    probe: FAILED {type(exc).__name__}: {exc}")
        out.append(f"    probe: leftover tree at {probe_root}")
        return
    if not existed:
        # Leave the tree exactly as found when the probe made the directory.
        try:
            path.rmdir()
        except OSError:
            pass


def _paths_section(out: list[str], paths: dict[str, str | None]) -> None:
    out.append("")
    out.append("configured paths")
    for label, value in paths.items():
        if not value:
            out.append(f"  {label}: (unset)")
            continue
        out.append(f"  {label}: {value}")
        if "://" in value:
            out.append("    remote: skipping local probe")
            continue
        _probe_local_dir(Path(value), out)


def build_report(
    config_source: str | None = None,
    paths: dict[str, str | None] | None = None,
) -> str:
    """Return the full diagnostic report as text."""
    out: list[str] = ["aa-recipe doctor", ""]
    _packages_section(out)
    _platform_section(out)
    _memory_section(out)
    out.append("")
    out.append("run config")
    out.append(f"  source: {config_source or '(none found)'}")
    _paths_section(out, paths or {})
    out.append("")
    return "\n".join(out)
