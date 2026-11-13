# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the helpers copied into generated echodataflow flows."""

from __future__ import annotations

import numpy as np
import xarray as xr

from aa_recipe_manager.generator.echodataflow import flow_runtime as rt


def _dataset(n: int = 10) -> xr.Dataset:
    return xr.Dataset(
        {"Sv": (("ping_time", "range_sample"), np.arange(n * 3.0).reshape(n, 3))},
        coords={"ping_time": np.arange(n), "channel": "ch0"},
    )


def test_as_items_lists_only_lists():
    assert rt._as_items([1, 2]) == [1, 2]
    assert rt._as_items((1, 2)) == [(1, 2)]
    assert rt._as_items("one") == ["one"]


def test_item_stem_prefers_a_label_then_a_path_stem():
    assert rt._item_stem({"label": "20240801T120000"}, 3) == "20240801T120000"
    assert rt._item_stem("/data/D20240801-T120000.raw", 3) == "D20240801-T120000"
    assert rt._item_stem(42, 3) == "0003"


def test_given_drops_none():
    assert rt._given(a=1, b=None, c=0) == {"a": 1, "c": 0}


def test_uniform_chunks_fixes_a_ragged_dimension():
    ds = _dataset().chunk({"ping_time": (3, 2, 5)})

    fixed = rt._uniform_chunks(ds)

    assert fixed["Sv"].chunks[0] == (5, 5)


def test_a_dataset_round_trips_lazily(tmp_path):
    ds = _dataset().chunk({"ping_time": (3, 7)})
    ds["Sv"].encoding["chunks"] = (99, 3)

    path = rt._write_handoff(ds, str(tmp_path / "step" / "item"))
    back = rt._open_handoff(path)

    assert path.endswith("item.zarr")
    assert back["Sv"].chunks is not None
    xr.testing.assert_identical(back.compute(), ds.compute())


def test_a_data_array_comes_back_as_a_data_array(tmp_path):
    da = _dataset()["Sv"].rename("depth_line")

    back = rt._open_handoff(rt._write_handoff(da, str(tmp_path / "line")))

    assert isinstance(back, xr.DataArray)
    np.testing.assert_array_equal(back.values, da.values)


def test_text_read_back_from_another_store_still_writes(tmp_path):
    from numcodecs import VLenUTF8

    names = np.array(["WBT 1-ES18", "WBT 2-ES38"], dtype=np.dtypes.StringDType())
    ds = xr.Dataset({"Sv": ("channel", [1.0, 2.0])}, coords={"channel": names})
    ds["channel"].encoding = {"dtype": names.dtype, "filters": (VLenUTF8(),)}

    back = rt._open_handoff(rt._write_handoff(ds, str(tmp_path / "mvbs")))

    assert [str(n) for n in back["channel"].values] == ["WBT 1-ES18", "WBT 2-ES38"]


def test_none_and_plain_values_pass_through(tmp_path):
    assert rt._write_handoff(None, str(tmp_path / "x")) is None
    assert rt._write_handoff([1, 2], str(tmp_path / "x")) == [1, 2]
    assert rt._open_handoff([None, "plain text", 3]) == [None, "plain text", 3]
