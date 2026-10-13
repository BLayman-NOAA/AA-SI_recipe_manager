=== "Flow diagram"

    <div class="recipe-flow" data-recipe="calibration-fused" markdown>

    - `initial_setup`: [`initial_setup`](../ops/initial_setup.md)
    - `gen_cal_mapping`: [`generate_standardized_cal_mapping`](../ops/generate_standardized_cal_mapping.md)

    </div>

=== "YAML"

    [Download `calibration_pipeline_fused.yaml`](../recipes/calibration/calibration_pipeline_fused.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/calibration_pipeline.yaml

    # HB2407 Calibration Only - Standardize Calibration Files and Build the Mapping File
    #
    #   initial_setup           resolve the raw file set and the calibration
    #                           output directory under outputs/
    #   gen_cal_mapping         parse the .cal / .xml files, write one standardized
    #                           single-channel file per channel, match each raw
    #                           channel to its calibration data, write the mapping
    #
    # Artifacts land under <outputs_dir>/<calibration_outputs>/:
    #
    #   raw_file_configs/                  raw_file_configs.yaml (channel config
    #                                      scanned out of the raw files)
    #   single_channel_calibration_files/  one standardized .yaml per channel <- REQUIRED BY NCEI
    #   mapping_files/                     channel_mapping.yaml, <- REQUIRED BY NCEI
    #                                      calibration_configurations.yaml
    #   unused_calibration_files/          cal files no raw channel matched
    #   logs/                              calibration_flags.json
    #
    # outputs_dir comes from the run config (aa-recipe.config.yaml) and must stay
    # local. The input folders may be local paths or gs:// URLs.
    #
    # gen_cal_mapping is marked regenerate: always. Its real product is the files
    # on disk, not the two dicts it returns, and a cache hit would hand back the
    # dicts without rewriting anything. Re-running this recipe always rewrites the
    # calibration outputs.
    #
    # The defaults process the WHOLE raw folder: both time bounds are unset, so no
    # window is applied. They are here for debugging - narrowing to one day turns a
    # survey-length scan into a couple of seconds. Every calibration file in
    # cal_input_folder is parsed either way, and any that no scanned channel
    # matches is moved to unused_calibration_files/.
    #
    # The op reuses the saved raw_file_configs.yaml only while the raw folder's contents and the window
    # still match what produced it, and re-parses the manufacturer files only when
    # single_channel_calibration_files/ is empty. Change raw_input_folder or a
    # bound and the scan re-runs; leave them alone and it does not.
    #
    # Both input folders may be gs:// URLs. The raw folder is scanned one file at
    # a time (download, read the channel config, delete the local copy) and the
    # calibration folder is small enough to download whole. Remote input needs the
    # gcs extra: pip install aa-si-calibration[gcs]. Outputs always stay local,
    # wherever the inputs came from.
    #
    # Usage:
    #   aa-recipe run calibration_pipeline.yaml 
    #
    # It's recommended that you use aa-calibration_pipeline.config.yaml to set
    # the raw_file_folder and cal_input_folder locations, which override the recipe input defaults


    recipe:
      name: "hb2407_calibration"
      description: "Standardize manufacturer calibration files and build the raw-channel mapping for HB2407."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        default: "./raw"
        fingerprint_mode: auto
        description: "Folder of .raw files, read for channel configuration only."
      cal_input_folder:
        type: path
        default: "./HB2407_calibration_files"
        description: "Folder of manufacturer calibration files (.cal for EK60, .xml for EK80)."
      calibration_outputs:
        type: str
        default: "calibration"
        description: "Subdirectory under the run's outputs folder for the calibration artifacts."
      cruise_id:
        type: str
        default: "HB2407"
        description: "Recorded in every generated single-channel file so it stays traceable to the survey."
      record_author:
        type: str
        default: "Brett Layman"
      file_time_start:
        type: str
        # Unset (the default) means no lower bound.
        required: false
        description: >
          Debugging knob: inclusive ISO datetime restricting the scan to raw files
          whose filename-encoded time (D{YYYYMMDD}-T{HHMMSS}) is at or after this
          bound. Leave unset to scan from the start of the folder. On a gs:// raw
          folder the filter runs before any file is downloaded.
      file_time_end:
        type: str
        # Unset (the default) means no upper bound.
        required: false
        description: >
          Debugging knob: inclusive ISO datetime upper bound. Leave unset to scan
          to the end of the folder. See file_time_start.
      conflict_resolution:
        type: str
        default: "error"
        description: >
          What to do when one raw channel matches several calibration files.
          "error" lists the conflicts and stops; "interactive" prompts for a
          choice, which needs a terminal and so cannot run unattended.

    steps:

      - id: initial_setup
        op: initial_setup
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          calibration_outputs: ${inputs.calibration_outputs}
          clear_previous_json_logs: true
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}

      - id: gen_cal_mapping
        op: generate_standardized_cal_mapping
        regenerate: always
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          cal_input_folder: ${inputs.cal_input_folder}
          output_base: ${initial_setup.calibration_output_dir}
          cruise_id: ${inputs.cruise_id}
          record_author: ${inputs.record_author}
          short_filenames: true
          # Keep unmatched calibration files rather than deleting them: they are
          # the evidence when a channel comes out unmapped.
          keep_unused: true
          conflict_resolution: ${inputs.conflict_resolution}
          # Keep these in step with initial_setup's window so the channel-config
          # scan covers exactly the files the run selected.
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
    ```

=== "Notebook"

    [Download the notebook](../data/notebooks/calibration-fused.ipynb), as `aa-recipe generate` writes it.

    ### hb2407_calibration

    Standardize manufacturer calibration files and build the raw-channel mapping for HB2407.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Utils.git
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_calibration.git
    ```

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import generate_standardized_cal_mapping
    ```

    ```python
    raw_input_folder = './raw'  # path
    cal_input_folder = './HB2407_calibration_files'  # path
    calibration_outputs = 'calibration'  # str
    cruise_id = 'HB2407'  # str
    record_author = 'Brett Layman'  # str
    file_time_start = None  # TODO: set this value  # str
    file_time_end = None  # TODO: set this value  # str
    conflict_resolution = 'error'  # str
    ```

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
    _initial_setup__raw_input_folder = './raw'
    _initial_setup__calibration_outputs = 'calibration'
    _initial_setup__clear_previous_json_logs = True
    _initial_setup__file_time_start = file_time_start
    _initial_setup__file_time_end = file_time_end

    _result = initial_setup_and_validation(
        raw_input_folder=_initial_setup__raw_input_folder,
        calibration_outputs_string=_initial_setup__calibration_outputs,
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
    _gen_cal_mapping__raw_input_folder = './raw'
    _gen_cal_mapping__cal_input_folder = './HB2407_calibration_files'
    _gen_cal_mapping__output_base = calibration_output_dir
    _gen_cal_mapping__cruise_id = 'HB2407'
    _gen_cal_mapping__record_author = 'Brett Layman'
    _gen_cal_mapping__short_filenames = True
    _gen_cal_mapping__keep_unused = True
    _gen_cal_mapping__conflict_resolution = 'error'
    _gen_cal_mapping__file_time_start = file_time_start
    _gen_cal_mapping__file_time_end = file_time_end

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
