# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""What a chain instance sends back to the client, and what the client keeps.

The client appends a MemberResult per member per instance and holds them until
the chain finalizes, so an uncheckpointed member's output is retained once per
instance. Within a chain each member reads the previous one's value out of the
element context in the worker, so a member nothing outside the chain reads is
retained for no one. Measured on HB1603's per-file chain that came to 76 MiB an
instance, 57 GiB by instance 766 of 3322.

Small JSON-native results are still carried: result.outputs is expected to hold
them and they cost nothing.
"""

from __future__ import annotations

import numpy as np
import xarray as xr

from aa_recipe_manager.executor.engine.tasks import _inline_outputs


def _heavy():
    return {"ds_Sv": xr.Dataset({"Sv": (("x",), np.zeros(1000))})}


def _light():
    return {"n": 8, "path": "exe_temp/f.zarr"}


def test_a_checkpointed_member_sends_nothing_back():
    assert _inline_outputs(_heavy(), checkpointed=True, externally_read=True) is None
    assert _inline_outputs(_light(), checkpointed=True, externally_read=False) is None


def test_a_heavy_result_no_one_outside_reads_is_dropped():
    assert _inline_outputs(_heavy(), checkpointed=False, externally_read=False) is None


def test_a_heavy_result_something_outside_reads_is_kept():
    out = _inline_outputs(_heavy(), checkpointed=False, externally_read=True)

    assert out is not None and "ds_Sv" in out


def test_a_small_result_is_kept_either_way():
    """result.outputs is expected to carry these; they cost nothing."""
    assert _inline_outputs(_light(), checkpointed=False, externally_read=False) == _light()
    assert _inline_outputs(_light(), checkpointed=False, externally_read=True) == _light()


def test_an_empty_result_is_an_empty_dict_not_none():
    """None means 'reload from the checkpoint'; {} means 'produced nothing'."""
    assert _inline_outputs({}, checkpointed=False, externally_read=False) == {}
    assert _inline_outputs(None, checkpointed=False, externally_read=False) == {}


def test_a_mixed_result_counts_as_heavy():
    mixed = {"n": 8, "ds_Sv": xr.Dataset({"Sv": (("x",), np.zeros(10))})}

    assert _inline_outputs(mixed, checkpointed=False, externally_read=False) is None
