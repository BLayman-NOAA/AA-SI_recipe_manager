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
    #
    # Masks sparse bins in the clean Sv dataset and computes MVBS from the result.
    # Per-cell statistics come from the pre-mask Sv dataset, for ML use.

    recipe:
      name: "processing_lvl_3"
      description: "Sparse masking, MVBS generation, and per-cell statistics."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      sv_clean_unmasked:
        type: dataset
        description: "Clean Sv dataset after noise removal and before sparse-bin masking."
      range_bin:
        type: str
        default: "20m"
        description: "Range bin size shared by sparse-bin masking, MVBS generation, and per-cell statistics."
      ping_time_bin:
        type: str
        default: "20s"
        description: "Ping-time bin size shared by sparse-bin masking, MVBS generation, and per-cell statistics."
      nan_threshold:
        type: float
        default: 0.6
        description: "Fraction of NaN samples in a bin above which the sparse-bin mask excludes it."
      mvbs_chunk_ping_time:
        type: int
        default: 1000
        description: >
          Ping-time chunk size before compute_mvbs, so it runs in dask chunks. A
          separate rechunk step sets it, since mask_sparse_bins loads Sv into memory.

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

=== "Notebook"

    These are the cells that run this level, taken from the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb). They use variables earlier levels define, so run them from the full notebook.

    #### Step: `mask_sparse`
    **Op:** `mask_sparse_bins`

    Remove spatiotemporal bins with fewer valid samples than a threshold, reducing noise in MVBS computation.

    **Parameters:**
    - `range_bin` str - Range bin size (e.g. '2m')
    - `ping_time_bin` str - Ping-time bin size (e.g. '10s')
    - `nan_threshold` float - Fraction of NaN values above which a bin is masked (0.0 to 1.0)
    - `range_var` str - Meter-valued variable to bin along, which must match the value given to compute_mvbs and compute_per_cell_statistics or the sparse mask lands on a different grid than the one it is meant to clean. 'echo_range' measures from the transducer face, 'depth' from the surface and needs the dataset to carry a depth variable. The bin edges are read from the first ping of the first channel, so a range variable that varies from ping to ping, as depth does under platform vertical offsets, is approximated by its first ping.

    ```python
    # --- Parameters ---
    _mask_sparse__range_bin = '4m'
    _mask_sparse__ping_time_bin = '4s'
    _mask_sparse__nan_threshold = 0.95

    mask_sparse_ds_Sv = mask_sparse_bins(
        ds_Sv=remove_noise_ds_Sv,
        range_bin=_mask_sparse__range_bin,
        ping_time_bin=_mask_sparse__ping_time_bin,
        nan_threshold=_mask_sparse__nan_threshold,
    )
    ```

    #### Step: `rechunk_before_mvbs`
    **Op:** `rechunk_dataset`

    Rechunk a Dataset along ping_time. A standalone step rather than a parameter on any particular masking or reduction op, so a recipe can insert (or remove) chunking wherever a downstream op should run dask-parallel, independent of which step happens to run immediately before it.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk size along ping_time.

    ```python
    # --- Parameters ---
    _rechunk_before_mvbs__ping_time_chunk = 1000

    rechunk_before_mvbs_ds_Sv = rechunk_dataset(ds_Sv=mask_sparse_ds_Sv, ping_time_chunk=_rechunk_before_mvbs__ping_time_chunk)
    ```

    #### Step: `compute_mvbs`
    **Op:** `compute_mvbs`

    Compute Mean Volume Backscattering Strength by averaging Sv over range and time bins. Returns a gridded MVBS Dataset.
    This step is echopype.commongrid.compute_MVBS called directly, so the bin edges and the averaging (in the linear domain, converted back to dB) are echopype's.
    Every sample is assigned to a bin by the value of its range variable rather than by its range_sample index, so channels recorded at different sample intervals land in the same physical bins. This is what makes MVBS safe for arithmetic across channels on data whose channels do not share a geometry.
    The flox_kwargs passthrough echopype accepts is not exposed, since a params block can only supply named arguments.

    **Parameters:**
    - `range_bin` str (m) - Depth bin size for averaging (e.g. '2m')
    - `ping_time_bin` str (s) - Ping-time bin size for averaging (e.g. '10s')
    - `range_var` str - Variable to bin along: 'echo_range' for range from the transducer face, or 'depth' for depth below the surface. 'depth' requires the dataset to carry a depth variable, which ep_add_depth adds; binning by it is what puts channels mounted at different depths on one vertical reference.

    - `range_var_max` str (m) - Deepest value the bins cover, as a unit string (e.g. '2100m'). When unset, echopype reads the maximum from the data, which forces a compute on a lazy dataset. Supplying it keeps the step lazy.

    - `closed` str - Which side of each bin interval is closed: 'left' or 'right'
    - `skipna` bool - Skip NaN values when averaging. When false, any NaN in a bin makes the whole bin NaN, so masked data collapses.

    - `fill_value` float - Value for bins holding no data to aggregate. echopype defaults to NaN, which YAML cannot express here, so leaving this unset keeps that default.

    - `method` str - Flox reduction strategy for dask arrays, normally 'map-reduce'. Any other value requires reindex to be set to null in the same params block, since echopype rejects a non-null reindex outside map-reduce and its own default for reindex is false rather than null.

    - `reindex` bool - Reindex at the blockwise stage rather than after it. False uses less memory and more time. Only meaningful when method is 'map-reduce'.

    ```python
    # --- Parameters ---
    _compute_mvbs__range_bin = '4m'
    _compute_mvbs__ping_time_bin = '4s'

    ds_MVBS = compute_MVBS(
        ds_Sv=rechunk_before_mvbs_ds_Sv,
        range_bin=_compute_mvbs__range_bin,
        ping_time_bin=_compute_mvbs__ping_time_bin,
    )
    ```

    #### Step: `compute_cell_stats`
    **Op:** `compute_per_cell_statistics`

    Compute per-MVBS-cell statistics (e.g. coefficient of variation) from fine-resolution Sv data before masking or MVBS averaging. Results are stored as new DataArrays in the returned dataset at MVBS-bin resolution. Must be called with the same range_bin, ping_time_bin and range_var values used by mask_sparse_bins and compute_mvbs so that bin grids align exactly. Pass the returned dataset to add_auxiliary_features via the ds_sv input to absorb the statistics into the ML feature matrix.

    **Parameters:**
    - `range_bin` str - Range bin size. Must match mask_sparse_bins and compute_mvbs (e.g. '2m')
    - `ping_time_bin` str - Ping-time bin size. Must match mask_sparse_bins and compute_mvbs (e.g. '10s')
    - `statistics` list - List of statistics to compute. Supported: ['cv']
    - `data_var` str - Name of the Sv variable in the input Dataset
    - `range_var` str - Meter-valued variable to bin along, which must match the value given to mask_sparse_bins and compute_mvbs or the cell statistics describe cells offset from the MVBS cells they are joined to. 'echo_range' measures from the transducer face, 'depth' from the surface and needs the dataset to carry a depth variable. The cell dimension names stay cell_ping_time and cell_echo_range either way, since add_auxiliary_features selects on them by name.

    - `range_var_max` str (m) - Deepest value the cells cover, as a unit string (e.g. '1810m'), matching the value given to compute_mvbs.
    When unset the extent is read from the data. That forces a compute on a lazy dataset, and it lets each dataset choose its own cell grid length, which is only safe when this step runs once on one merged dataset. Mapped over a survey's per-file Sv it is the bug: each file bins to its own extent and the fan-in outer-joins cell_echo_range and NaN-pads the short ones, the same trap the survey tier guards against on range_sample.
    Pin it whenever the results will be merged.

    - `cells_only` bool - Return only the statistic arrays instead of a copy of ds_Sv carrying them.
    The default keeps the source variables, which is what a single call on a merged dataset wants. Set it true whenever this step is checkpointed or its results are merged: otherwise the fine-resolution Sv is written a second time, and a merge on cell_ping_time drags ping_time and range_sample into the join, which across files with differing range_sample lengths outer-joins and NaN-pads them. That is the same trap the survey tier exists to avoid, reintroduced one dimension over.

    ```python
    # --- Parameters ---
    _compute_cell_stats__range_bin = '4m'
    _compute_cell_stats__ping_time_bin = '4s'
    _compute_cell_stats__statistics = ['cv']

    compute_cell_stats_ds_Sv = compute_per_cell_statistics(
        ds_Sv=remove_noise_ds_Sv,
        range_bin=_compute_cell_stats__range_bin,
        ping_time_bin=_compute_cell_stats__ping_time_bin,
        statistics=_compute_cell_stats__statistics,
    )
    ```
