=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-survey-echogram-pyramid" data-slug="hb1603-survey-echogram-pyramid" markdown>

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
    - `viewer_splitbeam_angle`: [`ep_add_splitbeam_angle`](../ops/ep_add_splitbeam_angle.md)
    - `viewer_crop`: [`crop_range`](../ops/crop_range.md)
    - `viewer_seabed_line`: [`phase_detect_seafloor`](../ops/phase_detect_seafloor.md)
    - `viewer_seabed_mask`: [`create_seafloor_mask`](../ops/create_seafloor_mask.md)
    - `viewer_surface_mask`: [`create_surface_mask`](../ops/create_surface_mask.md)
    - `viewer_frequency_mask`: [`create_frequency_mask`](../ops/create_frequency_mask.md)
    - `viewer_combine_masks`: [`combine_masks`](../ops/combine_masks.md)
    - `viewer_apply_mask`: [`apply_sv_mask`](../ops/apply_sv_mask.md)
    - `viewer_remove_noise`: [`remove_background_noise`](../ops/remove_background_noise.md)
    - `viewer_mask_sparse`: [`mask_sparse_bins`](../ops/mask_sparse_bins.md)
    - `viewer_file_mvbs`: [`compute_mvbs`](../ops/compute_mvbs.md)
    - `merge_viewer_mvbs`: [`merge_datasets`](../ops/merge_datasets.md)
    - `rechunk_viewer_mvbs`: [`rechunk_dataset`](../ops/rechunk_dataset.md)
    - `plan_segments`: [`plan_pyramid_segments`](../ops/plan_pyramid_segments.md)
    - `build_pyramid`: [`build_echogram_pyramid`](../ops/build_echogram_pyramid.md)
    - `survey_echogram_store`: [`merge_echogram_pyramids`](../ops/merge_echogram_pyramids.md)

    </div>

=== "YAML"

    [Download `survey_echogram_pyramid.yaml`](../recipes/HB1603/survey_echogram_pyramid.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/survey_echogram_pyramid.yaml

    # HB1603 survey echogram pyramid: the whole cruise as one EchogramGL store.
    #
    # Per-file MVBS on depth at 18 and 38 kHz, seabed masked, built into a
    # level-of-detail pyramid the WebGPU viewer opens. Levels 0 to 2a are the
    # survey's, so the per-file Sv loads from the survey cache.
    #
    #   aa-recipe run survey_echogram_pyramid.yaml --executor dask --dask-workers 16
    #
    #   level 0, 1, 2a     the survey's catalogue, calibration and per-file Sv
    #   level 2b (viewer)  per file: angles, crop, seabed line, masks, noise removal
    #   level 3 (viewer)   per file: MVBS on a pinned depth grid; fan in to one store
    #   pyramid            plan segments, build each, merge into the viewer store
    #
    # Needs aa-si-echogram-gl installed in the run environment.
    # Keep the survey levels in step with survey_sv.yaml, and put nothing that
    # isn't per-file between levels 1 and 3.
    # To try a few files first, narrow file_time_start and file_time_end. A
    # narrowed window is its own cache lineage, so run the full survey with the
    # defaults.
    # Add --cache-write-tier survey to share the store instead of keeping it in
    # your own tier.
    #
    # Sanity-check the shape before a long run:
    #   aa-recipe dry-run survey_echogram_pyramid.yaml --visualize

    recipe:
      name: "hb1603_survey_echogram_pyramid"
      description: "The whole HB1603 cruise as an EchogramGL pyramid: per-file MVBS on depth, 18 and 38 kHz, seabed masked."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:

      # Survey level. These must equal survey_sv.yaml's.
      file_time_start:
        type: str
        default: "2016-06-01T00:00:00"
        description: "Start of the catalogue query. The default is the whole cruise; narrow it only for a test."
      file_time_end:
        type: str
        default: "2016-09-01T00:00:00"
        description: "End of the catalogue query. See file_time_start."
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
        description: "Range ceiling of the shared survey Sv. Changing it recomputes the whole survey Sv."

      # Viewer level
      viewer_crop_max_range_m:
        type: float
        default: 3000.0
        description: "Range ceiling of the picture. Separate from crop_max_range_m, so the shared survey Sv keeps its 1800 m crop."
      viewer_grid_max_depth:
        type: str
        default: "3010m"
        description: >
          Deepest depth the level 3 bins cover. It must clear viewer_crop_max_range_m
          plus the transducer depth, or the deepest samples are dropped.

      # Every other input lives in the level file that uses it, and can still be
      # set from here or with --input.

    steps:

      # Survey levels: keep in step with survey_sv.yaml

      - include: sub_recipes/processing_lvl_0_user.yaml
        input_overrides:
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          cal_input_folder: ${inputs.cal_input_folder}
          calibration_output_dir: ${inputs.calibration_output_dir}

      - include: sub_recipes/processing_lvl_1_per_file.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}

      - include: sub_recipes/processing_lvl_2_survey.yaml
        input_overrides:
          per_file_source: ${query_ncei.raw_urls}
          calibration_dict: ${build_cal_mapping.calibration_dict}
          mapping_dict: ${build_cal_mapping.mapping_dict}
          crop_max_range_m: ${inputs.crop_max_range_m}

      # Viewer levels

      # The viewer crops for itself, deeper than the survey's 1800 m, without
      # touching the shared Sv.
      - include: sub_recipes/processing_lvl_2_viewer_seabed.yaml
        input_overrides:
          crop_max_range_m: ${inputs.viewer_crop_max_range_m}

      - include: sub_recipes/processing_lvl_3_viewer.yaml
        input_overrides:
          grid_max_depth: ${inputs.viewer_grid_max_depth}

      - include: sub_recipes/viewer_pyramid.yaml
        input_overrides:
          gridded_sv: ${rechunk_viewer_mvbs.ds_Sv}
    ```

=== "Notebook"

    [Download the notebook](../data/notebooks/hb1603-survey-echogram-pyramid.ipynb), as `aa-recipe generate` writes it.

    ### hb1603_survey_echogram_pyramid

    The whole HB1603 cruise as an EchogramGL pyramid: per-file MVBS on depth, 18 and 38 kHz, seabed masked.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Utils.git
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_calibration.git
    # %pip install echopype>=0.6.0
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_ML.git
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_EchogramGL.git
    ```

    ```python
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
    from echopype.consolidate import add_splitbeam_angle
    from aa_si_utils.seabed import detect_seafloor_phase
    from aa_si_utils.utils import create_seafloor_mask
    from aa_si_utils.utils import create_surface_mask
    from aa_si_utils.utils import create_frequency_mask
    from aa_si_utils.utils import combine_masks
    from aa_si_utils.utils import apply_mask_to_sv
    from aa_si_ml.ml import remove_noise
    from aa_si_utils.utils import mask_sparse_bins
    from echopype.commongrid import compute_MVBS
    from aa_si_utils.utils import concat_datasets
    from aa_si_utils.utils import rechunk_dataset
    from aa_si_echogram_gl.ops import plan_pyramid_segments
    from aa_si_echogram_gl.ops import build_echogram_pyramid
    from aa_si_echogram_gl.ops import merge_echogram_pyramids
    ```

    ```python
    file_time_start = '2016-06-01T00:00:00'  # str
    file_time_end = '2016-09-01T00:00:00'  # str
    cal_input_folder = '../calibration_files/HB201607_cal'  # path
    calibration_output_dir = './outputs/calibration'  # path
    crop_max_range_m = 1800.0  # float
    viewer_crop_max_range_m = 3000.0  # float
    viewer_grid_max_depth = '3010m'  # str
    cruise_name = 'HB1603'  # str
    cruise_id = 'HB1603'  # str
    record_author = '<Your Name>'  # str
    max_scan_bytes = 1048576  # int
    match_tolerances = {'transmit_power': 1001.0}  # dict
    sonar_model = 'EK60'  # str
    seabed_channel = '38'  # str
    seabed_max_slope_deg = 30.0  # float
    seabed_max_gap_s = 600.0  # float
    seafloor_buffer_m = 10.0  # float
    seabed_line = 'integration'  # str
    surface_depth_m = 10.0  # float
    frequencies_to_mask = [70, 120, 200]  # list
    noise_range_sample_num = 10  # int
    noise_ping_num = 5  # int
    noise_snr_threshold = '3.0dB'  # str
    noise_background_max = None  # TODO: set this value  # str
    range_bin = '2m'  # str
    ping_time_bin = '10s'  # str
    range_var = 'depth'  # str
    nan_threshold = 0.99  # float
    viewer_chunk_ping_time = 2048  # int
    pyramid_range_var = 'depth'  # str
    pyramid_levels = 8  # int
    pyramid_segment_columns = 16384  # int
    pyramid_aggregation = 'linear_mean'  # str
    ```

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

    ##### Step: `viewer_splitbeam_angle`
    **Op:** `ep_add_splitbeam_angle`

    Add split-beam alongship and athwartship angle variables to an Sv Dataset. This step is echopype.consolidate.add_splitbeam_angle called directly, so the angle computation and its parameters are echopype's. The result preserves the input Sv data and adds ``angle_alongship`` and ``angle_athwartship`` on ``(channel, ping_time, range_sample)`` when the source EchoData contains the required split-beam angle data.
    This step is required before echopype's Blackwell seafloor detector because that detector thresholds the per-sample split-beam angle fields. Calibration parameters such as ``angle_offset_alongship`` are used by echopype to compute these variables, but they are not themselves the angle fields consumed by the detector.

    **Parameters:**
    - `waveform_mode` str - Transmit waveform mode. Use ``CW`` for narrowband EK60/EK80 power/angle data, or ``BB`` for broadband complex samples.

    - `encode_mode` str - Return echo encoding mode. Use ``power`` for EK60/EK80 power/angle data, or ``complex`` for complex samples.

    - `pulse_compression` bool - Whether to pulse-compress broadband complex samples. Only valid for ``waveform_mode: BB`` and ``encode_mode: complex``.

    - `to_disk` bool - Write split-beam angles back to disk when ``ds_Sv`` is a path. Keep false for in-memory recipe execution.

    - `drop_last_hanning_zero` bool - Echopype compatibility option for broadband pulse compression. Usually leave false.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_crop`
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

    ##### Step: `viewer_seabed_line`
    **Op:** `phase_detect_seafloor`

    Detect the seafloor with a phase-aware dynamic-programming line picker and return a 1-D (ping_time,) DataArray of the seabed echo's leading edge in metres. On the primary channel the detector finds phase-stable, loud seed regions, scores every sample inside a search window on Sv, local phase stability, echo asymmetry, SNR and the leading-edge energy gradient (Mode 1 of the v4 design; ``mode: 2`` adds the below-versus-above Sv contrast, below-region stationarity and below-region phase activity, for when dense near-bottom aggregations are confused with the seabed), then runs a Viterbi pass over the top score maxima per ping with a Huber transition whose break-point scales with the distance the vessel travelled between pings. The pick is walked back to the -10 dB point of the echo. A forward-backward pass gives a confidence and margin per ping.
    Drop-in swap with the ``detect_seafloor``, ``ep_detect_seafloor``, ``read_seafloor_line`` and ``hdbscan_detect_seafloor`` ops: the output port name (``seafloor_depth``) and shape (1-D, ping_time) match, so ``create_seafloor_mask`` works unchanged. By default the line is the leading edge, not an integration line; put the dead-zone offset in ``create_seafloor_mask``'s ``seafloor_buffer_m``. ``line: integration`` returns the Ona-Mitson backstepped line with the slope correction instead, which on the HB1603 canyon walls clears the up-slope echo that a 10 m buffer above the leading edge left in the 18 kHz channel. Every intermediate is available through ``diagnostics_path``.
    The op is self-contained: with no window or prior given it first runs a fast slab estimator (Mode 0b) over the whole recorded range, which averages a few dozen slabs of pings, scores them and joins them with the same line search, adding slabs where the seabed moves; its line and uncertainty become the per-ping search window for the full detector. ``r_min`` / ``r_max`` bound that search and ``z_prior`` replaces it.
    Split-beam angles come from ``ds_Sv`` when ``ep_add_splitbeam_angle`` ran upstream. Without them the detector runs amplitude-only with a warning: seeds are picked on Sv and extent alone, so a dense aggregation can be taken for the seabed. Pings where no candidate clears ``min_score`` are bridged by the line search and interpolated up to ``max_gap_s``; longer runs and the ends of the file stay NaN, which ``create_seafloor_mask`` masks entirely. Aliased-seabed detection is off by default: it is single-channel only and on HB1603 the water-column angle noise alone tripped it and the mask covered the true seabed.
    Measured 2026-09-24 on one file each: HB1603 38 kHz at 1750 m (EK60, 8 s pinging) followed the seabed through a 100 m step with a median 8 m offset above the .bot line, which itself sits below the Sv rise; HB2407 18 kHz at 57 m (EK80) sat 2 m below the Echoview line with no gross errors. Both take a few seconds per file after Sv is computed.

    **Parameters:**
    - `channel` str - Primary channel as its label, or its frequency in Hz or kHz given as a string (e.g. "38"). Null picks the lowest frequency channel whose seeds span at least a fifth of the pings, falling back to the lowest frequency with a warning.

    - `r_min` float (m) - Lower bound of the search window on the dataset's range reference. Default 10 m past the first sample, which skips the transmit pulse.

    - `r_max` float (m) - Upper bound of the search window. Default the end of the channel's range. Set both bounds on deep surveys: the crop they define is what keeps a 15000-sample ping affordable.

    - `sigma_prior_m` float (m) - Uncertainty of z_prior; default 2 % of z_prior. Ignored without it.

    - `regime` str - Depth regime preset: "shelf" (< 200 m), "slope" (200 to 1000 m), "deep" (> 1000 m) or "auto" from the search window midpoint. Sets the SNR reference, the seed Sv floor and the Mode 2 shape window.

    - `mode` int - Scoring mode. 1 scores the six point features; 2 adds the three local shape features (Stage 3b), which cost a little more and help where a school sits on the bottom. Both are deterministic.

    - `line` str - "leading_edge" (default) or "integration": which line to return on the seafloor_depth port. See the description.

    - `prior` str - "auto" (default) runs the Mode 0 slab estimator when no z_prior is given and uses it as the search window; "none" searches only the fixed r_min / r_max window.

    - `missing` str - What a ping with no seabed found gets after gap filling. "nan" (default) leaves it NaN and create_seafloor_mask masks the ping entirely, which an analysis wants. "max_range" sets it to the last recorded range so the ping is kept whole, which a survey picture wants where the seabed is sometimes below the recording. ping_coverage still counts detections.

    - `min_seabed_sv_db` float (dB) - A pick whose mean Sv over the pulse length below it is under this is rejected as not seabed, both in the prior's slabs and in the final line. The scores are relative to each ping, so without a floor a file whose seabed lies beyond the recorded range picks its loudest scattering layer instead (on HB1603, a -57 dB layer just past the near field). Seabed echoes on HB1603 and HB2407 run -13 to -40 dB. Null disables the check.

    - `prior_budget` int - Slab budget for the Mode 0 estimator. Each slab reads a few pings across the range; on a zarr store chunked along ping_time a slab costs one chunk read.

    - `n_candidates` int - Score maxima kept per ping for the line search.
    - `max_slope_deg` float (deg) - Maximum plausible seabed slope. With the distance travelled between pings it sets the depth change the transition cost treats as ordinary; larger jumps are tolerated at linear cost. Use 45 on rocky or canyon terrain.

    - `alpha` float - Transition strength multiplier in [0.5, 2].
    - `beta_fraction` float - Shallow-first tie-break prior as a fraction of the candidate score spread; 0 disables it. Targets ghost bottoms and the echo tail.

    - `min_score` float - Candidates below this score are dropped; a ping with none is skipped and bridged. Scores are sums of tanh terms, so 0 means neutral.

    - `walkback_db` float (dB) - Drop below the echo peak that marks the leading edge.
    - `effective_beamwidth_deg` float (deg) - Effective beamwidth for the backstepped integration line in the diagnostics; default nominal times 12/7 (the ES38B ratio).

    - `vessel_speed_m_s` float (m/s) - Speed used for along-track distance when ds_Sv carries no latitude and longitude. Without either, the break-point falls to one pulse length per ping, which suits a station but over-smooths a transect.

    - `min_confidence` float - Pings whose posterior confidence is below this are flagged.
    - `phase_weight` float - Weight of the two phase features. Raise to 1.5 where fish aggregations are the main confuser.

    - `max_gap_s` float (s) - Interior runs of skipped pings up to this long are interpolated; longer runs stay NaN. Null interpolates nothing.

    - `transition` str - "huber" or "truncated" (quadratic capped at the break-point, for cliffs).
    - `pulse_length_s` float (s) - Pulse duration when ds_Sv has no tau_effective.
    - `beamwidth_deg` float (deg) - Nominal beamwidth when ds_Sv has none.
    - `detect_aliases` bool - Run the single-channel Blackwell aliased-seabed detector and exclude its mask. See the description before enabling it.

    - `phase_threshold_deg2` float (deg2) - Seed phase-stability threshold on the summed angle variance within one pulse length; default (beamwidth / 2)^2.

    - `confidence_tolerance_pulses` float - Alternatives within this many pulse lengths of the pick (plus the beam-geometry spread, or inside the same loud run) count as the same pick when computing confidence and margin.

    - `diagnostics_path` str - Optional zarr path for the full diagnostics: masks, features, score, candidates, raw pick, leading edge, backstepped integration line, confidence, margin and flags on the cropped grid. Local runs only; the path is written as given.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_seabed_mask`
    **Op:** `create_seafloor_mask`

    Build a seafloor mask from baseline Sv and detected seafloor depth. The seafloor depth is passed as an explicit input, enabling swappable detection methods without modifying masking logic.

    **Parameters:**
    - `seafloor_buffer_m` float (m) - Height above the detected seafloor line to also mask out. Widens the exclusion zone to cover detection error and the near-bottom dead zone, at the cost of discarding real backscatter just above the bottom.

    - `range_var` str - Name of the meter-valued variable on ds_Sv to compare against the seafloor line. Must use the same vertical reference as the seafloor line. When null (the default), auto-selects "depth" if present on ds_Sv (i.e., add_depth was run upstream), otherwise falls back to "echo_range".


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_surface_mask`
    **Op:** `create_surface_mask`

    Build a surface-interference mask from baseline Sv using a selectable method.

    **Parameters:**
    - `surface_depth_m` float (m) - Depth above which samples are masked out, removing the transmit pulse ringdown and near-surface bubble layer. The default of 0.0 masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_frequency_mask`
    **Op:** `create_frequency_mask`

    Build a mask that excludes selected frequency channels. Implementation can be swapped to use different exclusion criteria.

    **Parameters:**
    - `frequencies_to_mask` list (kHz) - Nominal channel frequencies to exclude, as numbers in kHz (e.g. [70, 120, 200]). Every sample on a listed channel is marked invalid and every other channel is left untouched. The default empty list masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_combine_masks`
    **Op:** `combine_masks`

    Combine two or more mask datasets into one final mask. Accepts a list of mask inputs to support inserting additional custom mask-producing steps into the chain without changing core framework code.

    **Parameters:**
    - `mode` str - Combination mode: 'and' (all must be valid) or 'or' (any must be valid)

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_apply_mask`
    **Op:** `apply_sv_mask`

    Apply a boolean mask to an Sv Dataset, setting masked samples to NaN.
    Thin wrapper over echopype.mask.apply_mask, calling it against the Sv variable with NaN as the fill value. It adds nothing of its own, so the broadcasting and alignment rules are echopype's.

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_remove_noise`
    **Op:** `remove_background_noise`

    Estimate and remove background noise from Sv using the De Robertis and Higginbottom method. Returns a cleaned Sv Dataset.
    Thin wrapper over echopype.clean.remove_background_noise. Every parameter below is forwarded to it unchanged, so its behavior is echopype's. The one thing the wrapper adds is that the corrected values are copied over the Sv variable, so downstream steps reading Sv get the cleaned data without having to know about Sv_corrected. Both variables are present on the result.

    **Parameters:**
    - `ping_num` int - Number of pings used to estimate the noise level
    - `range_sample_num` int - Number of range samples used to estimate the noise level
    - `background_noise_max` str - Upper limit on the estimated noise level (e.g. '-125dB'); omit to leave it unbounded
    - `SNR_threshold` str - Signal-to-noise ratio below which corrected samples are masked out (e.g. '3.0dB')

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_mask_sparse`
    **Op:** `mask_sparse_bins`

    Remove spatiotemporal bins with fewer valid samples than a threshold, reducing noise in MVBS computation.

    **Parameters:**
    - `range_bin` str - Range bin size (e.g. '2m')
    - `ping_time_bin` str - Ping-time bin size (e.g. '10s')
    - `nan_threshold` float - Fraction of NaN values above which a bin is masked (0.0 to 1.0)
    - `range_var` str - Meter-valued variable to bin along, which must match the value given to compute_mvbs and compute_per_cell_statistics or the sparse mask lands on a different grid than the one it is meant to clean. 'echo_range' measures from the transducer face, 'depth' from the surface and needs the dataset to carry a depth variable. The bin edges are read from the first ping of the first channel, so a range variable that varies from ping to ping, as depth does under platform vertical offsets, is approximated by its first ping.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `viewer_file_mvbs`
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
    viewer_splitbeam_angle_ds_Sv = []
    viewer_crop_ds_Sv = []
    seafloor_depth = []
    viewer_seabed_mask_mask = []
    viewer_surface_mask_mask = []
    viewer_frequency_mask_mask = []
    viewer_combine_masks_mask = []
    viewer_apply_mask_ds_Sv = []
    viewer_remove_noise_ds_Sv = []
    viewer_mask_sparse_ds_Sv = []
    ds_MVBS = []
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
        _iter_viewer_splitbeam_angle_ds_Sv = add_splitbeam_angle(
        source_Sv=_iter_ep_add_depth_ds_Sv,
        echodata=_iter_echodata,
        waveform_mode='CW',
        encode_mode='power',
        to_disk=False,
    )
        viewer_splitbeam_angle_ds_Sv.append(_iter_viewer_splitbeam_angle_ds_Sv)
        _iter_viewer_crop_ds_Sv = crop_range_samples(
        ds_Sv=_iter_viewer_splitbeam_angle_ds_Sv,
        max_range_m=3000.0,
        drop_trailing_all_nan=False,
        outlier_sigma=None,
    )
        viewer_crop_ds_Sv.append(_iter_viewer_crop_ds_Sv)
        _iter_seafloor_depth = detect_seafloor_phase(
        ds_Sv=_iter_viewer_crop_ds_Sv,
        channel='38',
        max_slope_deg=30.0,
        max_gap_s=600.0,
        line='integration',
        missing='max_range',
    )
        seafloor_depth.append(_iter_seafloor_depth)
        _iter_viewer_seabed_mask_mask = create_seafloor_mask(
        ds_Sv=_iter_viewer_crop_ds_Sv,
        seafloor_depth=_iter_seafloor_depth,
        seafloor_buffer_m=10.0,
    )
        viewer_seabed_mask_mask.append(_iter_viewer_seabed_mask_mask)
        _iter_viewer_surface_mask_mask = create_surface_mask(ds_Sv=_iter_viewer_crop_ds_Sv, surface_depth_m=10.0)
        viewer_surface_mask_mask.append(_iter_viewer_surface_mask_mask)
        _iter_viewer_frequency_mask_mask = create_frequency_mask(ds_Sv=_iter_viewer_crop_ds_Sv, frequencies_to_mask=[70, 120, 200])
        viewer_frequency_mask_mask.append(_iter_viewer_frequency_mask_mask)
        _iter_viewer_combine_masks_mask = combine_masks(masks=[_iter_viewer_surface_mask_mask, _iter_viewer_frequency_mask_mask, _iter_viewer_seabed_mask_mask], mode='and')
        viewer_combine_masks_mask.append(_iter_viewer_combine_masks_mask)
        _iter_viewer_apply_mask_ds_Sv = apply_mask_to_sv(ds_Sv=_iter_viewer_crop_ds_Sv, mask=_iter_viewer_combine_masks_mask)
        viewer_apply_mask_ds_Sv.append(_iter_viewer_apply_mask_ds_Sv)
        _iter_viewer_remove_noise_ds_Sv = remove_noise(
        ds_Sv=_iter_viewer_apply_mask_ds_Sv,
        range_sample_num=10,
        ping_num=5,
        SNR_threshold='3.0dB',
        background_noise_max=noise_background_max,
    )
        viewer_remove_noise_ds_Sv.append(_iter_viewer_remove_noise_ds_Sv)
        _iter_viewer_mask_sparse_ds_Sv = mask_sparse_bins(
        ds_Sv=_iter_viewer_remove_noise_ds_Sv,
        range_bin='2m',
        ping_time_bin='10s',
        range_var='depth',
        nan_threshold=0.99,
    )
        viewer_mask_sparse_ds_Sv.append(_iter_viewer_mask_sparse_ds_Sv)
        _iter_ds_MVBS = compute_MVBS(
        ds_Sv=_iter_viewer_mask_sparse_ds_Sv,
        range_bin='2m',
        ping_time_bin='10s',
        range_var='depth',
        range_var_max='3010m',
    )
        ds_MVBS.append(_iter_ds_MVBS)
    ```

    *End of included section: processing_lvl_1_per_file.yaml*

    #### Included: processing_lvl_2_survey.yaml

    Steps: extract_cal_params, compute_sv, compute_transducer_depth, ep_add_depth, crop_survey_range

    *End of included section: processing_lvl_2_survey.yaml*

    #### Included: processing_lvl_2_viewer_seabed.yaml

    Steps: viewer_splitbeam_angle, viewer_crop, viewer_seabed_line, viewer_seabed_mask, viewer_surface_mask, viewer_frequency_mask, viewer_combine_masks, viewer_apply_mask, viewer_remove_noise

    *End of included section: processing_lvl_2_viewer_seabed.yaml*

    #### Included: processing_lvl_3_viewer.yaml

    Steps: viewer_mask_sparse, viewer_file_mvbs, merge_viewer_mvbs, rechunk_viewer_mvbs

    ##### Step: `merge_viewer_mvbs`
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
    _merge_viewer_mvbs__dim = 'ping_time'
    _merge_viewer_mvbs__on_duplicate = 'mean'

    merged = concat_datasets(
        datasets=ds_MVBS,
        dim=_merge_viewer_mvbs__dim,
        on_duplicate=_merge_viewer_mvbs__on_duplicate,
    )
    ```

    ##### Step: `rechunk_viewer_mvbs`
    **Op:** `rechunk_dataset`

    Rechunk a Dataset along ping_time. A standalone step rather than a parameter on any particular masking or reduction op, so a recipe can insert (or remove) chunking wherever a downstream op should run dask-parallel, independent of which step happens to run immediately before it.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk size along ping_time.

    ```python
    # --- Parameters ---
    _rechunk_viewer_mvbs__ping_time_chunk = 2048

    rechunk_viewer_mvbs_ds_Sv = rechunk_dataset(ds_Sv=merged, ping_time_chunk=_rechunk_viewer_mvbs__ping_time_chunk)
    ```

    *End of included section: processing_lvl_3_viewer.yaml*

    #### Included: viewer_pyramid.yaml

    Steps: plan_segments, build_pyramid, survey_echogram_store

    ##### Step: `plan_segments`
    **Op:** `plan_pyramid_segments`

    Choose ping ranges that a pyramid can be built over independently and then concatenated exactly.
    A pyramid is a reduction along the ping axis, so it splits along that axis, but only if the cuts fall on the reduction's own cell boundaries. Level k merges 2**k pings, so every range but the last is a multiple of 2**(levels-1) and reduces to a whole number of cells at every level. The last range is whatever is left: its final cell is short, which is exactly what a serial build produces at the end of a survey, so the segmented result is not an approximation of the serial one but the same answer.

    **Parameters:**
    - `levels` int - Levels the build will produce, which is what sets the grid.
    - `target_pings` int - Rough segment size, rounded down onto the grid. Sized for the memory one mapped instance needs: a segment is held whole while its levels are reduced, so at five channels and an uncropped water column 32,768 pings is roughly a gigabyte at level zero and twice that through the cascade. Raise it for a wide machine, lower it for a deep water column.

    ```python
    # --- Parameters ---
    _plan_segments__levels = 8
    _plan_segments__target_pings = 16384

    _result = plan_pyramid_segments(
        ds_Sv=rechunk_viewer_mvbs_ds_Sv,
        levels=_plan_segments__levels,
        target_pings=_plan_segments__target_pings,
    )
    ranges = _result['ranges']
    grid = _result['grid']
    ```

    ##### Step: `build_pyramid`
    **Op:** `build_echogram_pyramid`

    Build a level-of-detail pyramid from an Sv Dataset for the WebGPU echogram viewer.
    The output is a DataTree with one node per level, level 0 being the data as given and level k merging 2**k pings into one by averaging in linear space. Checkpoint the step and the store lands in the recipe or survey cache under the step's content address, where `aa-echogram serve --store <path>` can read it directly. Nothing has to be published for a pyramid to be looked at.
    `ping_range` is what makes this parallel. Passing one builds a single segment of the survey, so the op can be mapped over the ranges from plan_pyramid_segments and the results collected by merge_echogram_pyramids. The slice is taken inside the op, so a mapped instance reads only its own pings rather than the whole survey.

    **Parameters:**
    - `levels` int - Levels to build, halving the ping axis each time. Eight levels reduce the ping axis by 128 at the coarsest, which is what lets an overview of a survey read a small array.

    - `range_var` str - Vertical coordinate to build the geometry from. echo_range rather than depth: depth carries heave, and averaging sample index i across pings that heaved averages different depths, which the pyramid's alignment check refuses. The surface reference is not lost, because the sidecar keeps depth minus echo_range as the per ping transducer depth.

    - `ping_range` list - Optional [start, stop] naming one segment of the ping axis. Supplied by a mapped step as ${_item} from plan_pyramid_segments.

    - `aggregation` str - How pings are merged: 'linear_mean' averages in linear space and reports decibels, 'max' keeps the loudest sample. Mean is right for biomass, max preserves a single strong target that a mean would dilute.


    *Runs once per `map_over` instance (fan-out loop).*

    ```python
    build_pyramid_pyramid = []
    for _item in ranges:
        _iter_build_pyramid_pyramid = build_echogram_pyramid(
        ds_Sv=rechunk_viewer_mvbs_ds_Sv,
        ping_range=_item,
        levels=8,
        range_var='depth',
        aggregation='linear_mean',
    )
        build_pyramid_pyramid.append(_iter_build_pyramid_pyramid)
    ```

    ##### Step: `survey_echogram_store`
    **Op:** `merge_echogram_pyramids`

    Collect per segment pyramids into one.
    The fan-in for a segment-parallel pyramid build. Level k of the survey is level k of each part laid end to end, so this concatenates per level rather than rebuilding, and adds no levels: each part already carries every level. The per chunk summaries are recomputed, because a part's chunk grid is not the whole survey's and its keys cannot be shifted into place.
    Only correct when the parts were cut by plan_pyramid_segments. A split off the grid puts a partial cell in the middle of the survey and every coarse level after it is wrong.

    **Parameters:**
    - `dim` str - Ping dimension to concatenate along.

    ```python
    # --- Parameters ---
    _survey_echogram_store__dim = 'ping_time'

    survey_echogram_store_pyramid = merge_echogram_pyramids(parts=build_pyramid_pyramid, dim=_survey_echogram_store__dim)
    ```

    *End of included section: viewer_pyramid.yaml*
