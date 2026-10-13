# Level 4: machine learning

Level 4 of the road map applies AI and machine learning models to the gridded
data. The examples cluster the grid cells by how their backscatter varies
across frequency, with HDBSCAN, to separate kinds of scatterers without
labelled training data.

| Step | What it does |
| --- | --- |
| [`reshape_for_ml`](../ops/reshape_for_ml.md) | Turns the MVBS grid into one row per cell, with each frequency's Sv as features. |
| [`add_auxiliary_features`](../ops/add_auxiliary_features.md) | Adds further features, here each cell's coefficient of variation from level 3. |
| [`normalize_ml_data`](../ops/normalize_ml_data.md) | Normalizes the features so none dominates by scale. |
| [`run_hdbscan`](../ops/run_hdbscan.md) | Clusters the cells. |
| [`assign_clusters_to_all_points`](../ops/assign_clusters_to_all_points.md) | Labels cells left out of a sampled fit. |
| [`embed_clustering_results`](../ops/embed_clustering_results.md) | Puts the labels back onto the grid. |
| [`plot_clustering_report`](../ops/plot_clustering_report.md) | Draws the clustered echogram, per-cluster statistics and the cluster hierarchy. |

HDBSCAN cluster numbers are not stable from run to run. Read clusters by their
statistics, not their ids.

## HB1603 {#hb1603}

One clustering of all 13 dives pooled together, so a cluster means the same
thing in every dive. The fitted model is checkpointed, so redrawing the report
never refits it.

--8<-- "data/partials/hb1603-level-4.md"

Part of the [HB1603 dive analysis](hb1603-dive-analysis.md).

## HB2407 {#hb2407}

One survey day. HDBSCAN is fitted on up to 1,000,000 cells and there is no
`assign_clusters_to_all_points` step, so on a larger grid the cells past that
limit would stay unlabelled. It also draws the normalized features with
[`plot_ml_echogram`](../ops/plot_ml_echogram.md) before clustering. The
HDBSCAN settings are set on the include in the parent recipe.

--8<-- "data/partials/hb2407-level-4.md"

Part of the [HB2407 herring pipeline](hb2407-herring-per-file.md).
