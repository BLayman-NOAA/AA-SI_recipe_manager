# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Checkpointing an xarray DataTree.

A DataTree is how a multi-group artifact reaches the cache: an echogram
pyramid is a node per level, and the viewer reads those subgroups straight out
of wherever the checkpoint put them. Before this it fell through to the pickle
branch, which stores the same data in a form nothing else can open.
"""

import numpy as np
import pytest
import xarray as xr

from aa_recipe_manager.executor import checkpoint as cp


def tree() -> xr.DataTree:
    """Two groups with an attribute on the root, shaped like a pyramid."""
    fine = xr.Dataset({"Sv": (("ping", "sample"), np.arange(24.0).reshape(4, 6))})
    coarse = xr.Dataset({"Sv": (("ping", "sample"), np.arange(12.0).reshape(2, 6))})
    built = xr.DataTree.from_dict({"/0": fine, "/1": coarse})
    built.attrs["multiscales"] = [{"name": "Sv", "datasets": [{"path": "0"}]}]
    return built


def test_a_datatree_is_written_as_zarr_not_pickled():
    assert cp._would_pickle(tree(), "zarr") is False


def test_pickle_is_still_available_when_asked_for():
    assert cp._would_pickle(tree(), "pickle") is True


def test_a_datatree_round_trips_through_zarr(tmp_path):
    source = tree()
    store = tmp_path / "pyramid.zarr"
    cp._write_tree_consolidated_once(source, str(store))

    back = xr.open_datatree(str(store), engine="zarr", chunks={})
    assert sorted(back.children) == ["0", "1"]
    for level in ("0", "1"):
        np.testing.assert_array_equal(
            np.asarray(source[level].to_dataset()["Sv"].values),
            np.asarray(back[level].to_dataset()["Sv"].values),
        )


def test_the_root_attributes_survive(tmp_path):
    """The multiscales block is what makes the store readable as a pyramid."""
    source = tree()
    store = tmp_path / "pyramid.zarr"
    cp._write_tree_consolidated_once(source, str(store))

    back = xr.open_datatree(str(store), engine="zarr", chunks={})
    assert back.attrs["multiscales"][0]["name"] == "Sv"


def test_the_store_is_consolidated_once(tmp_path):
    """Reading it back costs one request rather than a listing of every key."""
    import zarr

    store = tmp_path / "pyramid.zarr"
    cp._write_tree_consolidated_once(tree(), str(store))
    root = zarr.open_group(str(store), mode="r")
    assert sorted(root.group_keys()) == ["0", "1"]
