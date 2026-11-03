=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-level-3" data-drill="inc:sub_recipes/processing_lvl_3.yaml" markdown>

    - `mask_sparse`: [`mask_sparse_bins`](../ops/mask_sparse_bins.md)
    - `rechunk_before_mvbs`: [`rechunk_dataset`](../ops/rechunk_dataset.md)
    - `compute_mvbs`: [`compute_mvbs`](../ops/compute_mvbs.md)
    - `compute_cell_stats`: [`compute_per_cell_statistics`](../ops/compute_per_cell_statistics.md)

    </div>

=== "YAML"

    [Download `processing_lvl_3.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_3.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/sub_recipes/processing_lvl_3.yaml

    # Processing level 3: sparse masking, MVBS, and per-cell statistics.
    # The per-cell statistics come from the pre-mask Sv, for ML use.

    recipe:
      name: "processing_lvl_3"
      description: "Sparse masking, MVBS generation, and per-cell statistics."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      sv_clean_unmasked:
        type: dataset
        description: >
          Noise-removed Sv before sparse-bin masking; the sparse mask and the
          per-cell statistics are computed from it.
      range_bin:
        type: str
        default: "20m"
        description: >
          Depth bin height of the MVBS grid, as a length string such as "2m". The
          sparse-bin mask and the cell statistics use the same bin.
      ping_time_bin:
        type: str
        default: "20s"
        description: >
          Ping-time bin width of the MVBS grid, as a duration string such as "10s".
          The sparse-bin mask and the cell statistics use the same bin.
      nan_threshold:
        type: float
        default: 0.6
        description: "Fraction of NaN samples (0 to 1) above which the sparse-bin mask drops a bin."
      mvbs_chunk_ping_time:
        type: int
        default: 1000
        description: >
          Ping-time chunk size set before the MVBS step, so it runs in dask chunks
          rather than as one in-memory block.

    steps:

      - id: mask_sparse
        op: mask_sparse_bins
        checkpoint: always
        inputs:
          ds_Sv: ${inputs.sv_clean_unmasked}
        params:
          range_bin: ${inputs.range_bin}
          ping_time_bin: ${inputs.ping_time_bin}
          nan_threshold: ${inputs.nan_threshold}

      # A separate rechunk step because mask_sparse_bins loads Sv into memory;
      # standalone, it survives that step being removed or reordered.
      - id: rechunk_before_mvbs
        op: rechunk_dataset
        inputs:
          ds_Sv: ${mask_sparse.ds_Sv}
        params:
          ping_time_chunk: ${inputs.mvbs_chunk_ping_time}

      - id: compute_mvbs
        op: compute_mvbs
        checkpoint: always
        inputs:
          ds_Sv: ${rechunk_before_mvbs.ds_Sv}
        params:
          range_bin: ${inputs.range_bin}
          ping_time_bin: ${inputs.ping_time_bin}

      - id: compute_cell_stats
        op: compute_per_cell_statistics
        inputs:
          ds_Sv: ${inputs.sv_clean_unmasked}
        params:
          range_bin: ${inputs.range_bin}
          ping_time_bin: ${inputs.ping_time_bin}
          statistics:
            - cv
    ```

=== "Python"

    The function calls this part of the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_utils.utils import mask_sparse_bins
    from aa_si_utils.utils import rechunk_dataset
    from echopype.commongrid import compute_MVBS
    from aa_si_ml.ml import compute_per_cell_statistics

    # mask_sparse: Remove spatiotemporal bins with fewer valid samples than a
    # threshold, reducing noise in MVBS computation.
    mask_sparse_ds_Sv = mask_sparse_bins(
        ds_Sv=remove_noise_ds_Sv,
        range_bin='4m',
        ping_time_bin='4s',
        nan_threshold=0.95,
    )

    # rechunk_before_mvbs: Rechunk a Dataset along ping_time so the steps after it
    # can run dask-parallel.
    rechunk_before_mvbs_ds_Sv = rechunk_dataset(ds_Sv=mask_sparse_ds_Sv, ping_time_chunk=1000)

    # compute_mvbs: Compute Mean Volume Backscattering Strength by averaging Sv
    # over range and time bins.
    ds_MVBS = compute_MVBS(
        ds_Sv=rechunk_before_mvbs_ds_Sv,
        range_bin='4m',
        ping_time_bin='4s',
    )

    # compute_cell_stats: Compute statistics within each MVBS cell (such as the
    # coefficient of variation) from fine-resolution Sv, at MVBS bin resolution.
    compute_cell_stats_ds_Sv = compute_per_cell_statistics(
        ds_Sv=remove_noise_ds_Sv,
        range_bin='4m',
        ping_time_bin='4s',
        statistics=['cv'],
    )
    ```
