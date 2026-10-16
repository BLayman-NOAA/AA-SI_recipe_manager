=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-level-1" data-drill="inc:sub_recipes/processing_lvl_1_per_file.yaml" markdown>

    - `read_raw`: [`read_raw_files`](../ops/read_raw_files.md)
    - `combine_raw`: [`combine_raw_files`](../ops/combine_raw_files.md)

    </div>

=== "YAML"

    [Download `processing_lvl_1_per_file.yaml`](../recipes/HB1603/sub_recipes/processing_lvl_1_per_file.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/processing_lvl_1_per_file.yaml

    # Processing level 1: raw ingest.
    #
    #   read_raw -> combine_raw
    #
    # The top of the per-file chain: one instance per file, streamed from NCEI, to
    # a lazily opened EchoData. Nothing here is checkpointed; level 2's Sv is what
    # gets cached.
    #
    # Every recipe that includes this file shares its per-file cache, so editing a
    # step here recomputes the whole survey Sv.
    # Include levels 1 to 3 back to back with the same per_file_source, and put
    # nothing that isn't per-file between them, or the chain splits.

    recipe:
      name: "hb1603_processing_lvl_1_per_file"
      description: "Per-file raw ingest, streamed from NCEI."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      per_file_source:
        type: str
        description: >
          The list the per-file steps map over, normally ${query_ncei.raw_urls}.
          Required, and the same value for every level in the chain.
      sonar_model:
        type: str
        default: "EK60"
        description: "Sonar model string for echopype."

    steps:

      # Converts each raw file to a temporary store. intermediate_format stays a
      # literal because wiring it to an input changes the hash and recomputes the
      # survey Sv. "none" keeps each file in memory instead of on disk.
      - id: read_raw
        op: read_raw_files
        map_over: ${inputs.per_file_source}
        checkpoint: never
        # Deletes each file's store as soon as its instance finishes. Every step a
        # fan-in collects from this chain must then be checkpointed, or it reads
        # back as NaN.
        dispose_outputs: [raw_stores]
        execution:
          dask_config:
            scheduler: processes
        inputs:
          raw_file_paths:
            - ${_item}
        params:
          sonar_model: ${inputs.sonar_model}
          include_bot: false
          intermediate_format: "zarr"

      - id: combine_raw
        op: combine_raw_files
        map_over: ${inputs.per_file_source}
        inputs:
          raw_stores: ${read_raw.raw_stores}
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb). They use variables earlier levels define, so run them from the full notebook.

    !!! note "Shared loop"

        Steps here run inside a per-file loop shared with steps from another level (`extract_cal_params`, `compute_sv`, `compute_transducer_depth`, `ep_add_depth`, `crop_survey_range`, `dive_sv`, `dive_surface_mask`, `dive_frequency_mask`, `dive_seabed_line`, `dive_seabed_mask`, `dive_combine_masks`, `dive_apply_mask`, `dive_remove_noise`, `dive_mask_sparse`, `dive_file_mvbs`, `dive_cell_stats`), so the whole loop is shown.

    #### Step: `read_raw`
    **Op:** `read_raw_files`

    Open raw echosounder files with echopype and write each file as an intermediate per-file store. Returns a list of store-path strings for file-backed formats, or a list of in-memory EchoData objects when intermediate_format is "none". This is the future map_over target for parallel per-file processing. NOTE: use intermediate_format "netcdf" (default) or "none". The "zarr" option is not recommended; see the intermediate_format parameter description below.
    The per-file conversion is echopype.open_raw, and sonar_model, include_bot, and use_swap are forwarded to it. Around that call this step adds remote file staging, .bot companion handling, and writing each result to an intermediate store.

    **Parameters:**
    - `sonar_model` str - Sonar model string recognized by echopype (e.g. 'EK60', 'EK80')
    - `include_bot` bool - Include bottom-detection (.bot) data if available
    - `intermediate_format` str - Intermediate store format written to exe_temp/data between the read_raw and combine_raw steps. Supported values: "netcdf" (default), "zarr", "none".
    "netcdf" (default, recommended): each raw file is written as a temporary .nc file, reopened, and combined. After combining, all EchoData groups are rechunked to a single uniform chunk so the downstream zarr v2 checkpoint write (combine_raw with checkpoint: always) always succeeds.
    "none": all EchoData objects are kept in memory with no temp files. Use when the full dataset fits comfortably in RAM.
    "zarr": each raw file is written as a temporary zarr store (zarr_format=2, matching the checkpoint writer) and reopened lazily for combining. Unlike netcdf, zarr stores can live on remote object storage (e.g. a GCS bucket) by passing a gs:// --temp-dir, which is the intended path for surveys too large to stage on local disk. Combined with gs:// raw_file_paths, local disk usage stays at roughly one raw file for the whole run.

    - `use_swap` str - Passed to echopype open_raw as use_swap. "auto" (default) triggers per-file swap when the expanded data would exceed ~40% of total RAM.


    *Runs once per `map_over` instance (fan-out loop).*

    #### Step: `combine_raw`
    **Op:** `combine_raw_files`

    Lazily open per-file intermediate stores (zarr or netcdf paths) produced by read_raw_files, or combine in-memory EchoData objects (none mode), into a single combined EchoData object. Mark this step checkpoint: always in your recipe so the checkpoint system writes the combined output once and reuses it on subsequent runs.
    The reading and the combine itself are echopype.open_converted and echopype.combine_echodata. Around them this step adds store-path handling, input ordering, and the ping_time chunking of the combined result.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk length along ping_time for the combined EchoData, which directly sets the chunking of the downstream zarr checkpoint. Larger values mean fewer, bigger objects: faster remote (gs://) checkpoint writes and fewer read requests, at the cost of more RAM per computed chunk and coarser lazy-read granularity for windowed access. 1000 is a balanced default; try 2000-4000 for bucket-backed caches if the combined checkpoint write is slow and memory allows.


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
