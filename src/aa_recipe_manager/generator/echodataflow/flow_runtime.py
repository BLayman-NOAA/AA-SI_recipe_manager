# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Helpers copied into every generated flow module.

The renderer copies the source of each function in ``HELPERS`` into the
generated module, so the flow needs nothing from the recipe manager at run
time. They live here as ordinary code so they can be tested directly. They may
use only ``os``, ``Path`` and what they import themselves, because those are
the generated module's only top level imports besides Prefect.
"""

from __future__ import annotations

import os
from pathlib import Path


def _as_items(value):
    """Return a map_over source as a list of items, as the executor does."""
    return value if isinstance(value, list) else [value]


def _item_stem(item, index):
    """Short name for one mapped item: its label, its file stem, or its index."""
    if isinstance(item, dict) and item.get("label"):
        return str(item["label"])
    if isinstance(item, (str, os.PathLike)):
        stem = Path(str(item)).stem
        if stem:
            return stem
    return f"{index:04d}"


def _item_stems(items):
    """Unique short names for mapped items, for task run names and store paths.

    A name shared by two items, such as files with one name in two folders,
    gets the item's index appended, so no two items write the same store.
    """
    stems = [_item_stem(item, index) for index, item in enumerate(items)]
    counts = {}
    for stem in stems:
        counts[stem] = counts.get(stem, 0) + 1
    return [
        f"{stem}_{index:04d}" if counts[stem] > 1 else stem
        for index, stem in enumerate(stems)
    ]


def _given(**kwargs):
    """Keyword arguments whose value is not None."""
    return {name: value for name, value in kwargs.items() if value is not None}


def _uniform_chunks(ds):
    """Rechunk dimensions whose dask chunks zarr cannot store.

    Zarr needs every chunk along a dimension to be the same size except a
    smaller last one. Concatenated or coarsened arrays often break that.
    """
    problem = {}
    for var in ds.variables.values():
        if var.chunks is None:
            continue
        for dim, chunks in zip(var.dims, var.chunks):
            if len(chunks) <= 1:
                continue
            first = chunks[0]
            if any(size != first for size in chunks[:-1]) or chunks[-1] > first:
                problem[dim] = max(problem.get(dim, 0), max(chunks))
    return ds.chunk(problem) if problem else ds


def _write_handoff(value, path):
    """Write a dataset, data array or EchoData to a zarr store.

    Returns the store path. None and other values are returned unchanged.
    """
    if value is None:
        return None
    import xarray as xr

    if isinstance(value, xr.DataArray):
        value = value.to_dataset(name="_da")
    if isinstance(value, xr.Dataset):
        target = f"{path}.zarr"
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        ds = value.copy()
        for var in ds.variables.values():
            # Text keeps no stored dtype or codecs: an encoding read from
            # another store can contradict the text's current dtype.
            kept = ("_FillValue", "units", "calendar")
            if var.dtype.kind not in "OSUT":
                kept += ("dtype",)
            var.encoding = {
                key: val for key, val in var.encoding.items() if key in kept
            }
        _uniform_chunks(ds).to_zarr(target, mode="w")
        return target
    if type(value).__name__ == "EchoData":
        target = f"{path}.ed.zarr"
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        value.to_zarr(target, overwrite=True)
        return target
    return value


def _open_handoff(value):
    """Reopen stores written by _write_handoff, lazily. Other values pass through."""
    if isinstance(value, list):
        return [_open_handoff(item) for item in value]
    if not isinstance(value, str):
        return value
    if value.endswith(".ed.zarr"):
        import echopype as ep

        return ep.open_converted(value, chunks={})
    if value.endswith(".zarr"):
        import xarray as xr

        ds = xr.open_dataset(value, engine="zarr", chunks={})
        if list(ds.data_vars) == ["_da"]:
            return ds["_da"]
        return ds
    return value


HELPERS = (
    _as_items,
    _item_stem,
    _item_stems,
    _given,
    _uniform_chunks,
    _write_handoff,
    _open_handoff,
)
