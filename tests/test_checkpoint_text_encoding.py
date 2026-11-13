# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Text variables carrying another store's encoding into a zarr checkpoint.

Newer xarray reads text from a Zarr store as numpy's ``StringDType`` and keeps
the store's variable length UTF-8 filter in the encoding. An op such as
compute_MVBS carries both onto its result, and a Zarr v2 write of the pair
records metadata zarr cannot read back ("No Zarr data type found that matches
{'name': '<U24', 'object_codec_id': 'vlen-utf8'}"). It stopped the
echodataflow reference recipe at its MVBS checkpoint under echopype 0.12.
"""

from __future__ import annotations

import numpy as np
import pytest
import xarray as xr
from numcodecs import VLenUTF8

from aa_recipe_manager.executor.checkpoint import (
    _clear_text_encoding,
    _serialize_output,
)
from aa_recipe_manager.storage import StorageLocation

_NAMES = ["WBT 400479-15 ES18_ES", "WBT 400503-15 ES70-7C_ES"]


def _read_back_dataset() -> xr.Dataset:
    """A dataset shaped like MVBS built from a reopened Sv checkpoint."""
    names = np.array(_NAMES, dtype=np.dtypes.StringDType())
    ds = xr.Dataset(
        {"Sv": ("channel", [-70.0, -65.0])},
        coords={"channel": names},
    )
    ds["channel"].encoding = {
        "chunks": (2,),
        "dtype": np.dtypes.StringDType(),
        "filters": (VLenUTF8(),),
    }
    ds["Sv"].encoding = {"dtype": np.dtype("float64"), "chunks": (2,)}
    return ds


@pytest.mark.parametrize("as_data_array", [False, True], ids=["dataset", "dataarray"])
def test_a_checkpoint_with_read_back_text_writes_and_reloads(tmp_path, as_data_array):
    ds = _read_back_dataset()
    value = ds["Sv"] if as_data_array else ds

    target, _ = _serialize_output(value, StorageLocation.parse(str(tmp_path / "out")))
    back = xr.open_zarr(target.as_local_path())

    assert [str(name) for name in back["channel"].values] == _NAMES
    np.testing.assert_array_equal(back[back_var(as_data_array)].values, [-70.0, -65.0])


def back_var(as_data_array: bool) -> str:
    return "_da" if as_data_array else "Sv"


def test_only_text_encoding_is_cleared_and_the_input_is_untouched():
    ds = _read_back_dataset()

    cleared = _clear_text_encoding(ds)

    assert cleared["channel"].encoding == {"chunks": (2,)}
    assert cleared["Sv"].encoding == {"dtype": np.dtype("float64"), "chunks": (2,)}
    assert "filters" in ds["channel"].encoding


def test_a_tree_with_read_back_text_writes_and_reloads(tmp_path):
    """The DataTree branch, as an echogram pyramid merged from checkpoints."""
    tree = xr.DataTree.from_dict({
        "level_0": _read_back_dataset(),
        "level_1": _read_back_dataset(),
    })

    target, fmt = _serialize_output(tree, StorageLocation.parse(str(tmp_path / "t")))
    back = xr.open_datatree(target.as_local_path(), engine="zarr")

    assert fmt == "zarr_tree"
    assert [str(n) for n in back["level_1"]["channel"].values] == _NAMES
    assert "filters" in tree["level_0"]["channel"].encoding


def test_a_dataset_without_text_encoding_is_returned_as_is():
    ds = xr.Dataset({"Sv": ("ping", [1.0, 2.0])}, coords={"channel": "a"})

    assert _clear_text_encoding(ds) is ds
