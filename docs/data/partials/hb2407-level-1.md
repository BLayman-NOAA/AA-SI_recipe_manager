=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-level-1" data-drill="inc:sub_recipes/processing_lvl_1_per_file.yaml" markdown>

    - `read_raw`: [`read_raw_files`](../ops/read_raw_files.md)
    - `combine_raw`: [`combine_raw_files`](../ops/combine_raw_files.md)

    </div>

=== "YAML"

    [Download `processing_lvl_1_per_file.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_1_per_file.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/
    # sub_recipes/processing_lvl_1_per_file.yaml. Its first two steps moved to level
    # 0 and extract_cal_params to level 2, to match the HB1603 levels.

    # Processing level 1 (per file): raw ingest.
    #
    #   read_raw -> combine_raw
    #
    # The per-file fan-out starts here. Each raw file runs every step through
    # apply_mask in level 2 as its own cached instance, so adding a file later runs
    # only that file. Include processing_lvl_2_per_file.yaml immediately after this.

    recipe:
      name: "processing_lvl_1_per_file"
      description: "Per-file raw ingest (gs:// inputs)."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      sonar_model:
        type: str
        default: "EK80"
      ping_time_chunk:
        type: int
        default: 10000
        description: >
          Ping-time chunk length of each file's EchoData and checkpoint. The default
          keeps each file in one chunk; lower it if a file does not fit in memory.

    steps:

      # Never checkpointed: its output is a temporary store path deleted at the end
      # of the run.
      - id: read_raw
        op: read_raw_files
        map_over: ${initial_setup.raw_file_paths}
        checkpoint: never
        execution:
          dask_config:
            scheduler: processes
        inputs:
          raw_file_paths:
            - ${_item}          # a single-element list: just this file
        params:
          sonar_model: ${inputs.sonar_model}
          include_bot: false
          intermediate_format: "zarr"

      # Opens this file's store as an EchoData object. One file only, so not a fan-in.
      - id: combine_raw
        op: combine_raw_files
        map_over: ${initial_setup.raw_file_paths}
        inputs:
          raw_stores: ${read_raw.raw_stores}
        params:
          ping_time_chunk: ${inputs.ping_time_chunk}
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb). They use variables earlier levels define, so run them from the full notebook.

    !!! note "Shared loop"

        Steps here run inside a per-file loop shared with steps from another level (`extract_cal_params`, `compute_sv`, `compute_transducer_depth`, `ep_add_depth`, `detect_seafloor`, `create_seafloor_mask`, `create_surface_mask`, `combine_masks`, `apply_mask`), so the whole loop is shown.

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
    seafloor_depth = []
    create_seafloor_mask_mask = []
    create_surface_mask_mask = []
    combine_masks_mask = []
    apply_mask_ds_Sv = []
    for _item in raw_file_paths:
        _iter_raw_stores = read_raw_files_to_stores(
        raw_file_paths=[_item],
        sonar_model='EK80',
        include_bot=False,
        intermediate_format='zarr',
    )
        raw_stores.append(_iter_raw_stores)
        _iter_echodata = combine_raw_stores(raw_stores=_iter_raw_stores, ping_time_chunk=10000)
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
        waveform_mode='CW',
        encode_mode='power',
    )
        compute_sv_ds_Sv.append(_iter_compute_sv_ds_Sv)
        _iter_transducer_depth = compute_transducer_depth(
        ds_Sv=_iter_compute_sv_ds_Sv,
        echodata=_iter_echodata,
        datum_correction_m=-1.0,
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
        _iter_seafloor_depth = read_seafloor_line_evl(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        evl_path='gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines',
        file_time_start='2024-10-15T13:38:52',
        file_time_end='2024-10-15T23:43:36',
        edge_extend_s=0.0,
        max_gap_s=600,
        min_coverage=0.99,
    )
        seafloor_depth.append(_iter_seafloor_depth)
        _iter_create_seafloor_mask_mask = create_seafloor_mask(
        ds_Sv=_iter_ep_add_depth_ds_Sv,
        seafloor_depth=_iter_seafloor_depth,
        seafloor_buffer_m=1,
    )
        create_seafloor_mask_mask.append(_iter_create_seafloor_mask_mask)
        _iter_create_surface_mask_mask = create_surface_mask(ds_Sv=_iter_compute_sv_ds_Sv, surface_depth_m=5.0)
        create_surface_mask_mask.append(_iter_create_surface_mask_mask)
        _iter_combine_masks_mask = combine_masks(masks=[_iter_create_seafloor_mask_mask, _iter_create_surface_mask_mask], mode='and')
        combine_masks_mask.append(_iter_combine_masks_mask)
        _iter_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_ep_add_depth_ds_Sv, mask=_iter_combine_masks_mask)
        apply_mask_ds_Sv.append(_iter_apply_mask_ds_Sv)
    ```
