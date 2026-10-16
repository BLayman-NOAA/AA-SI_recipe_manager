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
    # Include this file directly after level 2 with the same per_file_source. The
    # pooled grid holds each dive's cells once, overlapping dives included, and
    # level 4 clusters it as is. Dive lines are read per dive in level 5.

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
          The list the per-file steps map over, normally ${query_ncei.raw_urls}.
          Required, and the same value for every level in the chain.
      range_bin:
        type: str
        default: "2m"
        description: "Depth bin of the per-dive grid."
      ping_time_bin:
        type: str
        default: "10s"
        description: "Ping-time bin of the per-dive grid."
      range_var:
        type: str
        default: "depth"
        description: "Variable the bins are built on. depth keeps the grid shared across files."
      nan_threshold:
        type: float
        default: .99
        description: "Fraction of a bin that may be NaN before the bin is dropped."
      mvbs_range_var_max:
        type: str
        default: "1810m"
        description: >
          Depth the MVBS grid is built out to, pinned so every file shares one
          depth axis. It must clear the deepest range plus the transducer depth
          (about 9 m), or the deepest bins are silently dropped.
      dive_chunk_ping_time:
        type: int
        default: 1000
        description: "Ping-time chunk of the pooled dive store, on the MVBS grid."

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

      # checkpoint: always is required here and on dive_cell_stats, because the
      # fan-in collects them after read_raw deletes its stores. Without it, every
      # cell comes back NaN with no error.
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

      # Cell CV from the denoised, seabed-masked Sv, before sparse masking.
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

      # A bin split by a file boundary is emitted by both files; "mean" merges it.
      - id: merge_dive_files_mvbs
        op: merge_datasets
        collect: ${dive_file_mvbs.ds_MVBS}
        inputs:
          datasets: ${dive_file_mvbs.ds_MVBS}
        params:
          dim: "ping_time"
          on_duplicate: "mean"

      # The pooled dive grid that level 4 clusters.
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

=== "Notebook"

    These are the cells that run this level, taken from the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb). They use variables earlier levels define, so run them from the full notebook.

    !!! note "Shared loop"

        Steps here run inside a per-file loop shared with steps from another level (`read_raw`, `combine_raw`, `extract_cal_params`, `compute_sv`, `compute_transducer_depth`, `ep_add_depth`, `crop_survey_range`, `dive_sv`, `dive_surface_mask`, `dive_frequency_mask`, `dive_seabed_line`, `dive_seabed_mask`, `dive_combine_masks`, `dive_apply_mask`, `dive_remove_noise`), so the whole loop is shown.

    #### Step: `dive_mask_sparse`
    **Op:** `mask_sparse_bins`

    Remove spatiotemporal bins with fewer valid samples than a threshold, reducing noise in MVBS computation.

    **Parameters:**
    - `range_bin` str - Range bin size (e.g. '2m')
    - `ping_time_bin` str - Ping-time bin size (e.g. '10s')
    - `nan_threshold` float - Fraction of NaN values above which a bin is masked (0.0 to 1.0)
    - `range_var` str - Meter-valued variable to bin along, which must match the value given to compute_mvbs and compute_per_cell_statistics or the sparse mask lands on a different grid than the one it is meant to clean. 'echo_range' measures from the transducer face, 'depth' from the surface and needs the dataset to carry a depth variable. The bin edges are read from the first ping of the first channel, so a range variable that varies from ping to ping, as depth does under platform vertical offsets, is approximated by its first ping.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_file_mvbs`
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


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `dive_cell_stats`
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


    *Runs once per `map_over` instance (fan-out loop).*

    ```python
    raw_stores = []
    echodata = []
    cal_params = []
    env_params = []
    other_params = []
    compute_sv_ds_Sv = []
    transducer_depth = []
    ep_add_depth_ds_Sv = []
    crop_survey_range_ds_Sv = []
    dive_sv_ds_Sv = []
    dive_surface_mask_mask = []
    dive_frequency_mask_mask = []
    seafloor_depth = []
    dive_seabed_mask_mask = []
    dive_combine_masks_mask = []
    dive_apply_mask_ds_Sv = []
    dive_remove_noise_ds_Sv = []
    dive_mask_sparse_ds_Sv = []
    ds_MVBS = []
    dive_cell_stats_ds_Sv = []
    for _item in raw_urls:
        _iter_raw_stores = read_raw_files_to_stores(
        raw_file_paths=[_item],
        sonar_model='EK60',
        include_bot=False,
        intermediate_format='zarr',
    )
        raw_stores.append(_iter_raw_stores)
        _iter_echodata = combine_raw_stores(raw_stores=_iter_raw_stores)
        echodata.append(_iter_echodata)
        _result = extract_standardized_calibration_parameters(
        calibration_dict=calibration_dict,
        mapping_dict=mapping_dict,
        echodata=_iter_echodata,
        raw_file_path=_item,
    )
        _iter_cal_params = _result['cal_params']
        _iter_env_params = _result['env_params']
        _iter_other_params = _result['other_params']
        cal_params.append(_iter_cal_params)
        env_params.append(_iter_env_params)
        other_params.append(_iter_other_params)
        _iter_compute_sv_ds_Sv = compute_Sv(
        echodata=_iter_echodata,
        cal_params=_iter_cal_params,
        env_params=_iter_env_params,
    )
        compute_sv_ds_Sv.append(_iter_compute_sv_ds_Sv)
        _iter_transducer_depth = compute_transducer_depth(
        ds_Sv=_iter_compute_sv_ds_Sv,
        echodata=_iter_echodata,
        use_heave=True,
        heave_sign=1.0,
    )
        transducer_depth.append(_iter_transducer_depth)
        _iter_ep_add_depth_ds_Sv = add_depth(
        ds=_iter_compute_sv_ds_Sv,
        echodata=_iter_echodata,
        depth_offset=_iter_transducer_depth,
    )
        ep_add_depth_ds_Sv.append(_iter_ep_add_depth_ds_Sv)
        _iter_crop_survey_range_ds_Sv = crop_range_samples(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        max_range_m=1800.0,
        drop_trailing_all_nan=False,
        outlier_sigma=None,
    )
        crop_survey_range_ds_Sv.append(_iter_crop_survey_range_ds_Sv)
        _iter_dive_sv_ds_Sv = select_ping_time_range(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        windows=windows,
        allow_empty=True,
        snap_to_bin='10s',
    )
        dive_sv_ds_Sv.append(_iter_dive_sv_ds_Sv)
        _iter_dive_surface_mask_mask = create_surface_mask(ds_Sv=_iter_dive_sv_ds_Sv, surface_depth_m=10.0)
        dive_surface_mask_mask.append(_iter_dive_surface_mask_mask)
        _iter_dive_frequency_mask_mask = create_frequency_mask(ds_Sv=_iter_dive_sv_ds_Sv, frequencies_to_mask=[70, 120, 200])
        dive_frequency_mask_mask.append(_iter_dive_frequency_mask_mask)
        _iter_seafloor_depth = read_seafloor_line_evl(
        ds_Sv=_iter_dive_sv_ds_Sv,
        evl_path='./Auxiliary/seabed_lines',
        min_coverage=0.99,
    )
        seafloor_depth.append(_iter_seafloor_depth)
        _iter_dive_seabed_mask_mask = create_seafloor_mask(
        ds_Sv=_iter_dive_sv_ds_Sv,
        seafloor_depth=_iter_seafloor_depth,
        seafloor_buffer_m=10.0,
    )
        dive_seabed_mask_mask.append(_iter_dive_seabed_mask_mask)
        _iter_dive_combine_masks_mask = combine_masks(masks=[_iter_dive_surface_mask_mask, _iter_dive_frequency_mask_mask, _iter_dive_seabed_mask_mask], mode='and')
        dive_combine_masks_mask.append(_iter_dive_combine_masks_mask)
        _iter_dive_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_dive_sv_ds_Sv, mask=_iter_dive_combine_masks_mask)
        dive_apply_mask_ds_Sv.append(_iter_dive_apply_mask_ds_Sv)
        _iter_dive_remove_noise_ds_Sv = remove_noise(
        ds_Sv=_iter_dive_apply_mask_ds_Sv,
        range_sample_num=10,
        ping_num=5,
        SNR_threshold='6.0dB',
        background_noise_max='-66dB',
    )
        dive_remove_noise_ds_Sv.append(_iter_dive_remove_noise_ds_Sv)
        _iter_dive_mask_sparse_ds_Sv = mask_sparse_bins(
        ds_Sv=_iter_dive_remove_noise_ds_Sv,
        range_bin='2m',
        ping_time_bin='10s',
        range_var='depth',
        nan_threshold=0.99,
    )
        dive_mask_sparse_ds_Sv.append(_iter_dive_mask_sparse_ds_Sv)
        _iter_ds_MVBS = compute_MVBS(
        ds_Sv=_iter_dive_mask_sparse_ds_Sv,
        range_bin='2m',
        ping_time_bin='10s',
        range_var='depth',
        range_var_max='3020m',
    )
        ds_MVBS.append(_iter_ds_MVBS)
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
    ```

    #### Step: `merge_dive_files_mvbs`
    **Op:** `merge_datasets`

    Reconsolidate a collected list of xarray Datasets into a single Dataset by concatenating along a dimension. This is the recipe system's fan-in (collect) target for segment-parallel work: a mapped step that produces one Dataset per data segment (e.g. per-file MVBS) is gathered via ``collect`` and merged back into one Dataset along ``dim`` (default ``ping_time``). A single Dataset passes through unchanged (single-item transparency).

    **Parameters:**
    - `dim` str - Dimension to concatenate the collected Datasets along.
    - `on_duplicate` str - What to do when two collected segments carry the same label on ``dim``.
    Unset (the default) leaves both, which is right when segments cannot overlap - per-file Sv concatenated on raw ping_time, for instance.
    "mean" averages them into one entry. That is what a fan-in over per segment BINNED products needs: compute_mvbs and compute_per_cell_statistics anchor their time bins to a global origin, so a segment boundary falling inside a bin makes the segment on each side emit that bin, each holding part of its pings. Left alone the merged store carries a duplicate index, which every downstream reindex rejects - add_auxiliary_features raises InvalidIndexError on it.
    "first" keeps the first entry for a label and sorts the result. That is what a fan-in over overlapping SLICES OF ONE STORE needs: dive windows cut from the survey grid, where two dives sharing a minute are the same cells twice, so nothing should be averaged. A per-segment variable that differs on the overlap is kept from the first segment only, which is the sign it does not belong on the shared axis; attach it per segment after the fan-in.
    Variables carrying "units: dB" are averaged in the linear domain and converted back, since a mean of decibels is not the mean of what they measure. The average is unweighted: the partial bins do not record how many pings each held.

    ```python
    # --- Parameters ---
    _merge_dive_files_mvbs__dim = 'ping_time'
    _merge_dive_files_mvbs__on_duplicate = 'mean'

    merge_dive_files_mvbs_merged = concat_datasets(
        datasets=ds_MVBS,
        dim=_merge_dive_files_mvbs__dim,
        on_duplicate=_merge_dive_files_mvbs__on_duplicate,
    )
    ```

    #### Step: `rechunk_dive_mvbs`
    **Op:** `rechunk_dataset`

    Rechunk a Dataset along ping_time. A standalone step rather than a parameter on any particular masking or reduction op, so a recipe can insert (or remove) chunking wherever a downstream op should run dask-parallel, independent of which step happens to run immediately before it.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk size along ping_time.

    ```python
    # --- Parameters ---
    _rechunk_dive_mvbs__ping_time_chunk = 1000

    rechunk_dive_mvbs_ds_Sv = rechunk_dataset(ds_Sv=merge_dive_files_mvbs_merged, ping_time_chunk=_rechunk_dive_mvbs__ping_time_chunk)
    ```

    #### Step: `merge_dive_files_cell_stats`
    **Op:** `merge_datasets`

    Reconsolidate a collected list of xarray Datasets into a single Dataset by concatenating along a dimension. This is the recipe system's fan-in (collect) target for segment-parallel work: a mapped step that produces one Dataset per data segment (e.g. per-file MVBS) is gathered via ``collect`` and merged back into one Dataset along ``dim`` (default ``ping_time``). A single Dataset passes through unchanged (single-item transparency).

    **Parameters:**
    - `dim` str - Dimension to concatenate the collected Datasets along.
    - `on_duplicate` str - What to do when two collected segments carry the same label on ``dim``.
    Unset (the default) leaves both, which is right when segments cannot overlap - per-file Sv concatenated on raw ping_time, for instance.
    "mean" averages them into one entry. That is what a fan-in over per segment BINNED products needs: compute_mvbs and compute_per_cell_statistics anchor their time bins to a global origin, so a segment boundary falling inside a bin makes the segment on each side emit that bin, each holding part of its pings. Left alone the merged store carries a duplicate index, which every downstream reindex rejects - add_auxiliary_features raises InvalidIndexError on it.
    "first" keeps the first entry for a label and sorts the result. That is what a fan-in over overlapping SLICES OF ONE STORE needs: dive windows cut from the survey grid, where two dives sharing a minute are the same cells twice, so nothing should be averaged. A per-segment variable that differs on the overlap is kept from the first segment only, which is the sign it does not belong on the shared axis; attach it per segment after the fan-in.
    Variables carrying "units: dB" are averaged in the linear domain and converted back, since a mean of decibels is not the mean of what they measure. The average is unweighted: the partial bins do not record how many pings each held.

    ```python
    # --- Parameters ---
    _merge_dive_files_cell_stats__dim = 'cell_ping_time'
    _merge_dive_files_cell_stats__on_duplicate = 'mean'

    merge_dive_files_cell_stats_merged = concat_datasets(
        datasets=dive_cell_stats_ds_Sv,
        dim=_merge_dive_files_cell_stats__dim,
        on_duplicate=_merge_dive_files_cell_stats__on_duplicate,
    )
    ```
