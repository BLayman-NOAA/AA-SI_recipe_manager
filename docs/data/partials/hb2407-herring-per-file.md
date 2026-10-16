=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-herring-per-file" markdown>

    - [HB2407 level 0, file selection and calibration mapping](level-0.md)
    - [HB2407 level 1, raw ingest](level-1.md)
    - [HB2407 level 2, calibrated Sv, masks and noise removal](level-2.md)
    - `plot_sv_clean`: [`plot_sv_echogram`](../ops/plot_sv_echogram.md)
    - [HB2407 level 3, gridding and per-cell statistics](level-3.md)
    - `plot_mvbs`: [`plot_sv_echogram`](../ops/plot_sv_echogram.md)
    - [HB2407 level 4, clustering](level-4.md)

    </div>

=== "YAML"

    [Download `Herring_10-15_per_file.yaml`](../recipes/HB2407/Herring_10-15_per_file.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/
    # Herring_10-15_per_file.yaml, regrouped into the HB1603 levels. Every step is
    # wired as in the original, in the same order.

    # HB2407 Herring 10-15, processed one raw file at a time.
    #
    # Each raw file is calibrated, converted to Sv and masked on its own, then
    # merge_sv joins the survey back together before noise removal.
    #
    #   level 0   select the raw files and build the calibration mapping
    #   level 1   per file: raw ingest
    #   level 2   per file: Sv, depth and masks; then merge, crop and noise removal
    #   level 3   sparse mask, MVBS and per-cell statistics
    #   level 4   HDBSCAN clustering and its report
    #
    #   aa-recipe run Herring_10-15_per_file.yaml
    #   aa-recipe run Herring_10-15_per_file.yaml --executor dask

    recipe:
      name: "herring_10_15_per_file"
      description: "HB2407 Herring 10-15 pipeline with per-raw-file processing and a pre-MVBS fan-in."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        fingerprint_mode: auto
      cal_input_folder:
        type: path
        default: "./HB2407_calibration_files"
      seabed_line_folder:
        type: path
        default: "gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines"

      # Time window. Selects the raw files and seabed line files, and each selected
      # raw file runs as its own chain instance.
      file_time_start:
        type: str
        default: "2024-10-15T13:38:52"
      file_time_end:
        type: str
        default: "2024-10-15T23:43:36"

      # Display window shared by every plot.
      min_depth:
        type: float
        default: 0
      max_depth:
        type: float
      # Unset shows the whole time range. Set a UTC timestamp to window the plots.
      time_min:
        type: str
      time_max:
        type: str
      x_axis_units:
        type: str
        default: "datetime"
      y_axis_units:
        type: str
        default: "meters"


    steps:

      - include: sub_recipes/processing_lvl_0_calibration.yaml
        input_overrides:
          raw_input_folder: ${inputs.raw_input_folder}
          cal_input_folder: ${inputs.cal_input_folder}
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          cruise_id: "HB2407"
          record_author: "<Your Name>"

      - include: sub_recipes/processing_lvl_1_per_file.yaml
        input_overrides:
          sonar_model: "EK80"

      # Must follow level 1 directly: it continues the same mapped chain.
      - include: sub_recipes/processing_lvl_2_per_file.yaml
        input_overrides:
          echodata: ${combine_raw.echodata}
          calibration_dict: ${gen_cal_mapping.calibration_dict}
          mapping_dict: ${gen_cal_mapping.mapping_dict}
          # Transducer depth is each file's drop keel offset plus heave, shifted by
          # datum_correction_m to the waterline.
          datum_correction_m: -1.0
          use_heave: true
          heave_sign: 1.0
          surface_mask_depth: 5.0
          seafloor_buffer_m: 1
          # Crop the range by outlier rejection, so one very deep ping does not hold
          # it open. Add crop_max_range_m for a hard ceiling.
          crop_outlier_sigma: 3.0
          crop_outlier_margin: 1.5
          # Parameters for echopype's remove_background_noise.
          noise_ping_num: 5
          noise_range_sample_num: 10
          seabed_line_folder: ${inputs.seabed_line_folder}
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}

      - id: plot_sv_clean
        op: plot_sv_echogram
        regenerate: if-missing
        inputs:
          ds_Sv: ${remove_noise.ds_Sv}
        params:
          min_depth: ${inputs.min_depth}
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          dpi: 100

      - include: sub_recipes/processing_lvl_3.yaml
        input_overrides:
          sv_clean_unmasked: ${remove_noise.ds_Sv}
          # MVBS bin size.
          range_bin: "4m"
          ping_time_bin: "4s"
          nan_threshold: 0.95 # mask a bin when more than this fraction is NaN

      - id: plot_mvbs
        op: plot_sv_echogram
        regenerate: if-missing
        inputs:
          ds_Sv: ${compute_mvbs.ds_MVBS}
          ds_Sv_source: ${mask_sparse.ds_Sv}
        params:
          min_depth: ${inputs.min_depth}
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          dpi: 50

      # echodata and seafloor_depth stay unwired. They only feed the seafloor_depth
      # and altitude features, which this recipe does not use.
      - include: sub_recipes/processing_lvl_4.yaml
        input_overrides:
          mvbs: ${compute_mvbs.ds_MVBS}
          overlay_line_var: "sw_dive_profile"
          sv_clean_unmasked: ${remove_noise.ds_Sv}
          cell_stats_sv: ${compute_cell_stats.ds_Sv}
          min_depth: ${inputs.min_depth}
          max_depth: ${inputs.max_depth}
          time_min: ${inputs.time_min}
          time_max: ${inputs.time_max}
          x_axis_units: ${inputs.x_axis_units}
          y_axis_units: ${inputs.y_axis_units}
          # HDBSCAN tuning
          hdbscan_min_cluster_size_fraction: 0.001
          hdbscan_min_samples: 7
          hdbscan_cluster_selection_method: "eom"
    ```

=== "Notebook"

    [Download the notebook](../data/notebooks/hb2407-herring-per-file.ipynb), as `aa-recipe generate` writes it.

    ### herring_10_15_per_file

    HB2407 Herring 10-15 pipeline with per-raw-file processing and a pre-MVBS fan-in.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Utils.git
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_calibration.git
    # %pip install echopype>=0.6.0
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_ML.git
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Visualization.git
    ```

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import generate_standardized_cal_mapping
    from aa_si_utils.utils import read_raw_files_to_stores
    from aa_si_utils.utils import combine_raw_stores
    from aa_si_calibration.calibration import extract_standardized_calibration_parameters
    from echopype.calibrate import compute_Sv
    from aa_si_utils.utils import compute_transducer_depth
    from echopype.consolidate import add_depth
    from aa_si_utils.utils import read_seafloor_line_evl
    from aa_si_utils.utils import create_seafloor_mask
    from aa_si_utils.utils import create_surface_mask
    from aa_si_utils.utils import combine_masks
    from aa_si_utils.utils import apply_mask_to_sv
    from aa_si_utils.utils import concat_datasets
    from aa_si_utils.utils import crop_range_samples
    from aa_si_utils.utils import rechunk_dataset
    from aa_si_ml.ml import remove_noise
    from aa_si_visualization.echogram import plot_sv_echogram
    from aa_si_utils.utils import mask_sparse_bins
    from echopype.commongrid import compute_MVBS
    from aa_si_ml.ml import compute_per_cell_statistics
    from aa_si_ml.ml import reshape_data_for_ml
    from aa_si_ml.ml import add_auxiliary_features
    from aa_si_ml.ml import normalize_data
    from aa_si_visualization.echogram import plot_flattened_data_echogram
    from aa_si_ml.ml import run_hdbscan
    from aa_si_ml.ml import embed_clustering_results
    from aa_si_ml.ml import plot_clustering_report
    ```

    ```python
    raw_input_folder = None  # TODO: set this value  # path
    cal_input_folder = './HB2407_calibration_files'  # path
    seabed_line_folder = 'gs://ggn-nmfs-aa-prod-1-data/HDD/Henry_B_Bigelow/HB2407/Auxiliary/EV_seabedlines'  # path
    file_time_start = '2024-10-15T13:38:52'  # str
    file_time_end = '2024-10-15T23:43:36'  # str
    min_depth = 0  # float
    max_depth = None  # TODO: set this value  # float
    time_min = None  # TODO: set this value  # str
    time_max = None  # TODO: set this value  # str
    x_axis_units = 'datetime'  # str
    y_axis_units = 'meters'  # str
    calibration_outputs = 'calibration'  # str
    raw_file_names = []  # list
    ping_time_chunk = 10000  # int
    seabed_line_max_gap_s = 600  # float
    seabed_line_min_coverage = 0.99  # float
    crop_trailing_all_nan = True  # bool
    crop_max_range_m = None  # TODO: set this value  # float
    noise_chunk_ping_time = 1000  # int
    mvbs_chunk_ping_time = 1000  # int
    echodata = None  # TODO: set this value  # echodata
    seafloor_depth = None  # TODO: set this value  # dataarray
    ```

    #### Included: processing_lvl_0_calibration.yaml

    Steps: initial_setup, gen_cal_mapping

    ##### Step: `initial_setup`
    **Op:** `initial_setup`

    Validate the raw file set in the input directory, create calibration output directories if they do not exist, and return a list of raw file paths and the resolved calibration output directory ready for processing.

    **Parameters:**
    - `raw_input_folder` path - Folder containing .raw files. May be a local path or a remote fsspec URL (e.g. gs://bucket/survey/raw); remote folders are listed without downloading any data, and existence checks are skipped at validation time for remote URLs.

    - `calibration_outputs` str - Subdirectory name under the pipeline's user-facing outputs folder where calibration artifacts are written. Resolved automatically relative to the outputs directory when run via the executor; treated as a CWD-relative path when called standalone (e.g. in a notebook). Calibration JSON logs are always written to a logs/ subdirectory within this folder.
    - `raw_file_names` list - Explicit file name list; empty means include all files in folder
    - `clear_previous_json_logs` bool - Delete the calibration_flags.json left by a previous run before this one starts, so the flags reflect this run alone. Set false to let the flags accumulate across runs.

    - `file_time_start` str - Optional inclusive ISO datetime (e.g. "2016-07-25T20:58") restricting the raw file set to files whose filename-encoded time (D{YYYYMMDD}-T{HHMMSS}) is at or after this bound. Filenames without a parseable datetime are excluded when a bound is set. Applies to local and gs:// folders alike; filtering is name-based, so no data is read (or downloaded) to filter.
    - `file_time_end` str - Optional inclusive ISO datetime upper bound for filename-encoded file times. See file_time_start.

    ```python
    # --- Parameters ---
    _initial_setup__raw_input_folder = '${inputs.raw_input_folder}'
    _initial_setup__calibration_outputs = 'calibration'
    _initial_setup__raw_file_names = []
    _initial_setup__clear_previous_json_logs = True
    _initial_setup__file_time_start = '2024-10-15T13:38:52'
    _initial_setup__file_time_end = '2024-10-15T23:43:36'

    _result = initial_setup_and_validation(
        raw_input_folder=_initial_setup__raw_input_folder,
        calibration_outputs_string=_initial_setup__calibration_outputs,
        raw_file_names=_initial_setup__raw_file_names,
        clear_previous_json_logs=_initial_setup__clear_previous_json_logs,
        file_time_start=_initial_setup__file_time_start,
        file_time_end=_initial_setup__file_time_end,
    )
    raw_file_paths = _result['raw_file_paths']
    calibration_output_dir = _result['calibration_output_dir']
    ```

    ##### Step: `gen_cal_mapping`
    **Op:** `generate_standardized_cal_mapping`

    Read manufacturer calibration files, convert them to per-channel standardized files, match each raw channel to its calibration data, and return a mapping dict and a calibration dict.

    **Parameters:**
    - `raw_input_folder` path - Folder containing raw acoustic files; used to determine channel configuration. May be a local path or a remote fsspec URL (e.g. gs://bucket/survey/raw). Remote raw files are scanned one at a time: each is downloaded to local scratch, read for its channel config, and the local copy deleted before the next file is fetched.

    - `cal_input_folder` path - Folder containing manufacturer calibration files (.cal / .xml). May be a local path or a remote fsspec URL; a remote folder is downloaded to a local scratch directory for the duration of the parse (these files are small) and removed afterwards.

    - `output_base` path - Root directory the standardized calibration files, mapping files, and logs are written under. Normally wired to ${initial_setup.calibration_output_dir} so calibration artifacts land beside the run's other outputs. Subdirectories are created as needed.

    - `cruise_id` str - Cruise identifier recorded in every generated single-channel calibration file (e.g. "HB1603"), so a standardized file stays traceable to the survey it came from.

    - `record_author` str - Name recorded as the author of every generated single-channel calibration file. Part of the provenance the standardized format carries, not something the calculation uses.

    - `short_filenames` bool - Use compact names for the generated single-channel files and the keys in mapping_dict. Set false for fully descriptive names, which are easier to read and longer.

    - `keep_unused` bool - Retain standardized files not matched to any channel
    - `conflict_resolution` str - 'error' or 'interactive' when multiple cal files match a channel
    - `verbose` bool - Print progress information during mapping; implementation-specific
    - `file_time_start` str - Optional inclusive ISO datetime restricting the scanned raw files to those whose filename-encoded time (D{YYYYMMDD}-T{HHMMSS}) is at or after this bound. Should match the initial_setup window so the channel-configuration scan covers exactly the files being processed. For remote folders the filter is applied before any file is downloaded.
    - `file_time_end` str - Optional inclusive ISO datetime upper bound for filename-encoded file times. See file_time_start.

    ```python
    # --- Parameters ---
    _gen_cal_mapping__raw_input_folder = '${inputs.raw_input_folder}'
    _gen_cal_mapping__cal_input_folder = './HB2407_calibration_files'
    _gen_cal_mapping__output_base = calibration_output_dir
    _gen_cal_mapping__cruise_id = 'HB2407'
    _gen_cal_mapping__record_author = '<Your Name>'
    _gen_cal_mapping__short_filenames = True
    _gen_cal_mapping__keep_unused = True
    _gen_cal_mapping__conflict_resolution = 'error'
    _gen_cal_mapping__file_time_start = '2024-10-15T13:38:52'
    _gen_cal_mapping__file_time_end = '2024-10-15T23:43:36'

    _result = generate_standardized_cal_mapping(
        raw_input_folder=_gen_cal_mapping__raw_input_folder,
        cal_input_folder=_gen_cal_mapping__cal_input_folder,
        output_base=_gen_cal_mapping__output_base,
        cruise_id=_gen_cal_mapping__cruise_id,
        record_author=_gen_cal_mapping__record_author,
        short_filenames=_gen_cal_mapping__short_filenames,
        keep_unused=_gen_cal_mapping__keep_unused,
        conflict_resolution=_gen_cal_mapping__conflict_resolution,
        file_time_start=_gen_cal_mapping__file_time_start,
        file_time_end=_gen_cal_mapping__file_time_end,
    )
    mapping_dict = _result['mapping_dict']
    calibration_dict = _result['calibration_dict']
    ```

    *End of included section: processing_lvl_0_calibration.yaml*

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

    ##### Step: `detect_seafloor`
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

    ##### Step: `create_seafloor_mask`
    **Op:** `create_seafloor_mask`

    Build a seafloor mask from baseline Sv and detected seafloor depth. The seafloor depth is passed as an explicit input, enabling swappable detection methods without modifying masking logic.

    **Parameters:**
    - `seafloor_buffer_m` float (m) - Height above the detected seafloor line to also mask out. Widens the exclusion zone to cover detection error and the near-bottom dead zone, at the cost of discarding real backscatter just above the bottom.

    - `range_var` str - Name of the meter-valued variable on ds_Sv to compare against the seafloor line. Must use the same vertical reference as the seafloor line. When null (the default), auto-selects "depth" if present on ds_Sv (i.e., add_depth was run upstream), otherwise falls back to "echo_range".


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `create_surface_mask`
    **Op:** `create_surface_mask`

    Build a surface-interference mask from baseline Sv using a selectable method.

    **Parameters:**
    - `surface_depth_m` float (m) - Depth above which samples are masked out, removing the transmit pulse ringdown and near-surface bubble layer. The default of 0.0 masks nothing.


    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `combine_masks`
    **Op:** `combine_masks`

    Combine two or more mask datasets into one final mask. Accepts a list of mask inputs to support inserting additional custom mask-producing steps into the chain without changing core framework code.

    **Parameters:**
    - `mode` str - Combination mode: 'and' (all must be valid) or 'or' (any must be valid)

    *Runs once per `map_over` instance (fan-out loop).*

    ##### Step: `apply_mask`
    **Op:** `apply_sv_mask`

    Apply a boolean mask to an Sv Dataset, setting masked samples to NaN.
    Thin wrapper over echopype.mask.apply_mask, calling it against the Sv variable with NaN as the fill value. It adds nothing of its own, so the broadcasting and alignment rules are echopype's.

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

    *End of included section: processing_lvl_1_per_file.yaml*

    #### Included: processing_lvl_2_per_file.yaml

    Steps: extract_cal_params, compute_sv, compute_transducer_depth, ep_add_depth, detect_seafloor, create_seafloor_mask, create_surface_mask, combine_masks, apply_mask, merge_sv, crop_range, rechunk_before_noise, remove_noise

    ##### Step: `merge_sv`
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
    _merge_sv__dim = 'ping_time'

    merged = concat_datasets(datasets=apply_mask_ds_Sv, dim=_merge_sv__dim)
    ```

    ##### Step: `crop_range`
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

    ```python
    # --- Parameters ---
    _crop_range__outlier_sigma = 3.0
    _crop_range__outlier_margin = 1.5
    _crop_range__drop_trailing_all_nan = True
    _crop_range__max_range_m = crop_max_range_m

    crop_range_ds_Sv = crop_range_samples(
        ds_Sv=merged,
        outlier_sigma=_crop_range__outlier_sigma,
        outlier_margin=_crop_range__outlier_margin,
        drop_trailing_all_nan=_crop_range__drop_trailing_all_nan,
        max_range_m=_crop_range__max_range_m,
    )
    ```

    ##### Step: `rechunk_before_noise`
    **Op:** `rechunk_dataset`

    Rechunk a Dataset along ping_time. A standalone step rather than a parameter on any particular masking or reduction op, so a recipe can insert (or remove) chunking wherever a downstream op should run dask-parallel, independent of which step happens to run immediately before it.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk size along ping_time.

    ```python
    # --- Parameters ---
    _rechunk_before_noise__ping_time_chunk = 1000

    rechunk_before_noise_ds_Sv = rechunk_dataset(ds_Sv=crop_range_ds_Sv, ping_time_chunk=_rechunk_before_noise__ping_time_chunk)
    ```

    ##### Step: `remove_noise`
    **Op:** `remove_background_noise`

    Estimate and remove background noise from Sv using the De Robertis and Higginbottom method. Returns a cleaned Sv Dataset.
    Thin wrapper over echopype.clean.remove_background_noise. Every parameter below is forwarded to it unchanged, so its behavior is echopype's. The one thing the wrapper adds is that the corrected values are copied over the Sv variable, so downstream steps reading Sv get the cleaned data without having to know about Sv_corrected. Both variables are present on the result.

    **Parameters:**
    - `ping_num` int - Number of pings used to estimate the noise level
    - `range_sample_num` int - Number of range samples used to estimate the noise level
    - `background_noise_max` str - Upper limit on the estimated noise level (e.g. '-125dB'); omit to leave it unbounded
    - `SNR_threshold` str - Signal-to-noise ratio below which corrected samples are masked out (e.g. '3.0dB')

    ```python
    # --- Parameters ---
    _remove_noise__range_sample_num = 10
    _remove_noise__ping_num = 5

    remove_noise_ds_Sv = remove_noise(
        ds_Sv=rechunk_before_noise_ds_Sv,
        range_sample_num=_remove_noise__range_sample_num,
        ping_num=_remove_noise__ping_num,
    )
    ```

    *End of included section: processing_lvl_2_per_file.yaml*

    #### Step: `plot_sv_clean`
    **Op:** `plot_sv_echogram`

    Render an Sv echogram. Supports two call patterns: (1) single dataset, plots ds_Sv directly; (2) dual dataset, plots ds_Sv using coordinate axes from ds_Sv_source (useful for MVBS overlaid on the original Sv grid). This is a sink step: it produces a side-effect (figure) with no data output.

    **Parameters:**
    - `min_depth` float - Minimum depth (y-axis) in meters
    - `max_depth` float - Maximum depth (y-axis) in meters. Omit to use the deepest depth present in the data.

    - `ping_min` int - First ping index to display
    - `ping_max` int - Last ping index to display. Omit to display through the last ping.
    - `time_min` str - Start of the displayed window as a UTC timestamp, for example "2024-10-15T12:30". Resolved to the closest ping and used instead of ping_min. Omit to window by ping index.

    - `time_max` str - End of the displayed window as a UTC timestamp. Resolved to the closest ping and used instead of ping_max. Omit to window by ping index.

    - `x_axis_units` str - Units for the x-axis: 'datetime' (default, absolute UTC clock times), 'seconds' from the first ping shown, 'pings', 'meters' (distance travelled, derived from the GPS track), or 'bins' (MVBS input only).

    - `y_axis_units` str - Units for the y-axis: 'meters' (default), 'range_sample', or 'bins' (MVBS input only).

    - `panel_width_to_height` float - Force each frequency panel to be this many times wider than tall (for example 8 for a long strip). Omit for automatic sizing.

    - `overlay_lines` list - Optional list of line overlays. Each entry is a dict with 'var' (variable name in ds_Sv) and 'style' (dict of matplotlib kwargs).

    - `save_image` bool - Save the rendered figure to an image file; null uses runtime defaults
    - `save_formats` list - Image formats to save, chosen from png, svg, jpeg, jpg, and pdf
    - `save_dir` str - Optional directory override for saved image artifacts
    - `show` bool - Whether to display the figure interactively or inline; null uses runtime defaults
    - `dpi` int - Optional DPI override for raster image outputs

    ```python
    # --- Parameters ---
    _plot_sv_clean__min_depth = 0
    _plot_sv_clean__max_depth = max_depth
    _plot_sv_clean__time_min = time_min
    _plot_sv_clean__time_max = time_max
    _plot_sv_clean__x_axis_units = 'datetime'
    _plot_sv_clean__y_axis_units = 'meters'
    _plot_sv_clean__dpi = 100

    plot_sv_echogram(
        ds_Sv=remove_noise_ds_Sv,
        min_depth=_plot_sv_clean__min_depth,
        max_depth=_plot_sv_clean__max_depth,
        time_min=_plot_sv_clean__time_min,
        time_max=_plot_sv_clean__time_max,
        x_axis_units=_plot_sv_clean__x_axis_units,
        y_axis_units=_plot_sv_clean__y_axis_units,
        dpi=_plot_sv_clean__dpi,
    )
    ```

    #### Included: processing_lvl_3.yaml

    Steps: mask_sparse, rechunk_before_mvbs, compute_mvbs, compute_cell_stats

    ##### Step: `mask_sparse`
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

    ##### Step: `rechunk_before_mvbs`
    **Op:** `rechunk_dataset`

    Rechunk a Dataset along ping_time. A standalone step rather than a parameter on any particular masking or reduction op, so a recipe can insert (or remove) chunking wherever a downstream op should run dask-parallel, independent of which step happens to run immediately before it.

    **Parameters:**
    - `ping_time_chunk` int - Target chunk size along ping_time.

    ```python
    # --- Parameters ---
    _rechunk_before_mvbs__ping_time_chunk = 1000

    rechunk_before_mvbs_ds_Sv = rechunk_dataset(ds_Sv=mask_sparse_ds_Sv, ping_time_chunk=_rechunk_before_mvbs__ping_time_chunk)
    ```

    ##### Step: `compute_mvbs`
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

    ##### Step: `compute_cell_stats`
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

    *End of included section: processing_lvl_3.yaml*

    #### Step: `plot_mvbs`
    **Op:** `plot_sv_echogram`

    Render an Sv echogram. Supports two call patterns: (1) single dataset, plots ds_Sv directly; (2) dual dataset, plots ds_Sv using coordinate axes from ds_Sv_source (useful for MVBS overlaid on the original Sv grid). This is a sink step: it produces a side-effect (figure) with no data output.

    **Parameters:**
    - `min_depth` float - Minimum depth (y-axis) in meters
    - `max_depth` float - Maximum depth (y-axis) in meters. Omit to use the deepest depth present in the data.

    - `ping_min` int - First ping index to display
    - `ping_max` int - Last ping index to display. Omit to display through the last ping.
    - `time_min` str - Start of the displayed window as a UTC timestamp, for example "2024-10-15T12:30". Resolved to the closest ping and used instead of ping_min. Omit to window by ping index.

    - `time_max` str - End of the displayed window as a UTC timestamp. Resolved to the closest ping and used instead of ping_max. Omit to window by ping index.

    - `x_axis_units` str - Units for the x-axis: 'datetime' (default, absolute UTC clock times), 'seconds' from the first ping shown, 'pings', 'meters' (distance travelled, derived from the GPS track), or 'bins' (MVBS input only).

    - `y_axis_units` str - Units for the y-axis: 'meters' (default), 'range_sample', or 'bins' (MVBS input only).

    - `panel_width_to_height` float - Force each frequency panel to be this many times wider than tall (for example 8 for a long strip). Omit for automatic sizing.

    - `overlay_lines` list - Optional list of line overlays. Each entry is a dict with 'var' (variable name in ds_Sv) and 'style' (dict of matplotlib kwargs).

    - `save_image` bool - Save the rendered figure to an image file; null uses runtime defaults
    - `save_formats` list - Image formats to save, chosen from png, svg, jpeg, jpg, and pdf
    - `save_dir` str - Optional directory override for saved image artifacts
    - `show` bool - Whether to display the figure interactively or inline; null uses runtime defaults
    - `dpi` int - Optional DPI override for raster image outputs

    ```python
    # --- Parameters ---
    _plot_mvbs__min_depth = 0
    _plot_mvbs__max_depth = max_depth
    _plot_mvbs__time_min = time_min
    _plot_mvbs__time_max = time_max
    _plot_mvbs__x_axis_units = 'datetime'
    _plot_mvbs__y_axis_units = 'meters'
    _plot_mvbs__dpi = 50

    plot_sv_echogram(
        ds_Sv=ds_MVBS,
        ds_Sv_original=mask_sparse_ds_Sv,
        min_depth=_plot_mvbs__min_depth,
        max_depth=_plot_mvbs__max_depth,
        time_min=_plot_mvbs__time_min,
        time_max=_plot_mvbs__time_max,
        x_axis_units=_plot_mvbs__x_axis_units,
        y_axis_units=_plot_mvbs__y_axis_units,
        dpi=_plot_mvbs__dpi,
    )
    ```

    #### Included: processing_lvl_4.yaml

    Steps: reshape_ml, add_aux_features, normalize_ml, plot_normalized_ml, run_hdbscan, embed_results, plot_clustering_report

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
        ds_Sv=ds_MVBS,
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
        echodata=echodata,
        ds_sv=compute_cell_stats_ds_Sv,
        seafloor_depth=seafloor_depth,
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

    ##### Step: `plot_normalized_ml`
    **Op:** `plot_ml_echogram`

    Render a normalized ML feature echogram for inspection. This is a sink step: it produces a side-effect (figure) with no data output.

    **Parameters:**
    - `dataset_name` str - ML dataset to plot. Must match the dataset_name used by reshape_for_ml.

    - `normalization_name` str - Normalized feature set to plot. Must match the normalization_name used by normalize_ml_data.

    - `min_depth` float - Minimum depth (y-axis) in meters
    - `max_depth` float - Maximum depth (y-axis) in meters. Omit to use the deepest depth present in the data.

    - `ping_min` int - First ping index to display
    - `ping_max` int - Last ping index to display. Omit to display through the last ping.
    - `time_min` str - Start of the displayed window as a UTC timestamp, for example "2024-10-15T12:30". Resolved to the closest ping and used instead of ping_min. Omit to window by ping index.

    - `time_max` str - End of the displayed window as a UTC timestamp. Resolved to the closest ping and used instead of ping_max. Omit to window by ping index.

    - `x_axis_units` str - Units for the x-axis: 'datetime' (default, absolute UTC clock times), 'seconds' from the first ping shown, 'pings', 'meters' (distance travelled, derived from the GPS track), or 'bins' (MVBS-derived data only).

    - `y_axis_units` str - Units for the y-axis: 'meters' (default), 'range_sample', or 'bins' (MVBS-derived data only).

    - `save_image` bool - Save the rendered figure to an image file; null uses runtime defaults
    - `save_formats` list - Image formats to save, chosen from png, svg, jpeg, jpg, and pdf
    - `save_dir` str - Optional directory override for saved image artifacts
    - `show` bool - Whether to display the figure interactively or inline; null uses runtime defaults
    - `dpi` int - Optional DPI override for raster image outputs

    ```python
    # --- Parameters ---
    _plot_normalized_ml__dataset_name = 'ml_dataset'
    _plot_normalized_ml__normalization_name = 'normalized_flatten_mean_centered'
    _plot_normalized_ml__min_depth = 0
    _plot_normalized_ml__max_depth = max_depth
    _plot_normalized_ml__time_min = time_min
    _plot_normalized_ml__time_max = time_max
    _plot_normalized_ml__x_axis_units = 'datetime'
    _plot_normalized_ml__y_axis_units = 'meters'
    _plot_normalized_ml__dpi = 30

    plot_flattened_data_echogram(
        ds_ml=normalize_ml_ds_normalized,
        ds_Sv_original=remove_noise_ds_Sv,
        ml_dataset_name=_plot_normalized_ml__dataset_name,
        ml_specific_data_name=_plot_normalized_ml__normalization_name,
        min_depth=_plot_normalized_ml__min_depth,
        max_depth=_plot_normalized_ml__max_depth,
        time_min=_plot_normalized_ml__time_min,
        time_max=_plot_normalized_ml__time_max,
        x_axis_units=_plot_normalized_ml__x_axis_units,
        y_axis_units=_plot_normalized_ml__y_axis_units,
        dpi=_plot_normalized_ml__dpi,
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
    _run_hdbscan__min_cluster_size_fraction = 0.001
    _run_hdbscan__min_samples = 7
    _run_hdbscan__sample_size = 1000000
    _run_hdbscan__cluster_selection_method = 'eom'
    _run_hdbscan__use_hdbscan = True
    _run_hdbscan__soft_membership_threshold = 0.6

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
    clustering_results = _result['clustering_results']
    clustering_model = _result['clustering_model']
    background_label = _result['background_label']
    cluster_labels = _result['cluster_labels']
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
        clustering_results=clustering_results,
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
    _plot_clustering_report__plot_window = [0, '${inputs.max_depth}']
    _plot_clustering_report__time_min = time_min
    _plot_clustering_report__time_max = time_max
    _plot_clustering_report__x_axis_units = 'datetime'
    _plot_clustering_report__y_axis_units = 'meters'
    _plot_clustering_report__dpi = 50
    _plot_clustering_report__cluster_stats_dpi = 200
    _plot_clustering_report__overlay_line_var = 'sw_dive_profile'
    _plot_clustering_report__cluster_colors = ['#9D00FF', '#35E200', '#FF0000', '#2F00FF', '#FF8800', '#FFFB1C', '#FF44E9', '#00BE8BCA', '#00FFE5', '#3D8D00FF', '#9C67FFFF']
    _plot_clustering_report__cluster_stats_sv_data_var = 'Sv'

    plot_clustering_report(
        ds_normalized=embed_results_ds_normalized,
        clustering_results=clustering_results,
        clustering_model=clustering_model,
        ds_Sv=remove_noise_ds_Sv,
        dataset_name=_plot_clustering_report__dataset_name,
        ml_result_name=_plot_clustering_report__ml_result_name,
        plot_window=_plot_clustering_report__plot_window,
        time_min=_plot_clustering_report__time_min,
        time_max=_plot_clustering_report__time_max,
        x_axis_units=_plot_clustering_report__x_axis_units,
        y_axis_units=_plot_clustering_report__y_axis_units,
        dpi=_plot_clustering_report__dpi,
        cluster_stats_dpi=_plot_clustering_report__cluster_stats_dpi,
        overlay_line_var=_plot_clustering_report__overlay_line_var,
        cluster_colors=_plot_clustering_report__cluster_colors,
        cluster_stats_sv_data_var=_plot_clustering_report__cluster_stats_sv_data_var,
    )
    ```

    *End of included section: processing_lvl_4.yaml*
