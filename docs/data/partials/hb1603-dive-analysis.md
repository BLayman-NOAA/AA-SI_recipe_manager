=== "Levels"

    <div class="recipe-flow" data-recipe="hb1603-dive-analysis-overview" markdown>

    - [HB1603 dive plan](hb1603-dive-analysis.md)
    - [HB1603 level 0, NCEI catalogue and calibration mapping](level-0.md)
    - [HB1603 level 1, raw ingest](level-1.md)
    - [HB1603 level 2, calibrated Sv, masks and noise removal](level-2.md)
    - [HB1603 level 3, gridding and per-cell statistics](level-3.md)
    - [HB1603 level 4, clustering](level-4.md)
    - [HB1603 level 5, dive SVCodes](level-5.md)

    </div>

=== "All steps"

    <div class="recipe-flow" data-recipe="hb1603-dive-analysis" markdown>

    - `plan_dives`: custom `aa_si_utils.dive_profiles.plan_dive_datasets`
    - `query_ncei`: [`query_ncei_data`](../ops/query_ncei_data.md)
    - `scan_raw_config`: [`read_raw_file_config`](../ops/read_raw_file_config.md)
    - `record_raw_configs`: [`record_raw_file_configs`](../ops/record_raw_file_configs.md)
    - `standardize_cal`: [`standardize_calibration_files`](../ops/standardize_calibration_files.md)
    - `build_cal_mapping`: [`build_calibration_mapping`](../ops/build_calibration_mapping.md)
    - `read_raw`: [`read_raw_files`](../ops/read_raw_files.md)
    - `combine_raw`: [`combine_raw_files`](../ops/combine_raw_files.md)
    - `extract_cal_params`: [`extract_standardized_cal_params`](../ops/extract_standardized_cal_params.md)
    - `compute_sv`: [`compute_sv`](../ops/compute_sv.md)
    - `compute_transducer_depth`: [`compute_transducer_depth`](../ops/compute_transducer_depth.md)
    - `ep_add_depth`: [`ep_add_depth`](../ops/ep_add_depth.md)
    - `crop_survey_range`: [`crop_range`](../ops/crop_range.md)
    - `dive_sv`: [`select_ping_time_range`](../ops/select_ping_time_range.md)
    - `dive_surface_mask`: [`create_surface_mask`](../ops/create_surface_mask.md)
    - `dive_frequency_mask`: [`create_frequency_mask`](../ops/create_frequency_mask.md)
    - `dive_seabed_line`: [`read_seafloor_line`](../ops/read_seafloor_line.md)
    - `dive_seabed_mask`: [`create_seafloor_mask`](../ops/create_seafloor_mask.md)
    - `dive_combine_masks`: [`combine_masks`](../ops/combine_masks.md)
    - `dive_apply_mask`: [`apply_sv_mask`](../ops/apply_sv_mask.md)
    - `dive_remove_noise`: [`remove_background_noise`](../ops/remove_background_noise.md)
    - `dive_mask_sparse`: [`mask_sparse_bins`](../ops/mask_sparse_bins.md)
    - `dive_file_mvbs`: [`compute_mvbs`](../ops/compute_mvbs.md)
    - `dive_cell_stats`: [`compute_per_cell_statistics`](../ops/compute_per_cell_statistics.md)
    - `merge_dive_files_mvbs`: [`merge_datasets`](../ops/merge_datasets.md)
    - `rechunk_dive_mvbs`: [`rechunk_dataset`](../ops/rechunk_dataset.md)
    - `merge_dive_files_cell_stats`: [`merge_datasets`](../ops/merge_datasets.md)
    - `reshape_ml`: [`reshape_for_ml`](../ops/reshape_for_ml.md)
    - `add_aux_features`: [`add_auxiliary_features`](../ops/add_auxiliary_features.md)
    - `normalize_ml`: [`normalize_ml_data`](../ops/normalize_ml_data.md)
    - `run_hdbscan`: [`run_hdbscan`](../ops/run_hdbscan.md)
    - `label_all_points`: [`assign_clusters_to_all_points`](../ops/assign_clusters_to_all_points.md)
    - `embed_results`: [`embed_clustering_results`](../ops/embed_clustering_results.md)
    - `plot_clustering_report`: [`plot_clustering_report`](../ops/plot_clustering_report.md)
    - `generate_sv_codes`: custom `aa_si_utils.dive_profiles.generate_sv_codes`
    - `sv_code_depth_table`: custom `aa_si_utils.dive_profiles.sv_code_depth_table`

    </div>

=== "YAML"

    [Download `dive_analysis.yaml`](../recipes/HB1603/dive_analysis.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/dive_analysis.yaml,
    # with its two level 2 includes (2a survey, 2b user) merged into processing_lvl_2_combined.yaml.
    # Unlike the original, level 2b keeps the survey's surface-referenced depth rather than re-adding
    # depth as range from the transducer, so the level 3 depth axis is pinned 10 m deeper.

    # HB1603 dive analysis: 13 dives, one clustering, per-dive SVCodes.
    #
    #   aa-recipe run dive_analysis.yaml --executor dask --dask-workers 16
    #
    #   plan        read the dive configs, one window per dive
    #   level 0     the survey's NCEI catalogue and calibration mapping
    #   level 1     per file: raw ingest, streamed from NCEI
    #   level 2     per file: calibrated Sv and depth (2a), then inside the dive
    #               windows the surface, frequency and seabed masks and noise
    #               removal (2b)
    #   level 3     per file: sparse mask, grid, per-cell CV; fan in to the pooled
    #               dive grid
    #   level 4     pooled HDBSCAN clustering and the clustering report
    #   level 5     per-dive SVCodes and the depth-interval tables
    #
    # WHERE THE VALUES LIVE
    #
    #   Each level file documents its own steps and inputs. Every value is set in
    #   this file, on the include that uses it, so the whole analysis reads from
    #   one place. A value more than one include needs is a recipe input below;
    #   the rest are literals in input_overrides. A literal is not reachable with
    #   --input: edit it here.
    #
    # THE SURVEY TIER
    #
    #   Levels 0 to 2a are the survey's: computed once per cruise by a separate
    #   survey recipe and loaded from its shared cache here. A step's hash folds
    #   in how its inputs are wired, and those hashes address that cache, so their
    #   overrides must stay exactly as the survey recipe has them. Do not narrow
    #   the catalogue query to get a subset: it is a parent of every per-file
    #   step. The window belongs in level 2b.
    #
    # ORDER MATTERS BETWEEN LEVELS 1 AND 3
    #
    #   They are one per-file chain. Nothing non-mapped may be declared between
    #   those includes, which is why the dive plan comes first.
    #
    # Sanity-check the shape before a long run:
    #   aa-recipe dry-run dive_analysis.yaml --visualize

    recipe:
      name: "hb1603_dive_analysis"
      description: "Pooled HDBSCAN clustering of 13 HB1603 sperm whale dives with per-dive SVCodes, seabed masked on Sv."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:

      # --- Survey level -----------------------------------------------------------
      cal_input_folder:
        type: path
        default: "../calibration_files/HB201607_cal"
        description: "Manufacturer .cal files."
      calibration_output_dir:
        type: path
        default: "./outputs/calibration"
      crop_max_range_m:
        type: float
        default: 1800.0
        description: >
          Range ceiling applied per file. Every consumer shares it: change it and
          the whole survey Sv is recomputed.

      # --- Shared by levels 2b, 3 and 5 -------------------------------------------
      ping_time_bin:
        type: str
        default: "10s"
        description: >
          Ping-time bin of the per-dive grid. Level 2b widens each dive window to
          whole bins of this width, level 3 grids on it, and level 5 counts one bin
          of this length per ping in the duration tables.

    steps:

      - include: sub_recipes/dive_plan.yaml
        input_overrides:
          json_dir: "./Auxiliary/JSON_Files"
          evl_root: "./Auxiliary"
          pad_minutes: 0.0
          dive_labels:
            - "SWD_20160707-OE20"
            - "SWD_20160707-OE29"
            - "SWD_20160707-OE32"
            - "SWD_20160719-OE07"
            - "SWD_20160725-OE50"
            - "SWD_20160725-OE54"
            - "SWD_20160725-OE61"
            - "SWD_20160725-OE62"
            - "SWD_20160725-OE64"
            - "SWD_20160802-OE16"
            - "SWD_20160814-OE03"
            - "SWD_20160814-OE09"
            - "SWD_20160814-OE44"

      - include: sub_recipes/processing_lvl_0_user.yaml
        input_overrides:
          cal_input_folder: ${inputs.cal_input_folder}
          calibration_output_dir: ${inputs.calibration_output_dir}

      - include: sub_recipes/processing_lvl_1_per_file.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}

      - include: sub_recipes/processing_lvl_2_combined.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}
          calibration_dict: ${build_cal_mapping.calibration_dict}
          mapping_dict: ${build_cal_mapping.mapping_dict}
          crop_max_range_m: ${inputs.crop_max_range_m}
          dive_windows: ${plan_dives.windows}
          ping_time_bin: ${inputs.ping_time_bin}
          surface_depth_m: 10.0
          frequencies_to_mask: [70, 120, 200]
          seabed_lines_dir: "./Auxiliary/seabed_lines"
          seabed_min_coverage: 0.99
          seafloor_buffer_m: 10.0
          noise_range_sample_num: 10
          noise_ping_num: 5
          noise_snr_threshold: "6.0dB"
          noise_background_max: "-66dB"

      - include: sub_recipes/processing_lvl_3_user.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}
          ping_time_bin: ${inputs.ping_time_bin}
          range_bin: "2m"
          range_var: "depth"
          nan_threshold: 0.99
          # Pinned so every file shares one depth axis. HB1603 recorded to about
          # 3003 m of range, and depth adds the transducer's own depth below the
          # surface (about 9 m, plus heave), so 3020m clears every file.
          mvbs_range_var_max: "3020m"
          dive_chunk_ping_time: 1000

      - include: sub_recipes/processing_lvl_4.yaml
        input_overrides:
          mvbs: ${rechunk_dive_mvbs.ds_Sv}
          cell_stats_sv: ${merge_dive_files_cell_stats.merged}
          dive_windows: ${plan_dives.windows}
          sample_size: 1200000
          min_cluster_size_fraction: 0.01
          min_samples: 4

      - include: sub_recipes/processing_lvl_5_sv_codes.yaml
        input_overrides:
          clustered: ${embed_results.ds_normalized}
          dive_windows: ${plan_dives.windows}
          ping_time_bin: ${inputs.ping_time_bin}
          code_output_dir: "./outputs/sv_codes"
          depth_interval_m: 200.0
    ```

=== "Notebook"

    [Download the notebook](../data/notebooks/hb1603-dive-analysis.ipynb), as `aa-recipe generate` writes it.

    ### hb1603_dive_analysis

    Pooled HDBSCAN clustering of 13 HB1603 sperm whale dives with per-dive SVCodes, seabed masked on Sv.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Utils.git
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_calibration.git
    # %pip install echopype>=0.6.0
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_ML.git
    ```

    ```python
    from aa_si_utils.dive_profiles import plan_dive_datasets
    from aa_si_utils.data_retrieval import query_ncei_data
    from aa_si_calibration.calibration import read_raw_file_config
    from aa_si_calibration.calibration import record_raw_file_configs
    from aa_si_calibration.calibration import standardize_calibration_files
    from aa_si_calibration.calibration import build_calibration_mapping
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores
    from aa_si_calibration.calibration import extract_standardized_calibration_parameters
    from echopype.calibrate import compute_Sv
    from aa_si_utils.utils import compute_transducer_depth
    from echopype.consolidate import add_depth
    from aa_si_utils.utils import crop_range_samples
    from aa_si_utils.utils import select_ping_time_range
    from aa_si_utils.utils import create_surface_mask
    from aa_si_utils.utils import create_frequency_mask
    from aa_si_utils.utils import read_seafloor_line_evl
    from aa_si_utils.utils import create_seafloor_mask
    from aa_si_utils.utils import combine_masks
    from aa_si_utils.utils import apply_mask_to_sv
    from aa_si_ml.ml import remove_noise
    from aa_si_utils.utils import mask_sparse_bins
    from echopype.commongrid import compute_MVBS
    from aa_si_ml.ml import compute_per_cell_statistics
    from aa_si_utils.utils import concat_datasets
    from aa_si_utils.utils import rechunk_dataset
    from aa_si_ml.ml import reshape_data_for_ml
    from aa_si_ml.ml import add_auxiliary_features
    from aa_si_ml.ml import normalize_data
    from aa_si_ml.ml import run_hdbscan
    from aa_si_ml.ml import assign_clusters_by_prediction
    from aa_si_ml.ml import embed_clustering_results
    from aa_si_ml.ml import plot_clustering_report
    from aa_si_utils.dive_profiles import generate_sv_codes
    from aa_si_utils.dive_profiles import sv_code_depth_table
    ```

    ```python
    cal_input_folder = '../calibration_files/HB201607_cal'  # path
    calibration_output_dir = './outputs/calibration'  # path
    crop_max_range_m = 1800.0  # float
    ping_time_bin = '10s'  # str
    cruise_name = 'HB1603'  # str
    file_time_start = '2016-06-01T00:00:00'  # str
    file_time_end = '2016-09-01T00:00:00'  # str
    cruise_id = 'HB1603'  # str
    record_author = '<Your Name>'  # str
    max_scan_bytes = 1048576  # int
    match_tolerances = {'transmit_power': 1001.0}  # dict
    sonar_model = 'EK60'  # str
    ```

    #### Included: dive_plan.yaml

    Steps: plan_dives

    ##### Step: `plan_dives`
    **Op:** `custom` (custom / unregistered)

    Turn the NEFSC dive configs into recipe windows.

    **Parameters:**
    - `json_dir` path
    - `evl_root` path
    - `pad_minutes` float
    - `include_labels` list

    ```python
    # --- Parameters ---
    _plan_dives__json_dir = './Auxiliary/JSON_Files'
    _plan_dives__evl_root = './Auxiliary'
    _plan_dives__pad_minutes = 0.0
    _plan_dives__include_labels = ['SWD_20160707-OE20', 'SWD_20160707-OE29', 'SWD_20160707-OE32', 'SWD_20160719-OE07', 'SWD_20160725-OE50', 'SWD_20160725-OE54', 'SWD_20160725-OE61', 'SWD_20160725-OE62', 'SWD_20160725-OE64', 'SWD_20160802-OE16', 'SWD_20160814-OE03', 'SWD_20160814-OE09', 'SWD_20160814-OE44']

    _result = plan_dive_datasets(
        json_dir=_plan_dives__json_dir,
        evl_root=_plan_dives__evl_root,
        pad_minutes=_plan_dives__pad_minutes,
        include_labels=_plan_dives__include_labels,
    )
    windows = _result['windows']
    raw_files = _result['raw_files']
    ```

    *End of included section: dive_plan.yaml*

    #### Included: processing_lvl_0_user.yaml

    Steps: query_ncei, scan_raw_config, record_raw_configs, standardize_cal, build_cal_mapping

    ##### Step: `query_ncei`
    **Op:** `query_ncei_data`

    Query an NCEI acoustic data catalog for file listings within a time window. Returns a list of result records (file metadata), not the files themselves.

    **Parameters:**
    - `file_time_start` str - ISO 8601 start of window, e.g. '2016-07-25T20:58'
    - `file_time_end` str - ISO 8601 end of window
    - `filters` dict - Key-value filters applied to NCEI metadata (e.g. CRUISE_NAME)
    - `file_names` list - Keep only these files, matched on the D{date}-T{time} stamp in the name, so a bare D20160725-T205832.raw finds the archive's HB1603_L1- form and the reverse. Applied after the time window, client side, in catalogue order. A name the query did not return raises rather than silently shrinking the result. Wire it from a planning step, for example ${plan_dives.raw_files}, to map a per-file chain over an analysis's files alone. Empty keeps everything.

    ```python
    # --- Parameters ---
    _query_ncei__file_time_start = '2016-06-01T00:00:00'
    _query_ncei__file_time_end = '2016-09-01T00:00:00'

    _result = query_ncei_data(
        file_time_start=_query_ncei__file_time_start,
        file_time_end=_query_ncei__file_time_end,
        filters={'CRUISE_NAME': 'HB1603'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']
    ```

    ##### Step: `scan_raw_config`
    **Op:** `read_raw_file_config`

    Read ONE raw file's channel configuration from its datagram headers.
    The fan-out unit of the calibration raw scan. Map it over initial_setup.raw_file_paths with checkpoint: always and each file becomes its own cache entry, so a run interrupted at file 400 of 500 keeps the 399 already scanned. Pair it with record_raw_file_configs, which collects the results into the survey's raw_file_configs.yaml.

    **Parameters:**
    - `raw_file_path` path - One .raw file. May be a local path or a remote fsspec URL, which is downloaded to local scratch, read, and deleted before this step returns. Normally wired to ${_item}. Not fingerprinted, because the mapped item already content-addresses the instance.

    - `verify_start_time` bool - If true, EK80 files are additionally read with the full SimradFileReader to verify metadata_start_time, which is slower. The default single pass scan is enough for channel configuration.

    - `verbose` bool - Print per-file progress. Worth setting false on a long fan-out, where one banner per file buries the rest of the log.

    - `max_scan_bytes` int - Ceiling on how many leading bytes of a remote EK60 or EK80 file may be read in place of transferring it whole. The scan starts at min(8 MiB, this) and doubles up to this cap, stopping as soon as the configuration settles every channel, so a file that settles early pays only the small read and the local prefix grows in place rather than being re-read from zero.
    Size it for the deepest configuration in the survey. Too low is expensive, not wrong: a file that never settles is read whole.
    EK80: CW files settle within a fraction of a MiB; FM files sit behind much larger RAW3 payloads and scale with file size, so a survey of ~1 GiB FM files needs a cap in the tens of MiB.
    EK60 settles far earlier, and the cap doubles as the starting read, so setting it to 1 MiB turns a cruise-wide scan into one small read per file. Measured on HB1603 (52 MB files, five channels), the configuration settles at 256 KiB. Do not be tempted lower on the strength of the channel list alone: at 64 KiB every channel is already listed and looks complete, but three of the five carry CON0's configured 2000 W rather than the 500, 300 and 750 W actually transmitted. The step guards against that by requiring one RAW0 datagram per channel before it trusts a prefix, so a too-small cap costs a whole-file read rather than wrong calibration.
    Because a prefix cannot measure them, the per-file datagram counts and gps_data come back null; last_ping_time is recovered from the file's tail. Leave unset for the whole-file read, which is also what local files and verify_start_time always get.


    *Runs once per `map_over` instance (fan-out loop).*

    ```python
    file_config = []
    for _item in raw_urls:
        _iter_file_config = read_raw_file_config(
        raw_file_path=_item,
        verbose=False,
        max_scan_bytes=1048576,
    )
        file_config.append(_iter_file_config)
    ```

    ##### Step: `record_raw_configs`
    **Op:** `record_raw_file_configs`

    Collect per-file channel configurations into the survey's raw config file.
    The fan-in half of the raw scan. Drops the files that could not be read, sorts the rest by metadata_start_time, and writes raw_file_configs/raw_file_configs.yaml under the calibration output directory. Declare it with collect on the scan step's file_config output.

    **Parameters:**
    - `output_base` path - Root directory the calibration artifacts are written under. Normally wired to initial_setup's calibration_output_dir.

    - `verbose` bool - Print a summary of what was recorded.

    ```python
    # --- Parameters ---
    _record_raw_configs__output_base = './outputs/calibration'

    _result = record_raw_file_configs(file_configs=file_config, output_base=_record_raw_configs__output_base)
    raw_file_configs = _result['raw_file_configs']
    frequencies = _result['frequencies']
    raw_configs_path = _result['raw_configs_path']
    ```

    ##### Step: `standardize_cal`
    **Op:** `standardize_calibration_files`

    Convert manufacturer calibration files to standardized single-channel files.
    Parses the EK60 .cal or EK80 .xml files in the calibration input folder, validates them against the standardized schema, and writes one .yaml per channel into single_channel_calibration_files/ under the calibration output directory. These per-channel files are what NCEI archives.
    The parse is skipped when standardization.fingerprint.json shows the same manufacturer files already produced the files that are there. That sidecar answers to the manufacturer folder, so deleting a single-channel file does not bring it back, which is what the mapping step's "error" conflict workflow relies on.

    **Parameters:**
    - `cal_input_folder` path - Folder of manufacturer calibration files (.cal for EK60, .xml for EK80). May be a remote fsspec URL, which is downloaded whole to local scratch for the duration of the parse.

    - `output_base` path - Root directory the calibration artifacts are written under. Normally wired to initial_setup's calibration_output_dir.

    - `cruise_id` str - Cruise identifier recorded in every generated single-channel file (e.g. "HB2407"), so a standardized file stays traceable to the survey it came from.

    - `record_author` str - Name recorded as the author of every generated single-channel file. Part of the provenance the standardized format carries, not something the calculation uses.

    - `short_filenames` bool - Use compact names for the generated single-channel files. Set false for longer, more descriptive names. The names become the calibration keys the mapping is built from, so this participates in the reuse fingerprint.

    - `overwrite` bool - Re-parse and rewrite even when the sidecar says the existing files are current, forcing a clean rebuild of the single-channel folder.

    - `verbose` bool - Print the parsed parameters and the files written.
    - `override_channels` dict - Write these channels as the single-channel files instead of parsing the manufacturer files. Same shape as the single_channel_data output: omit an entry to discard that channel, and edit values in place to correct them. This is how a reviewed and edited set of channels comes back from a caller that has no access to the folder.
    _calibration_file_key is ignored on input, because filenames are re-derived from the supplied values and an edit can therefore rename a file. The channels are validated against the standardized schema before anything is written, and the folder is cleared first so a discarded channel leaves no orphan behind for the mapping to match. Folded into the standardization fingerprint, so changing them rewrites the folder, and a later run that parses the manufacturer files again clears what an override wrote rather than mapping both sets.

    ```python
    # --- Parameters ---
    _standardize_cal__cal_input_folder = '../calibration_files/HB201607_cal'
    _standardize_cal__output_base = './outputs/calibration'
    _standardize_cal__cruise_id = 'HB1603'
    _standardize_cal__record_author = '<Your Name>'
    _standardize_cal__short_filenames = True

    _result = standardize_calibration_files(
        frequencies=frequencies,
        cal_input_folder=_standardize_cal__cal_input_folder,
        output_base=_standardize_cal__output_base,
        cruise_id=_standardize_cal__cruise_id,
        record_author=_standardize_cal__record_author,
        short_filenames=_standardize_cal__short_filenames,
    )
    single_channel_dir = _result['single_channel_dir']
    channel_count = _result['channel_count']
    skipped = _result['skipped']
    standardize_cal_single_channel_data = _result['single_channel_data']
    ```

    ##### Step: `build_cal_mapping`
    **Op:** `build_calibration_mapping`

    Match each raw channel to its calibration data and save the mapping files.
    Loads the raw file configurations and the standardized single-channel files, runs the matching algorithm, moves aside any calibration file no raw channel matched, resolves conflicts, writes mapping_files/channel_mapping.yaml, calibration_configurations.yaml and calibration_provenance.yaml, and verifies the result.
    Reads the single-channel files from disk rather than taking them as data. The mapping keys are those files' names, which only the on-disk loader attaches, and this step rewrites that folder.
    Mark it regenerate: always in a recipe. Its real product is the files on disk, and a hand-deleted single-channel file cannot change a cache key computed before the run, so a cache hit would return the dictionaries without re-reading the folder. The step is cheap: a few small local YAML reads and the matcher.

    **Parameters:**
    - `output_base` path - Root directory the calibration artifacts are written under. Normally wired to initial_setup's calibration_output_dir.

    - `single_channel_dir` path - Folder of standardized single-channel files. Defaults to single_channel_calibration_files/ under output_base. Wire it to the standardization step's single_channel_dir output for the ordering dependency. Not fingerprinted, because a step-output reference is still an unresolved placeholder when cache keys are computed; use regenerate: always instead.

    - `raw_configs_path` path - Path of the saved raw_file_configs.yaml. Defaults to raw_file_configs/raw_file_configs.yaml under output_base, and is only consulted when raw_file_configs is not supplied.

    - `conflict_resolution` str - What to do when one raw channel matches several calibration files. "error" (the default) lists the conflicts and stops, so you delete the unwanted single-channel file and re-run. "interactive" prompts on the terminal, and raises before any file is moved when the run has no interactive input. "report" neither raises nor prompts: it returns the conflicts on the conflicts output and writes no mapping file, which is the mode for a caller with no terminal that will present the choices and run the step again with calibration_choices.

    - `calibration_choices` dict - Conflict decisions supplied up front, mapping a conflict id from a previous run's conflicts output to the calibration key to keep. A list of candidate keys in place of one key averages them: the average is written as a new single-channel file with is_averaged set, the conflict's channels are mapped to it, and the candidates it was made from are set aside. Only calibrations of the same transducer, at the same settings and with the same sphere, can be averaged. Applied in every conflict_resolution mode before that mode's handler runs, so a partial map resolves what it covers and leaves the rest to be reported, prompted for, or raised on. An unknown conflict id, a key that is not one of that conflict's candidates, or candidates that cannot be averaged, is an error, and nothing is changed.

    - `tolerances` dict - Per-field match tolerances, merged over the matcher's defaults so a dict naming only one field widens only that field. The defaults are exact for frequency, frequency_start, frequency_end and transmit_power (1 Hz / 1 W) and 1e-6 s for transmit_duration_nominal, because a calibration is formally valid only for the configuration it was measured at.
    Widening one is a scientific decision, not a convenience: a channel that matches only under a widened tolerance was calibrated at a different setting than it is being applied to, and the resulting gain and sa_correction carry a bias of that difference. Every override is reported on the terminal. Reach for it when a survey changed a setting mid-cruise and no calibration exists for the other side of the change, and record why in the recipe.

    - `unmapped_channels` str - The policy the extract_standardized_cal_params step that consumes this mapping runs with, "warn" or "error". Informational only: it changes nothing about the matching, and only lets the provenance report state what actually happened to an unmatched channel instead of listing both possibilities. Set it to the same value that step uses, or leave it out and the report says the outcome is decided downstream.

    - `cruise_id` str - Recorded in the provenance report when given.
    - `record_author` str - Recorded as the author of any record averaged while resolving conflicts. When omitted, the averaged calibrations' author is kept if they share one.

    - `keep_unused` bool - Move calibration files no raw channel matched into unused_calibration_files/ instead of deleting them. They are the evidence when a channel comes out unmapped.

    - `short_filenames` bool - Remap the returned dictionaries to compact calibration keys when they are not already the single-channel file names, and give averaged records compact keys. Should match the setting the single-channel files were written with.

    - `verbose` bool - Print the mapping summary, preview, and verification results.

    ```python
    # --- Parameters ---
    _build_cal_mapping__output_base = './outputs/calibration'
    _build_cal_mapping__single_channel_dir = single_channel_dir
    _build_cal_mapping__raw_configs_path = raw_configs_path
    _build_cal_mapping__short_filenames = True
    _build_cal_mapping__keep_unused = True
    _build_cal_mapping__conflict_resolution = 'error'

    _result = build_calibration_mapping(
        raw_file_configs=raw_file_configs,
        output_base=_build_cal_mapping__output_base,
        single_channel_dir=_build_cal_mapping__single_channel_dir,
        raw_configs_path=_build_cal_mapping__raw_configs_path,
        short_filenames=_build_cal_mapping__short_filenames,
        tolerances={'transmit_power': 1001.0},
        keep_unused=_build_cal_mapping__keep_unused,
        conflict_resolution=_build_cal_mapping__conflict_resolution,
    )
    mapping_dict = _result['mapping_dict']
    calibration_dict = _result['calibration_dict']
    missing_params = _result['missing_params']
    unused_files = _result['unused_file_names']
    provenance = _result['provenance']
    provenance_path = _result['provenance_path']
    conflicts = _result['conflicts']
    build_cal_mapping_single_channel_data = _result['single_channel_data']
    ```

    *End of included section: processing_lvl_0_user.yaml*

    #### Included: processing_lvl_1_per_file.yaml

    Steps: read_raw, combine_raw

    ##### Step: `read_raw`
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

    ##### Step: `combine_raw`
    **Op:** `combine_raw_files`

    Lazily open per-file intermediate stores (zarr or netcdf paths) produced by read_raw_files, or combine in-memory EchoData objects (none mode), into a single combined EchoData object. Mark this step checkpoint: always in your recipe so the checkpoint system writes the combined output once and reuses it on subsequent runs.
    The reading and the combine itself are echopype.open_converted and echopype.combine_echodata. Around them this step adds store-path handling, input ordering, and the ping_time chunking of the combined result.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk length along ping_time for the combined EchoData, which directly sets the chunking of the downstream zarr checkpoint. Larger values mean fewer, bigger objects: faster remote (gs://) checkpoint writes and fewer read requests, at the cost of more RAM per computed chunk and coarser lazy-read granularity for windowed access. 1000 is a balanced default; try 2000-4000 for bucket-backed caches if the combined checkpoint write is slow and memory allows.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `extract_cal_params`
    **Op:** `extract_standardized_cal_params`

    Extract calibration parameters (gain, sa-correction, EBA) and environmental parameters (sound speed, absorption) from standardized calibration files, cross-referenced against the EchoData object for channel alignment.

    **Parameters:**
    - `raw_file_path` path - Path to the specific raw file whose channel mapping should be selected from mapping_dict (matched by basename). Wire this to ${_item} in a per-file map_over chain so each instance picks its own file's calibration mapping instead of defaulting to the first file in the survey. Leave unset for whole-survey recipes.

    - `unmapped_channels` str - What to do about a channel the mapping does not cover, which happens when no calibration file matched it. "warn" (the default) reports the channel on the terminal and falls back to the values the raw file itself records, which is what echopype would calibrate that channel with given no calibration file at all. "error" stops the run instead. The fallback needs the echodata input; without it "warn" raises the way "error" would.

    - `output_logs_folder` path - Where the fallback writes its calibration flags. A temporary folder is used when omitted, which is what a per-file chain wants: several files falling back at once would otherwise race on one flags file.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `compute_sv`
    **Op:** `compute_sv`

    Compute volume backscattering strength (Sv) from raw EchoData. When cal_params and env_params are supplied they are applied to the computation. When omitted, echopype uses default environmental and calibration values embedded in the raw file.
    This step is echopype.calibrate.compute_Sv called directly, so the calibration model and its defaults are echopype's.

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `compute_transducer_depth`
    **Op:** `compute_transducer_depth`

    Compute per-ping transducer depth in metres below the water surface from the EK Platform group, for feeding ``ep_add_depth`` as its ``depth_offset``.
    Reads the Platform group directly rather than going through ``ep_add_depth(use_platform_vertical_offsets=true)``, which gets the sign wrong on both EK families for different reasons.
    On an EK80 drop keel vessel the transducers ride on a retractable keel and ``drop_keel_offset`` records how far it was lowered, while the ``transducer_offset_z`` and ``water_level`` fields echopype's formula reads are left at zero. echopype carries ``drop_keel_offset`` for provenance and never reads it.
    An EK60 file has no ``drop_keel_offset`` at all, and echopype maps the ER60 per-ping transducer depth into ``water_level``, whose value its formula then subtracts (``transducer_offset_z - (water_level + vertical_offset)``, with ``transducer_offset_z`` zero because the ER60 does not populate it). The transducer comes out above the water surface and every depth is short by twice the draft. This step reads ``water_level`` directly instead.
    ``drop_keel_offset`` is preferred when both are present; the field actually used is recorded in the output's ``static_offset_field`` attr.
    The static position is per file and heave is per ping, so the result is ``static offset + datum_correction_m + heave_sign * vertical_offset``. Heave is interpolated onto ds_Sv's ping_time here, which also means ``ep_add_depth`` passes the array straight through instead of resampling it again with nearest neighbour.
    The output is small (one value per ping) and is the record of what vertical reference a run actually used, so checkpointing it is cheap and makes the reference auditable without recomputing Sv.

    **Parameters:**
    - `datum_correction_m` float (m) - Metres added to the static offset to reach depth below the water surface, for when the recorded offset is measured from a hull datum rather than the waterline. Positive pushes the transducer deeper. The default 0.0 takes the recorded offset as already waterline referenced.

    - `use_heave` bool - Add the per-ping ``vertical_offset`` (heave) term. False holds the static keel depth on every ping. When the Platform group carries no usable heave the step reports that and falls back to the static depth.

    - `heave_sign` float - Sign applied to ``vertical_offset``. The default 1.0 was determined empirically on HB2407 (EK80, Henry B. Bigelow) by correlating raw heave against the hand-verified EVL seabed line: the correct sign left a 0.14 m residual against 0.75 m for the wrong one. The convention is not recorded in the file attributes and is not the same sign echopype's own platform-vertical-offsets formula uses (that formula is referenced from a hull datum, not drop_keel_offset) - re-verify the same way on a new vessel or instrument setup rather than assuming.

    - `interp_method` str - Interpolation used to put heave on ping_time. Heave is typically sampled several times coarser than the ping rate, so the default linear is preferred over nearest.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `ep_add_depth`
    **Op:** `ep_add_depth`

    Add a surface-referenced ``depth`` data variable to an Sv Dataset. This step is echopype.consolidate.add_depth called directly, so the depth computation and its parameters are echopype's. The result preserves all existing variables (including ``echo_range``) and adds ``depth`` on ``(channel, ping_time, range_sample)`` in metres below surface.
    Whatever depth-computation technique is selected (constant ``depth_offset``, Platform vertical offsets, Platform/Beam angles, user-supplied ``tilt``) is fully captured in the values of the resulting ``depth`` array, so downstream consumers can treat the dataset as the single source of truth for vertical reference frame without knowing which technique was used.

    **Parameters:**
    - `depth_offset` (m) - Vertical offset for the transducer position, in metres. Either a constant, or a per-ping DataArray wired from a producer step such as ``compute_transducer_depth`` (echopype accepts both on this argument; a DataArray must have exactly one dimension and is aligned to ping_time). When set, takes precedence over Platform vertical offsets. Left untyped because the two accepted forms are a scalar and a DataArray.

    - `tilt` float (degree) - Constant transducer tilt; 0 corresponds to a vertically-pointing transducer. When set, takes precedence over Platform/Beam angles.

    - `downward` bool - True when transducers point downward (typical hull-mounted).
    - `use_platform_vertical_offsets` bool - Use Echodata Platform group vertical offsets to compute transducer depth. EK60/EK80 only. Ignored when ``depth_offset`` is set.

    - `use_platform_angles` bool - Use Echodata Platform group angles to compute echo_range scaling. EK60/EK80 only. Cannot be combined with ``use_beam_angles``.

    - `use_beam_angles` bool - Use Echodata Beam group angles to compute echo_range scaling. EK60/EK80 only. Cannot be combined with ``use_platform_angles``.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `crop_survey_range`
    **Op:** `crop_range`

    Trim trailing range samples that carry no data. A raw file records to the sonar's configured range, so after seafloor masking the deep end of ``range_sample`` is entirely NaN. Those samples still cost memory and time in every downstream step, and they widen the binned cell grid that ``compute_mvbs`` and ``compute_per_cell_statistics`` build - the flox reduction behind the latter allocates dense bookkeeping sized by channels x ping bins x range bins, so an over-wide range axis is what pushes it into a MemoryError on a long survey.
    A standalone step rather than a parameter on ``apply_sv_mask``, for the same reason ``rechunk_dataset`` is standalone: masking is ping-local and runs correctly per file, but the crop point is a property of the whole survey. Cropping per file would leave the segments with different ``range_sample`` lengths, and the ping_time concatenation resolves that by padding the short ones back out with NaN - which undoes the crop and leaves NaN in ``echo_range``, where downstream range binning reads it. Place this after the fan-in instead.
    Three criteria are available and compose - whichever are active each produce a candidate cut and the shallowest wins:
    * ``outlier_sigma`` rejects anomalous pings and then crops to the deepest
      **retained** ping. Prefer this survey-wide: a single glitch ping reaching
      far past the seafloor otherwise holds the whole range axis open, which is
      exactly what defeats the lossless mode. The threshold is deliberately
      generous, so uncommon-but-real depths survive and only wild outliers go.
    * ``max_range_m`` is a hard ceiling, applied whether or not samples above it
      hold data. Cheapest when the answer is already known (e.g. from the
      deepest point in the survey's seabed lines).
    * ``drop_trailing_all_nan`` is the lossless fallback used when no outlier
      test is requested.

    **Parameters:**
    - `max_range_m` float (m) - Explicit cap. Range samples deeper than this are dropped whether or not they hold data, so a cap shallower than the deepest echo of interest discards it. Leave unset to derive the crop from the data.

    - `outlier_sigma` float - Standard deviations above the mean, over the per-ping deepest finite sample, at which a ping becomes a candidate outlier. Combined with outlier_margin to form the reject threshold. Leave unset to disable outlier rejection and fall back to drop_trailing_all_nan.

    - `outlier_margin` float - Multiplier applied to (mean + outlier_sigma * sd) to get the reject threshold. Raise it to flag fewer pings, lower it to flag more. The default 1.5 is deliberately loose: on HB2407's 2024-10-15 window it puts the threshold at 424 m against a deepest retained ping of 214 m, so it catches only genuine anomalies. Only used when outlier_sigma is set.

    - `drop_trailing_all_nan` bool - Crop to one past the deepest range sample holding a finite Sv value anywhere in the dataset. Lossless. Ignored when outlier_sigma is set. Set false with the other two criteria unset to disable the step without removing it from the recipe.

    - `data_var` str - Variable inspected for finite values.
    - `range_var` str - Variable supplying each sample's range in metres.

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_sv`
    **Op:** `select_ping_time_range`

    Narrow a Dataset to a ping_time window. The user-facing entry point into a survey-wide Sv store: a survey-level recipe computes and checkpoints Sv once for the whole cruise, and a user recipe reuses that checkpoint and calls this to work on its own segment.
    Cheap on a zarr-backed Dataset. ``sel`` narrows the dask graph to the chunks the window touches, so only those are read - measured on a 39570-ping store with 3305-ping chunks, a 4001-ping window read 4 of 24 partitions in 1.5 s and 1.4 GB against 16.5 s and 8.9 GB for the whole array. Chunk the survey store along ping_time (see ``rechunk_dataset``) at roughly the window size users ask for.
    PLACEMENT MATTERS. Step hashes are a Merkle chain: each step folds in its parents' hashes. Wiring a user's window into an upstream step's params - for example ``initial_setup``'s file_time_start - changes that step's hash and every hash below it, so no two windows ever share a cached Sv. Keep the window on this step, downstream of everything whose checkpoint you want shared, and give the survey-level steps a fixed survey-wide window instead.

    **Parameters:**
    - `start` str - Inclusive ISO datetime lower bound (e.g. "2024-10-15T13:38:52"). Leave unset to leave the start open.

    - `end` str - Inclusive ISO datetime upper bound. Leave unset to leave the end open. The step fails rather than returning an empty Dataset when the window selects no pings.

    - `window` dict - Mapping supplying start and end instead of passing them directly. Mutually exclusive with them: giving both is an error rather than a silent precedence rule.
    This is the form a mapped fan-out needs. ${_item} is an all-or-nothing token with no attribute access - there is no ${_item.start} - so a step mapped over a list of window dicts cannot pull the two bounds out of the item as separate params and has to take the dict itself. Wire it as ``window: ${_item}``.
    Extra keys are ignored, so one dict can carry everything an instance needs (a label, its line files, its raw file list) while this step reads only the bounds. An optional ``label`` key is printed to identify the instance in the run log.

    - `dim` str - Time coordinate to slice. Binned products carry the bin's left edge under a different name, so a compute_per_cell_statistics dataset is windowed with "cell_ping_time" while raw Sv uses the default. The coordinate has to be datetime-valued either way, which the cell grid's left edges are.

    - `windows` list - Several window dicts at once, each with ``start`` and ``end`` as the single ``window`` form takes. Keeps every ping inside at least one of them, in time order, each ping once even where windows overlap.
    This is how a step mapped over the survey's per-file Sv pulls a whole set of dive windows out of each file in one pass: the instance sees one file and cannot loop over the list itself. Wire it straight from the planner, ``windows: ${plan_dives.windows}``, and pair it with ``allow_empty: true`` so the files no window touches drop out at the fan-in. Mutually exclusive with ``window`` and ``start``/``end``.

    - `snap_to_bin` str - Bin width such as "10s". Widens every window outward to whole bins of that width before selecting: the start moves back to its bin's left edge and the end forward to the last instant of its bin.
    Set it to the downstream ping_time_bin when this selection feeds compute_mvbs. A window cut at its exact bounds leaves the first and last bins averaged over only part of their pings, and a window that ends just inside a bin can leave that bin with no ping at all, so the grid comes out one bin short. A later selection on the binned grid still trims to the true window.

    - `allow_empty` bool - Return nothing instead of failing when the window selects no pings.
    This is what lets the step be mapped over a whole survey's per-file Sv rather than applied to one merged store. Most files lie outside any one window, and ``merge_datasets`` drops the empty instances when it collects, so the fan-in keeps exactly the files the window touches. That is the route to a windowed analysis that reuses the per-file checkpoints without a survey-wide merge ever being built.
    Left false by default: against a single merged store, a window that selects nothing is a mistake and should fail loudly.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_surface_mask`
    **Op:** `create_surface_mask`

    Build a surface-interference mask from baseline Sv using a selectable method.

    **Parameters:**
    - `surface_depth_m` float (m) - Depth above which samples are masked out, removing the transmit pulse ringdown and near-surface bubble layer. The default of 0.0 masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_frequency_mask`
    **Op:** `create_frequency_mask`

    Build a mask that excludes selected frequency channels. Implementation can be swapped to use different exclusion criteria.

    **Parameters:**
    - `frequencies_to_mask` list (kHz) - Nominal channel frequencies to exclude, as numbers in kHz (e.g. [70, 120, 200]). Every sample on a listed channel is marked invalid and every other channel is left untouched. The default empty list masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_seabed_line`
    **Op:** `read_seafloor_line`

    Read a seafloor line from an Echoview ``.evl`` line file and align it to the Sv dataset's pings, returning a 1-D (ping_time,) DataArray in metres. Use it when a hand-verified Echoview bottom line is more trustworthy than running detection over the Sv data.
    Echoview exports a survey as a series of part-day line files, so ``evl_path`` may be a folder: the files whose names span ``file_time_start`` to ``file_time_end`` are selected and concatenated into one line before interpolation. Give it the same window that selected the raw files.
    Drop-in swap with the ``detect_seafloor`` and ``ep_detect_seafloor`` ops: the output port name (``seafloor_depth``) and shape (1-D, ping_time) match, so downstream steps such as ``create_seafloor_mask`` work unchanged. The recipe step's ``inputs`` drop the ``echodata`` line (no detection is run) and the ``params`` block takes ``evl_path`` plus the alignment settings below instead of ``channel`` / ``min_valid_depth_m`` or ``method`` / ``method_params``.
    Pings the line does not cover come back as NaN, and ``create_seafloor_mask`` rejects **every sample** of a ping whose seafloor is NaN (the metres-vs-metres comparison is False against NaN). The step therefore prints its ping coverage on every run; set ``min_coverage`` to turn a shortfall into a hard error, or widen ``edge_extend_s`` / ``max_gap_s`` if the gaps are benign.

    **Parameters:**
    - `evl_path` path - A single Echoview ``.evl`` line file, or a folder of them to select from with file_time_start / file_time_end. May be a local path or a remote fsspec URL (e.g. gs://bucket/survey/seabed_lines); remote files are read in place (no download) since line files are small. The must_exist check is skipped at validation time for remote URLs and enforced by the step at run time instead.
    Mutually exclusive with ``window``: exactly one of the two is required.

    - `window` dict - Mapping carrying the line path under ``window_key``, used instead of ``evl_path``.
    This is the form a mapped fan-out needs. ${_item} is an all-or-nothing token with no attribute access - there is no ${_item.seabed_evl} - so a step mapped over a list of window dicts cannot pull the path out of the item as its own param and has to take the dict. Wire it as ``window: ${_item}``.
    A window also supplies file_time_start / file_time_end when those are not given directly, so a folder of part-day seabed exports narrows to the window without repeating its bounds. Extra keys are ignored, and an optional ``label`` names the instance in errors.
    Note that a window's path is NOT content-fingerprinted the way evl_path is: it arrives as a runtime value rather than a declared param, so re-exporting that line does not by itself invalidate the step. The upstream step that produced the window is what carries its identity.

    - `window_key` str - Key the line path is read from when ``window`` is given. Defaults to "seabed_evl", which is what plan_dive_datasets emits.

    - `vertical_reference` str - What the EVL depths are measured from. "surface" (the default, and Echoview's usual seabed-line reference) means metres below the water surface; "transducer" means metres along the beam from the transducer face. The line is converted to whichever reference ds_Sv carries - depth when ep_add_depth ran upstream, otherwise echo_range.

    - `min_status` int - Drop line points whose Echoview status is below this value (0 = none, 1 = unverified, 2 = bad, 3 = good). When null (the default) every point is kept. Dropped points leave a gap in the line, subject to max_gap_s.

    - `max_gap_s` float (s) - Widest hole in the line, in seconds, to interpolate across; pings sitting inside a wider hole come back as NaN. A ping landing exactly on a line point always keeps that point's depth. When null (the default) holes of any width are interpolated across.

    - `edge_extend_s` float (s) - How many seconds past the line's first and last point to hold that point's depth. The default 0.0 does no extrapolation at all, so pings outside the line's time span are NaN - this is what keeps a line covering only part of a survey from silently asserting a constant seafloor over the rest. Set null to hold the end depths indefinitely.

    - `depth_offset_m` float (m) - Constant metres added to the line, for when the transducer draft configured in Echoview differs from the one baked into ds_Sv['depth']. Positive pushes the seafloor deeper.

    - `min_coverage` float - Fraction of pings (0-1) that must end up with a finite seafloor depth; below it the step fails instead of silently masking the uncovered pings away. Default 0.0 never fails.

    - `file_time_start` str - Optional inclusive ISO datetime (e.g. "2024-10-12T00:15") selecting which line files in an evl_path folder to read, by the d{YYYYMMDD}_t{HHMMSS}-t{HHMMSS} span in their names. Pass the same window that selected the raw files so the line and the pings stay in step.
    A file's span runs from its own name stamp to the *next* file's, not to the end stamp in its own name - Echoview's end stamp under-reports the line's real last point by about one raw file's duration, so trusting it would drop the export straddling the window start and leave the leading pings with no seafloor. Selection is name-based, so no line data is read (or downloaded) to filter. Ignored when evl_path names a single file.

    - `file_time_end` str - Optional inclusive ISO datetime upper bound for line file selection. See file_time_start.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_seabed_mask`
    **Op:** `create_seafloor_mask`

    Build a seafloor mask from baseline Sv and detected seafloor depth. The seafloor depth is passed as an explicit input, enabling swappable detection methods without modifying masking logic.

    **Parameters:**
    - `seafloor_buffer_m` float (m) - Height above the detected seafloor line to also mask out. Widens the exclusion zone to cover detection error and the near-bottom dead zone, at the cost of discarding real backscatter just above the bottom.

    - `range_var` str - Name of the meter-valued variable on ds_Sv to compare against the seafloor line. Must use the same vertical reference as the seafloor line. When null (the default), auto-selects "depth" if present on ds_Sv (i.e., add_depth was run upstream), otherwise falls back to "echo_range".


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_combine_masks`
    **Op:** `combine_masks`

    Combine two or more mask datasets into one final mask. Accepts a list of mask inputs to support inserting additional custom mask-producing steps into the chain without changing core framework code.

    **Parameters:**
    - `mode` str - Combination mode: 'and' (all must be valid) or 'or' (any must be valid)

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_apply_mask`
    **Op:** `apply_sv_mask`

    Apply a boolean mask to an Sv Dataset, setting masked samples to NaN.
    Thin wrapper over echopype.mask.apply_mask, calling it against the Sv variable with NaN as the fill value. It adds nothing of its own, so the broadcasting and alignment rules are echopype's.

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_remove_noise`
    **Op:** `remove_background_noise`

    Estimate and remove background noise from Sv using the De Robertis and Higginbottom method. Returns a cleaned Sv Dataset.
    Thin wrapper over echopype.clean.remove_background_noise. Every parameter below is forwarded to it unchanged, so its behavior is echopype's. The one thing the wrapper adds is that the corrected values are copied over the Sv variable, so downstream steps reading Sv get the cleaned data without having to know about Sv_corrected. Both variables are present on the result.

    **Parameters:**
    - `ping_num` int - Number of pings used to estimate the noise level
    - `range_sample_num` int - Number of range samples used to estimate the noise level
    - `background_noise_max` str - Upper limit on the estimated noise level (e.g. '-125dB'); omit to leave it unbounded
    - `SNR_threshold` str - Signal-to-noise ratio below which corrected samples are masked out (e.g. '3.0dB')

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_mask_sparse`
    **Op:** `mask_sparse_bins`

    Remove spatiotemporal bins with fewer valid samples than a threshold, reducing noise in MVBS computation.

    **Parameters:**
    - `range_bin` str - Range bin size (e.g. '2m')
    - `ping_time_bin` str - Ping-time bin size (e.g. '10s')
    - `nan_threshold` float - Fraction of NaN values above which a bin is masked (0.0 to 1.0)
    - `range_var` str - Meter-valued variable to bin along, which must match the value given to compute_mvbs and compute_per_cell_statistics or the sparse mask lands on a different grid than the one it is meant to clean. 'echo_range' measures from the transducer face, 'depth' from the surface and needs the dataset to carry a depth variable. The bin edges are read from the first ping of the first channel, so a range variable that varies from ping to ping, as depth does under platform vertical offsets, is approximated by its first ping.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `dive_file_mvbs`
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

    ##### Step: `dive_cell_stats`
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

    *End of included section: processing_lvl_1_per_file.yaml*

    #### Included: processing_lvl_2_combined.yaml

    Steps: extract_cal_params, compute_sv, compute_transducer_depth, ep_add_depth, crop_survey_range, dive_sv, dive_surface_mask, dive_frequency_mask, dive_seabed_line, dive_seabed_mask, dive_combine_masks, dive_apply_mask, dive_remove_noise

    *End of included section: processing_lvl_2_combined.yaml*

    #### Included: processing_lvl_3_user.yaml

    Steps: dive_mask_sparse, dive_file_mvbs, dive_cell_stats, merge_dive_files_mvbs, rechunk_dive_mvbs, merge_dive_files_cell_stats

    ##### Step: `merge_dive_files_mvbs`
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

    ##### Step: `rechunk_dive_mvbs`
    **Op:** `rechunk_dataset`

    Rechunk a Dataset along ping_time. A standalone step rather than a parameter on any particular masking or reduction op, so a recipe can insert (or remove) chunking wherever a downstream op should run dask-parallel, independent of which step happens to run immediately before it.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk size along ping_time.

    ```python
    # --- Parameters ---
    _rechunk_dive_mvbs__ping_time_chunk = 1000

    rechunk_dive_mvbs_ds_Sv = rechunk_dataset(ds_Sv=merge_dive_files_mvbs_merged, ping_time_chunk=_rechunk_dive_mvbs__ping_time_chunk)
    ```

    ##### Step: `merge_dive_files_cell_stats`
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

    *End of included section: processing_lvl_3_user.yaml*

    #### Included: processing_lvl_4.yaml

    Steps: reshape_ml, add_aux_features, normalize_ml, run_hdbscan, label_all_points, embed_results, plot_clustering_report

    ##### Step: `reshape_ml`
    **Op:** `reshape_for_ml`

    Reshape an MVBS Dataset into a flat, ML-ready xarray Dataset where each observation corresponds to one spatial or temporal bin.

    **Parameters:**
    - `data_var` str - Name of the data variable to use as features
    - `dataset_name` str - Name this ML dataset is stored under inside the returned Dataset. Every later ML step (add_auxiliary_features, normalize_ml_data, run_hdbscan, the ML plots) takes the same dataset_name to find the feature matrix again, so a recipe can carry several independent ML datasets side by side.

    - `feature_strategy` str - Feature construction strategy (e.g. 'mean_centered')
    - `baseline_channel` int - Channel index used as the baseline when computing relative features

    ```python
    # --- Parameters ---
    _reshape_ml__data_var = 'Sv'
    _reshape_ml__dataset_name = 'ml_dataset'
    _reshape_ml__feature_strategy = 'mean_centered'
    _reshape_ml__baseline_channel = 0

    reshape_ml_ds_ml_ready = reshape_data_for_ml(
        ds_Sv=rechunk_dive_mvbs_ds_Sv,
        data_var=_reshape_ml__data_var,
        dataset_name=_reshape_ml__dataset_name,
        feature_strategy=_reshape_ml__feature_strategy,
        baseline_channel=_reshape_ml__baseline_channel,
    )
    ```

    ##### Step: `add_aux_features`
    **Op:** `add_auxiliary_features`

    Append auxiliary coordinate-derived features (e.g. depth, ping_time_seconds) to the flattened ML-ready Dataset. Optional step; omit when no auxiliary features are needed.

    **Parameters:**
    - `dataset_name` str - ML dataset to append the features to. Must match the dataset_name used by reshape_for_ml.

    - `features` list - List of built-in feature name strings to append. Supported built-ins: 'depth', 'ping_time_seconds', 'seafloor_depth', 'altitude', 'cell_cv'. 'seafloor_depth' and 'altitude' require the echodata input. 'cell_cv' requires the ds_sv input (from compute_per_cell_statistics).

    ```python
    # --- Parameters ---
    _add_aux_features__dataset_name = 'ml_dataset'
    _add_aux_features__features = ['cell_cv']

    add_aux_features_ds_ml_ready = add_auxiliary_features(
        ds_ml_ready=reshape_ml_ds_ml_ready,
        ds_sv=merge_dive_files_cell_stats_merged,
        dataset_name=_add_aux_features__dataset_name,
        features=_add_aux_features__features,
    )
    ```

    ##### Step: `normalize_ml`
    **Op:** `normalize_ml_data`

    Normalize ML features in the flattened Dataset. Supports per-feature normalization methods via per_group_methods.

    **Parameters:**
    - `method` str - Global normalization method (e.g. 'flatten', 'minmax', 'zscore')
    - `dataset_name` str - ML dataset to normalize. Must match the dataset_name used by reshape_for_ml.

    - `normalization_name` str - Name this normalized result is stored under. run_hdbscan, the ML plots, and filter_normalized_by_cluster take the same name to find it, so several normalizations of one dataset can coexist and be compared.

    - `shift_positive` bool - Shift all values to be non-negative after normalization
    - `feature_weights` list - Per-feature weight multipliers applied before normalization
    - `per_group_methods` dict - Map of feature name to individual normalization method override

    ```python
    # --- Parameters ---
    _normalize_ml__method = 'flatten'
    _normalize_ml__dataset_name = 'ml_dataset'
    _normalize_ml__normalization_name = 'normalized_flatten_mean_centered'
    _normalize_ml__shift_positive = False

    normalize_ml_ds_normalized = normalize_data(
        ds_ml_ready=add_aux_features_ds_ml_ready,
        method=_normalize_ml__method,
        dataset_name=_normalize_ml__dataset_name,
        normalization_name=_normalize_ml__normalization_name,
        shift_positive=_normalize_ml__shift_positive,
    )
    ```

    ##### Step: `run_hdbscan`
    **Op:** `run_hdbscan`

    Run HDBSCAN unsupervised clustering on normalized feature data. Pure-compute step: accepts normalized features and returns a clustering result dict without modifying any Dataset. Designed to be a sweep target for hyperparameter comparison.

    **Parameters:**
    - `dataset_name` str - ML dataset to cluster. Must match the dataset_name used by reshape_for_ml.

    - `normalization_name` str - Normalized feature set to cluster. Must match the normalization_name used by normalize_ml_data.

    - `ml_result_name` str - Name this clustering result is stored under. embed_clustering_results and plot_clustering_report take the same name to find it, so a recipe can keep several clusterings of one dataset side by side.

    - `min_cluster_size` int - Absolute minimum cluster size. Mutually exclusive with min_cluster_size_fraction.
    - `min_cluster_size_fraction` float - Fraction of the points actually fitted used to derive min_cluster_size when min_cluster_size is omitted. Mutually exclusive with min_cluster_size. When omitted, the ML function defaults to 0.03.
    Taken of the subsample when sample_size binds, so the number means the same thing at any scale: a cluster must hold this share of what the model saw. Raising sample_size therefore raises the absolute floor proportionally, and lowering it lowers it.

    - `min_samples` int - Neighbourhood size HDBSCAN uses to judge how dense a point is. Higher values make the run more conservative, pushing sparse points into noise instead of into a cluster.

    - `sample_size` int - Cap on how many samples are clustered. A survey flattens to more points than HDBSCAN can handle in memory, so when the dataset is larger than this a random subset of this size is clustered and the remaining points receive no label at all. Set it above the sample count to cluster everything.

    - `cluster_selection_method` str - HDBSCAN cluster selection method ('eom' or 'leaf')
    - `use_hdbscan` bool - Use HDBSCAN; set false to fall back to a simpler clustering method
    - `epsilon` float - DBSCAN epsilon distance parameter
    - `find_background_cluster` bool - Run background-cluster detection path instead of standard HDBSCAN. When true, background_label output is populated.

    - `soft_membership_threshold` float - Reassign noise points via soft-membership probabilities when set. null disables soft-membership reassignment.

    ```python
    # --- Parameters ---
    _run_hdbscan__dataset_name = 'ml_dataset'
    _run_hdbscan__normalization_name = 'normalized_flatten_mean_centered'
    _run_hdbscan__ml_result_name = 'hdbscan_results'
    _run_hdbscan__min_cluster_size_fraction = 0.01
    _run_hdbscan__min_samples = 4
    _run_hdbscan__sample_size = 1200000
    _run_hdbscan__cluster_selection_method = 'leaf'
    _run_hdbscan__use_hdbscan = True
    _run_hdbscan__soft_membership_threshold = 0.1

    _result = run_hdbscan(
        ds_normalized=normalize_ml_ds_normalized,
        dataset_name=_run_hdbscan__dataset_name,
        normalization_name=_run_hdbscan__normalization_name,
        ml_result_name=_run_hdbscan__ml_result_name,
        min_cluster_size_fraction=_run_hdbscan__min_cluster_size_fraction,
        min_samples=_run_hdbscan__min_samples,
        sample_size=_run_hdbscan__sample_size,
        cluster_selection_method=_run_hdbscan__cluster_selection_method,
        use_hdbscan=_run_hdbscan__use_hdbscan,
        soft_membership_threshold=_run_hdbscan__soft_membership_threshold,
    )
    run_hdbscan_clustering_results = _result['clustering_results']
    run_hdbscan_clustering_model = _result['clustering_model']
    background_label = _result['background_label']
    run_hdbscan_cluster_labels = _result['cluster_labels']
    ```

    ##### Step: `label_all_points`
    **Op:** `assign_clusters_to_all_points`

    Extend a subsampled clustering to every point in the dataset, so no cell is left unlabelled.
    ``run_hdbscan`` clusters at most ``sample_size`` points, and above that the remaining points receive no label at all - they come back NaN once ``embed_clustering_results`` regrids the result. On a survey-scale ML dataset that is most of the data. This step projects the fitted structure onto the unlabelled points with hdbscan's ``approximate_predict``, which walks each point down the condensed tree the fit produced and returns the cluster it falls into, or -1 when it falls outside every cluster.
    Points the fit did see keep their fitted label rather than a predicted one: the fit is the more authoritative answer where it exists, and only the remainder is being filled in.
    Place it between ``run_hdbscan`` and ``embed_clustering_results``. The output is the same shape ``run_hdbscan`` produces, so everything downstream (``embed_clustering_results``, ``plot_clustering_report``, ``merge_clustering_passes``) consumes it unchanged.
    CHECKPOINTING. This step needs the fitted model, and a checkpointed clustering_results Dataset does not carry one: converting a result for Zarr drops the model and records only model_type and model_available. The clustering_model port is a plain object, so it falls back to pickle format, which the tiered store refuses to write to the shared survey tier. To checkpoint this step without pickle, set keep_model: false: the model port is then null, every output is Zarr or JSON, and a downstream plot_clustering_report simply skips the hierarchy panel. Otherwise keep this step adjacent to the run_hdbscan that feeds it, in the user tier, or set checkpoint: never and let it recompute.

    **Parameters:**
    - `dataset_name` str - ML dataset the clustering belongs to. Must match the dataset_name used by reshape_for_ml and run_hdbscan.

    - `normalization_name` str - Normalized feature set that was clustered. Must match the normalization_name used by normalize_ml_data and run_hdbscan.

    - `ml_result_name` str - Name the extended result is stored under. Leave unset to keep the name already on the incoming result, which is what keeps a downstream embed_clustering_results / plot_clustering_report pair pointing at it without changes.

    - `batch_size` int - Rows predicted per approximate_predict call. The prediction materialises intermediate arrays proportional to the batch, so this is the memory knob; lower it if a survey-scale run runs out of memory. Batching does not change the labels.

    - `keep_model` bool - Pass the fitted model through on the clustering_model output. Set false when this step is checkpointed and pickle is unwanted; the model is only used by the hierarchy panel of plot_clustering_report, which is skipped when no model reaches it.

    ```python
    # --- Parameters ---
    _label_all_points__dataset_name = 'ml_dataset'
    _label_all_points__normalization_name = 'normalized_flatten_mean_centered'

    _result = assign_clusters_by_prediction(
        ds_normalized=normalize_ml_ds_normalized,
        clustering_results=run_hdbscan_clustering_results,
        clustering_model=run_hdbscan_clustering_model,
        dataset_name=_label_all_points__dataset_name,
        normalization_name=_label_all_points__normalization_name,
    )
    label_all_points_clustering_results = _result['clustering_results']
    label_all_points_clustering_model = _result['clustering_model']
    label_all_points_cluster_labels = _result['cluster_labels']
    ```

    ##### Step: `embed_results`
    **Op:** `embed_clustering_results`

    Embed HDBSCAN clustering results into the normalized ML Dataset. Accepts a single result dict from run_hdbscan, or a collected list of result dicts when used with the collect pattern for ensemble runs. For a list, one cluster-label variable is embedded per collected result using deterministic names derived from ml_result_name.

    **Parameters:**
    - `dataset_name` str - ML dataset the labels are embedded into. Must match the dataset_name used by reshape_for_ml.

    - `ml_result_name` str - Name the embedded cluster labels are stored under. Normally the ml_result_name from run_hdbscan, so plot_clustering_report finds the same result. For a collected list of results this is the stem the per-result variable names are derived from.

    ```python
    # --- Parameters ---
    _embed_results__dataset_name = 'ml_dataset'
    _embed_results__ml_result_name = 'hdbscan_results'

    _result = embed_clustering_results(
        ds_normalized=normalize_ml_ds_normalized,
        clustering_results=label_all_points_clustering_results,
        dataset_name=_embed_results__dataset_name,
        ml_result_name=_embed_results__ml_result_name,
    )
    embed_results_ds_normalized = _result['ds_normalized']
    gridded_results = _result['gridded_results']
    ```

    ##### Step: `plot_clustering_report`
    **Op:** `plot_clustering_report`

    Render a full clustering report for one or more clustering results. Produces the clustering echogram, per-cluster statistics report, and the HDBSCAN hierarchy plot when the clustering result includes a model and hierarchy plotting is applicable. This is a sink step: it produces figures with no data output.

    **Parameters:**
    - `dataset_name` str - ML dataset to report on. Must match the dataset_name used by reshape_for_ml.

    - `ml_result_name` str - Clustering result to report on. Must match the ml_result_name used by embed_clustering_results, which is the name the labels were stored under in ds_normalized.

    - `plot_window` list - [min_depth, max_depth, ping_min, ping_max] for echogram display
    - `time_min` str - Start of the displayed window as a UTC timestamp, for example "2024-10-15T12:30". Resolved to the closest ping and used instead of the ping_min entry in plot_window. Omit to window by ping index.

    - `time_max` str - End of the displayed window as a UTC timestamp. Resolved to the closest ping and used instead of the ping_max entry in plot_window. Omit to window by ping index.

    - `x_axis_units` str - Units for the clustering echogram x-axis: 'datetime' (default, absolute UTC clock times), 'seconds' from the first ping shown, 'pings', 'bins', or 'meters'.

    - `y_axis_units` str - Units for the clustering echogram y-axis ('meters', 'range_sample', or 'bins')
    - `overlay_line_var` str - Name of a variable in ds_normalized to draw as a line overlay
    - `overlay_windows` list - Window dicts (as plan_dive_datasets emits) whose line files are drawn onto the echogram. Each window's lines are read onto its own pings, so every window is a separate segment and windows that overlap in time each keep their own line. Empty draws nothing.

    - `overlay_window_keys` list - Window keys naming the line files to draw. Defaults to the three dive profile keys dive_fit_evl, dive_u99_evl and dive_l99_evl. A window missing a key is skipped for that key.

    - `overlay_fit_style` dict - Matplotlib line keywords for the fitted dive profile drawn by overlay_windows, for example {color: "#23FFFF", linewidth: 3.0}. Merged over the built-in style, so naming only one key leaves the rest alone. Empty uses the built-in style: solid cyan.

    - `overlay_bound_style` dict - The same for both 99% confidence bound lines, for example {color: "#D08BFF", linestyle: ":"}. Empty uses the built-in style: dotted violet, thinner than the fitted profile. Both bounds always share one style, since they are two edges of one interval.

    - `normalization_name` str - Normalized feature set to plot statistics for when cluster_stats_sv_data_var is "ml_features" and no clustering_results input is wired. Ignored otherwise; with a result wired, its own normalization_name is used.

    - `cluster_colors` list - Hex color strings for each cluster label
    - `y_to_x_aspect_ratio_override` float - Override the echogram aspect ratio; null uses the default
    - `cluster_stats_sv_data_var` str - Sv variable name used when computing per-cluster statistics, or "ml_features" to plot the flattened ML features used for clustering
    - `cluster_stats_compute_pairwise_differences` bool - Compute inter-channel diffs in cluster statistics, taken against the second-lowest-frequency channel

    - `cluster_stats_dpi` int - DPI for the cluster statistics figure, which holds much smaller text than the echogram. Null falls back to dpi.

    - `save_image` bool - Save the rendered figures to image files; null uses runtime defaults
    - `save_formats` list - Image formats to save, chosen from png, svg, jpeg, jpg, and pdf
    - `save_dir` str - Optional directory override for saved image artifacts
    - `show` bool - Whether to display figures interactively or inline; null uses runtime defaults
    - `dpi` int - Optional DPI override for raster image outputs

    ```python
    # --- Parameters ---
    _plot_clustering_report__dataset_name = 'ml_dataset'
    _plot_clustering_report__ml_result_name = 'hdbscan_results'
    _plot_clustering_report__x_axis_units = 'bins'
    _plot_clustering_report__y_to_x_aspect_ratio_override = 1.78
    _plot_clustering_report__plot_window = [0, 2400]
    _plot_clustering_report__cluster_stats_sv_data_var = 'Sv'
    _plot_clustering_report__overlay_windows = windows

    plot_clustering_report(
        ds_normalized=embed_results_ds_normalized,
        clustering_results=run_hdbscan_clustering_results,
        clustering_model=run_hdbscan_clustering_model,
        ds_Sv=rechunk_dive_mvbs_ds_Sv,
        dataset_name=_plot_clustering_report__dataset_name,
        ml_result_name=_plot_clustering_report__ml_result_name,
        x_axis_units=_plot_clustering_report__x_axis_units,
        y_to_x_aspect_ratio_override=_plot_clustering_report__y_to_x_aspect_ratio_override,
        plot_window=_plot_clustering_report__plot_window,
        cluster_stats_sv_data_var=_plot_clustering_report__cluster_stats_sv_data_var,
        overlay_windows=_plot_clustering_report__overlay_windows,
        overlay_fit_style={'color': '#23FFFF', 'linewidth': 3.0},
        overlay_bound_style={'color': '#D08BFF', 'linewidth': 1.8, 'linestyle': ':'},
    )
    ```

    *End of included section: processing_lvl_4.yaml*

    #### Included: processing_lvl_5_sv_codes.yaml

    Steps: generate_sv_codes, sv_code_depth_table

    ##### Step: `generate_sv_codes`
    **Op:** `custom` (custom / unregistered)

    Write per-dive SVCode CSVs from the embedded clustering.

    **Parameters:**
    - `windows` list
    - `output_dir` path
    - `dataset_name` str
    - `ml_result_name` str
    - `range_var` str
    - `ping_time_bin` str

    ```python
    # --- Parameters ---
    _generate_sv_codes__windows = windows
    _generate_sv_codes__output_dir = './outputs/sv_codes'
    _generate_sv_codes__dataset_name = 'ml_dataset'
    _generate_sv_codes__ml_result_name = 'hdbscan_results'
    _generate_sv_codes__range_var = 'depth'
    _generate_sv_codes__ping_time_bin = '10s'

    _result = generate_sv_codes(
        ds=embed_results_ds_normalized,
        windows=_generate_sv_codes__windows,
        output_dir=_generate_sv_codes__output_dir,
        dataset_name=_generate_sv_codes__dataset_name,
        ml_result_name=_generate_sv_codes__ml_result_name,
        range_var=_generate_sv_codes__range_var,
        ping_time_bin=_generate_sv_codes__ping_time_bin,
    )
    code_csv_paths = _result['code_csv_paths']
    summary_csv_path = _result['summary_csv_path']
    dive_labels = _result['dive_labels']
    ```

    ##### Step: `sv_code_depth_table`
    **Op:** `custom` (custom / unregistered)

    Percent of dive duration per depth interval per code.

    **Parameters:**
    - `summary_csv_path` path
    - `output_dir` path
    - `depth_interval_m` float

    ```python
    # --- Parameters ---
    _sv_code_depth_table__summary_csv_path = summary_csv_path
    _sv_code_depth_table__output_dir = './outputs/sv_codes'
    _sv_code_depth_table__depth_interval_m = 200.0

    _result = sv_code_depth_table(
        summary_csv_path=_sv_code_depth_table__summary_csv_path,
        output_dir=_sv_code_depth_table__output_dir,
        depth_interval_m=_sv_code_depth_table__depth_interval_m,
    )
    per_dive_csv_path = _result['per_dive_csv_path']
    combined_csv_path = _result['combined_csv_path']
    totals_csv_path = _result['totals_csv_path']
    ```

    *End of included section: processing_lvl_5_sv_codes.yaml*
