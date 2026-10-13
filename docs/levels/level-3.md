# Level 3: gridding

Level 3 of the road map averages the cleaned Sv onto a regular grid of depth
and time cells, the form most analyses and models take as input.

| Road map | Recipe steps |
| --- | --- |
| Grid the data | [`mask_sparse_bins`](../ops/mask_sparse_bins.md) drops grid cells that are mostly empty after masking, then [`compute_mvbs`](../ops/compute_mvbs.md) averages Sv in each cell: mean volume backscattering strength, MVBS. |
| Provide validated data at the grid resolution | [`compute_per_cell_statistics`](../ops/compute_per_cell_statistics.md) computes per-cell statistics such as the coefficient of variation, from the Sv before the sparse-cell mask, for use as machine learning features. |

The two settings that matter most are the cell size, `range_bin` and
`ping_time_bin`, and `nan_threshold`, the fraction of a cell that may be masked
before the whole cell is dropped.

The road map's level 3B quality control has no recipe steps yet.

## HB1603 {#hb1603}

Still per file: each file's dive pings are gridded onto a depth axis pinned to
the same length for every file, 2 m by 10 s, and only then merged. Both grids,
MVBS and the per-cell statistics, are merged across files with
[`merge_datasets`](../ops/merge_datasets.md), averaging the cells a file
boundary splits.

--8<-- "data/partials/hb1603-level-3.md"

Part of the [HB1603 dive analysis](hb1603-dive-analysis.md).

## HB2407 {#hb2407}

The survey was merged in level 2, so gridding runs once on the whole survey, at
4 m by 4 s.

--8<-- "data/partials/hb2407-level-3.md"

Part of the [HB2407 herring pipeline](hb2407-herring-per-file.md).
