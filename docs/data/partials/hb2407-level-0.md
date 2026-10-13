=== "Flow diagram"

    <div class="recipe-flow" data-recipe="hb2407-level-0" markdown>

    - `initial_setup`: [`initial_setup`](../ops/initial_setup.md)
    - `gen_cal_mapping`: [`generate_standardized_cal_mapping`](../ops/generate_standardized_cal_mapping.md)

    </div>

=== "YAML"

    [Download `processing_lvl_0_calibration.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_0_calibration.yaml)

    ```yaml
    # Documentation copy of the first two steps of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/
    # sub_recipes/processing_lvl_1_per_file.yaml, split out so HB2407 follows the same levels as HB1603.

    # Processing level 0: select the raw files and build the calibration mapping.
    #
    #   initial_setup -> gen_cal_mapping
    #
    # initial_setup lists the raw files in raw_input_folder inside the time window,
    # and that list is what every per-file step in levels 1 and 2 maps over.
    # gen_cal_mapping runs the whole calibration pipeline in one step, once for the
    # survey: it scans the raw files' channel settings, standardizes the
    # manufacturer calibration files (.xml for EK80), and matches every raw channel
    # to a calibration. mapping_dict is keyed per raw filename, and each
    # extract_cal_params instance in level 2 picks its own file's entry out of it.

    recipe:
      name: "processing_lvl_0_calibration"
      description: "Raw file selection and the survey's calibration mapping (gs:// inputs)."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        description: "Folder containing the raw acoustic files (local path or gs:// URL)."
      cal_input_folder:
        type: path
        description: "Folder containing manufacturer calibration files (local path or gs:// URL)."
      calibration_outputs:
        type: str
        default: "calibration"
        description: "Subdirectory name under the pipeline outputs folder for calibration artifacts."
      cruise_id:
        type: str
        description: "Cruise identifier used in calibration mapping."
      record_author:
        type: str
        default: "<Your Name>"
      raw_file_names:
        type: list
        default: []
        description: >
          Optional subset of filenames within raw_input_folder to use. Empty list
          means "use every .raw file in the folder".
      file_time_start:
        type: str
        required: false
        description: >
          Optional inclusive ISO datetime restricting processing to raw files whose
          filename-encoded time (D{YYYYMMDD}-T{HHMMSS}) is at or after this bound.
          Applied to both the processed file set and the calibration scan; leave
          unset to process every file. This bound also sets the fan-out width: one
          mapped instance per selected file.
      file_time_end:
        type: str
        required: false
        description: "Optional inclusive ISO datetime upper bound; see file_time_start."

    steps:

      - id: initial_setup
        op: initial_setup
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          calibration_outputs: ${inputs.calibration_outputs}
          raw_file_names: ${inputs.raw_file_names}
          clear_previous_json_logs: true
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}

      # Runs once for the whole survey, so the calibration files are parsed once.
      - id: gen_cal_mapping
        op: generate_standardized_cal_mapping
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          cal_input_folder: ${inputs.cal_input_folder}
          output_base: ${initial_setup.calibration_output_dir}
          cruise_id: ${inputs.cruise_id}
          record_author: ${inputs.record_author}
          short_filenames: true
          keep_unused: true
          conflict_resolution: "error"
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb). They use variables earlier levels define, so run them from the full notebook.

    #### Step: `initial_setup`
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

    #### Step: `gen_cal_mapping`
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
