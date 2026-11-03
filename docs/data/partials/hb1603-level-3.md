=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-level-3" data-drill="inc:sub_recipes/processing_lvl_3_user.yaml" markdown>

    - `dive_mask_sparse`: [`mask_sparse_bins`](../ops/mask_sparse_bins.md)
    - `dive_file_mvbs`: [`compute_mvbs`](../ops/compute_mvbs.md)
    - `dive_cell_stats`: [`compute_per_cell_statistics`](../ops/compute_per_cell_statistics.md)
    - `merge_dive_files_mvbs`: [`merge_datasets`](../ops/merge_datasets.md)
    - `rechunk_dive_mvbs`: [`rechunk_dataset`](../ops/rechunk_dataset.md)
    - `merge_dive_files_cell_stats`: [`merge_datasets`](../ops/merge_datasets.md)

    </div>

=== "YAML"

    [Download `processing_lvl_3_user.yaml`](../recipes/HB1603/sub_recipes/processing_lvl_3_user.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/processing_lvl_3_user.yaml

    # Processing level 3 (user): sparse masking, MVBS and per-cell statistics.
    #
    #   [per file]  mask_sparse_bins -> compute_mvbs (pinned depth grid)
    #               compute_per_cell_statistics (from the pre-sparse Sv)
    #   [fan-in]    merge the files a dive window touched -> the pooled dive grid
    #
    # The per-file steps continue level 2b's chain: include this directly after it
    # with the same per_file_source. The fan-in is here, not at level 2, because
    # HB1603 files only share an axis once they are on the pinned depth grid. The
    # per-cell CV comes from the seabed-masked, denoised Sv, so no cell under the
    # seabed carries one.
    #
    # THE FAN-IN AVERAGES
    #
    #   Time bins anchor to a global origin, so a file boundary inside a bin makes
    #   both files emit it; on_duplicate "mean" keeps the merged index unique.
    #
    # THE POOLED GRID IS THE ANALYSIS SET
    #
    #   dive_sv already kept each dive's pings, snapped to whole bins, so level 4
    #   reads the merged grid directly. Overlapping dives (OE29/OE32 on 7 July,
    #   OE61/OE62/OE64 on 25 July) share pings, which the union select keeps once.
    #   Per-dive work happens downstream: generate_sv_codes and the report read
    #   each dive's lines onto its own slice, since two whales under one ping
    #   cannot share one line variable. A window that selects no pings is reported
    #   there as a warning.

    recipe:
      name: "hb1603_processing_lvl_3_user"
      description: "Per-file sparse masking, MVBS on a pinned depth grid and per-cell CV, fanned in to the pooled dive grid."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      per_file_source:
        type: str
        description: >
          The step output list the per-file steps map over, one instance per
          entry, normally ${query_ncei.raw_urls}. Required; give every level in
          the chain the same value.
      range_bin:
        type: str
        default: "2m"
        description: >
          Depth bin height of the MVBS grid, as a length string such as "2m". The
          sparse-bin mask and the cell statistics use the same bin.
      ping_time_bin:
        type: str
        default: "10s"
        description: >
          Ping-time bin width of the MVBS grid, as a duration string such as
          "10s". The sparse-bin mask and the cell statistics use the same bin.
      # "depth" needs the level 2 chain to route through ep_add_depth (dive_depth
      # here). All binning steps must take the same value.
      range_var:
        type: str
        default: "depth"
        description: >
          Variable the bins are built on: "depth" (below the surface, shared
          across files and channels) or "echo_range" (from the transducer face).
      nan_threshold:
        type: float
        default: .99
        description: "Fraction of NaN samples (0 to 1) above which the sparse-bin mask drops a bin."
      mvbs_range_var_max:
        type: str
        default: "1810m"
        description: >
          Deepest depth the MVBS grid covers, such as "1810m", pinned so every
          file shares one depth axis. It must clear the range crop plus the
          transducer depth, or the deepest bins are dropped.
      dive_chunk_ping_time:
        type: int
        default: 1000
        description: "Ping-time chunk size of the pooled dive store, counted in MVBS time bins."

    steps:

      - id: dive_mask_sparse
        op: mask_sparse_bins
        map_over: ${inputs.per_file_source}
        inputs:
          ds_Sv: ${dive_remove_noise.ds_Sv}
        params:
          range_bin: ${inputs.range_bin}
          ping_time_bin: ${inputs.ping_time_bin}
          range_var: ${inputs.range_var}
          nan_threshold: ${inputs.nan_threshold}

      # Pinned depth axis, so every file gets the same length and the fan-in has
      # nothing to pad.
      #
      # checkpoint: always is required here and on dive_cell_stats: the fan-in
      # collects them, and uncheckpointed they would reach it as lazy graphs over
      # scratch stores read_raw has deleted, which zarr reads back as all NaN with
      # no error.
      - id: dive_file_mvbs
        op: compute_mvbs
        map_over: ${inputs.per_file_source}
        checkpoint: always
        inputs:
          ds_Sv: ${dive_mask_sparse.ds_Sv}
        params:
          range_bin: ${inputs.range_bin}
          ping_time_bin: ${inputs.ping_time_bin}
          range_var: ${inputs.range_var}
          range_var_max: ${inputs.mvbs_range_var_max}

      # From the same pre-sparse Sv as the survey tier, now seabed-masked too, so
      # no cell under the seabed carries a CV.
      - id: dive_cell_stats
        op: compute_per_cell_statistics
        map_over: ${inputs.per_file_source}
        checkpoint: always
        inputs:
          ds_Sv: ${dive_remove_noise.ds_Sv}
        params:
          range_bin: ${inputs.range_bin}
          ping_time_bin: ${inputs.ping_time_bin}
          range_var: ${inputs.range_var}
          range_var_max: ${inputs.mvbs_range_var_max}
          statistics:
            - cv
          cells_only: true

      - id: merge_dive_files_mvbs
        op: merge_datasets
        collect: ${dive_file_mvbs.ds_MVBS}
        inputs:
          datasets: ${dive_file_mvbs.ds_MVBS}
        params:
          dim: "ping_time"
          on_duplicate: "mean"

      # The pooled dive grid, what this level produces. Checkpointed, and what
      # level 4 clusters.
      - id: rechunk_dive_mvbs
        op: rechunk_dataset
        checkpoint: always
        inputs:
          ds_Sv: ${merge_dive_files_mvbs.merged}
        params:
          ping_time_chunk: ${inputs.dive_chunk_ping_time}

      # The matching per-cell statistics, on the cell grid's own time axis.
      - id: merge_dive_files_cell_stats
        op: merge_datasets
        collect: ${dive_cell_stats.ds_Sv}
        checkpoint: always
        inputs:
          datasets: ${dive_cell_stats.ds_Sv}
        params:
          dim: "cell_ping_time"
          on_duplicate: "mean"
    ```

=== "Python"

    The function calls this part of the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_utils.utils import mask_sparse_bins
    from echopype.commongrid import compute_MVBS
    from aa_si_ml.ml import compute_per_cell_statistics
    from aa_si_utils.utils import concat_datasets
    from aa_si_utils.utils import rechunk_dataset

    dive_mask_sparse_ds_Sv = []
    ds_MVBS = []
    dive_cell_stats_ds_Sv = []
    for _item in raw_urls:
        ...  # HB1603 level 1, raw ingest

        ...  # HB1603 level 2, calibrated Sv, masks and noise removal

        # dive_mask_sparse: Remove spatiotemporal bins with fewer valid samples than a
        # threshold, reducing noise in MVBS computation.
        _iter_dive_mask_sparse_ds_Sv = mask_sparse_bins(
            ds_Sv=_iter_dive_remove_noise_ds_Sv,
            range_bin='2m',
            ping_time_bin='10s',
            range_var='depth',
            nan_threshold=0.99,
        )
        dive_mask_sparse_ds_Sv.append(_iter_dive_mask_sparse_ds_Sv)

        # dive_file_mvbs: Compute Mean Volume Backscattering Strength by averaging Sv
        # over range and time bins.
        _iter_ds_MVBS = compute_MVBS(
            ds_Sv=_iter_dive_mask_sparse_ds_Sv,
            range_bin='2m',
            ping_time_bin='10s',
            range_var='depth',
            range_var_max='3020m',
        )
        ds_MVBS.append(_iter_ds_MVBS)

        # dive_cell_stats: Compute statistics within each MVBS cell (such as the
        # coefficient of variation) from fine-resolution Sv, at MVBS bin resolution.
        _iter_dive_cell_stats_ds_Sv = compute_per_cell_statistics(
            ds_Sv=_iter_dive_remove_noise_ds_Sv,
            range_bin='2m',
            ping_time_bin='10s',
            range_var='depth',
            range_var_max='3020m',
            statistics=['cv'],
            cells_only=True,
        )
        dive_cell_stats_ds_Sv.append(_iter_dive_cell_stats_ds_Sv)

    # merge_dive_files_mvbs: Concatenate a collected list of Datasets into one
    # along dim, for example per-file MVBS gathered with collect.
    merge_dive_files_mvbs_merged = concat_datasets(
        datasets=ds_MVBS,
        dim='ping_time',
        on_duplicate='mean',
    )

    # rechunk_dive_mvbs: Rechunk a Dataset along ping_time so the steps after it
    # can run dask-parallel.
    rechunk_dive_mvbs_ds_Sv = rechunk_dataset(ds_Sv=merge_dive_files_mvbs_merged, ping_time_chunk=1000)

    # merge_dive_files_cell_stats: Concatenate a collected list of Datasets into
    # one along dim, for example per-file MVBS gathered with collect.
    merge_dive_files_cell_stats_merged = concat_datasets(
        datasets=dive_cell_stats_ds_Sv,
        dim='cell_ping_time',
        on_duplicate='mean',
    )
    ```
