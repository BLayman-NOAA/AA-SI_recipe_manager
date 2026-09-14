# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Chunk sizing applied to a Dataset on its way into a zarr checkpoint.

``_zarr_uniform_chunks`` exists to make ragged dask chunking Zarr-writable, and
sized every block after the dimension's largest existing block. That is right
for the coarsen/reindex shapes it was written for, where the blocks are already
small, and wrong for an N-way fan-in: a survey concatenated from thousands of
per-file stores has one block per file along the concat dimension, so the rule
sized every block after the single longest file in the cruise and the write then
held that much per in-flight task. These tests pin both behaviours.
"""

from __future__ import annotations

import dask.array as da
import numpy as np
import xarray as xr

from aa_recipe_manager.executor.checkpoint import (
    _CHUNK_TARGET_ENV_VAR,
    _chunk_target_bytes,
    _slice_bytes,
    _zarr_uniform_chunks,
)


def _zarr_writable(chunks: tuple[int, ...]) -> bool:
    """Zarr's rule: uniform interior, final block no larger than the first."""
    return all(c == chunks[0] for c in chunks[:-1]) and chunks[-1] <= chunks[0]


def _dataset(blocks: list[int], range_sample: int = 64, channel: int = 4):
    """A Dataset chunked one block per entry in *blocks* along ping_time."""
    arr = da.zeros(
        (channel, sum(blocks), range_sample),
        chunks=(channel, tuple(blocks), range_sample),
        dtype="float32",
    )
    return xr.Dataset(
        {"Sv": (("channel", "ping_time", "range_sample"), arr)},
        coords={
            "ping_time": np.arange(sum(blocks)) * np.timedelta64(1, "s")
            + np.datetime64("2016-06-27")
        },
    )


def test_well_formed_chunks_are_left_alone():
    ds = _dataset([100] * 5 + [40])
    assert _zarr_uniform_chunks(ds) is ds


def test_coarsen_tail_is_squared_up_without_the_cap_binding():
    # The shape the function was written for: a final block larger than the
    # first. Blocks are small, so the byte cap must not change the answer.
    ds = _dataset([103] * 9 + [105])
    out = _zarr_uniform_chunks(ds)
    chunks = out["Sv"].chunks[1]
    assert chunks[0] == 105
    assert _zarr_writable(chunks)


def test_n_way_fan_in_is_capped_by_bytes_not_by_the_longest_block():
    # One block per file, one unusually long file among many ordinary ones.
    blocks = [512] * 400
    blocks[100] = 4000
    ds = _dataset(blocks, range_sample=9400)

    out = _zarr_uniform_chunks(ds)
    chunks = out["Sv"].chunks[1]

    assert _zarr_writable(chunks)
    assert chunks[0] < 4000, "the longest file must not size every block"
    assert chunks[0] * _slice_bytes(ds, "ping_time") <= _chunk_target_bytes()


def test_cap_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv(_CHUNK_TARGET_ENV_VAR, "0")
    blocks = [512] * 50
    blocks[10] = 4000
    ds = _dataset(blocks, range_sample=9400)

    chunks = _zarr_uniform_chunks(ds)["Sv"].chunks[1]
    assert chunks[0] == 4000
    assert _zarr_writable(chunks)


def test_slice_bytes_measures_the_widest_variable():
    ds = _dataset([100, 100], range_sample=64)
    ds["narrow"] = (("ping_time",), da.zeros(200, chunks=100, dtype="float32"))
    # 4 channels x 64 range_sample x 4 bytes dominates the 4-byte 1-D variable.
    assert _slice_bytes(ds, "ping_time") == 4 * 64 * 4
