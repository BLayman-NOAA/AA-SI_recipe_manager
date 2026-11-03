=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="calibration-standardize" data-slug="calibration-standardize" markdown>

    - `initial_setup`: [`initial_setup`](../ops/initial_setup.md)
    - `scan_raw_config`: [`read_raw_file_config`](../ops/read_raw_file_config.md)
    - `record_raw_configs`: [`record_raw_file_configs`](../ops/record_raw_file_configs.md)
    - `standardize_cal`: [`standardize_calibration_files`](../ops/standardize_calibration_files.md)

    </div>

=== "YAML"

    [Download `calibration_standardize.yaml`](../recipes/calibration/calibration_standardize.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/RL2307/calibration_standardize.yaml

    # RL2307 calibration, phase 1: standardize.
    # Scans the raw channel configurations and turns the manufacturer calibration
    # files into one standardized file per channel, stopping before the mapping so
    # a user can review and correct them. Phase 2 is calibration_mapping.yaml;
    # save_calibration.yaml (phase 3) archives the result.
    #
    # The four steps below are copied verbatim into calibration_mapping.yaml and
    # must stay that way: once the copies drift, phase 2 stops reusing the raw
    # scan and reads every file again, with no error.
    # tests/test_calibration_phase_recipes.py asserts the hashes match; run it
    # after editing either file.
    #
    # Artifacts land under <outputs_dir>/<calibration_outputs>/:
    #   raw_file_configs/                  raw_file_configs.yaml
    #   single_channel_calibration_files/  one standardized .yaml per channel (required by NCEI)
    #   logs/                              calibration_flags.json
    #   standardization.fingerprint.json   which manufacturer files produced the
    #                                      single-channel files above
    #
    # Both phases must resolve to the same user_cache_dir and outputs_dir or the
    # cache is silently not shared: keep the two <recipe_stem>.config.yaml files
    # identical or pass --config explicitly.
    #
    # The three phase recipes back the calibration service's three HTTP calls; see
    # calibration_service_api.md in AA-SI_calibration/recipes/documentation/.
    #
    # Usage:
    #   aa-recipe run calibration_standardize.yaml

    recipe:
      name: "RL2307_calibration_standardize"
      description: "Standardize the RL2307 manufacturer calibration files and return them for review."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        default: "gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/data/raw"
        fingerprint_mode: auto
        description: >
          Folder of raw .raw files, a local path or a gs:// URL, read for channel
          configuration only.
      cal_input_folder:
        type: path
        default: "gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/Calibration/RESULTS"
        description: >
          Folder of manufacturer calibration files (.cal for EK60, .xml for EK80),
          a local path or a gs:// URL.
      calibration_outputs:
        type: str
        default: "calibration"
        description: >
          Name of the subfolder of the run's outputs folder that calibration
          artifacts are written to.
      cruise_id:
        type: str
        default: "RL2307"
        description: >
          Cruise identifier, such as "HB1603", recorded in every generated
          single-channel calibration file so it stays traceable to the survey.
      record_author:
        type: str
        default: "<Your Name>"
        description: "Name recorded as the author of every generated single-channel calibration file."
      file_time_start:
        type: str
        required: false
        default: "2023-07-17T16:30:46"
        description: >
          Optional inclusive ISO datetime, for debugging, matched to the
          D{YYYYMMDD}-T{HHMMSS} time in each raw file name; files at or after it
          are scanned for calibration. Unset takes every file.
      file_time_end:
        type: str
        required: false
        default: "2023-07-18T19:50:19"
        description: >
          Optional inclusive ISO datetime, for debugging, matched to the
          D{YYYYMMDD}-T{HHMMSS} time in each raw file name; files at or before it
          are scanned for calibration. Unset takes every file.
      # override_channels is declared and left unset in both phase recipes: an
      # unset value hashes identically, so standardize_cal shares its cache entry
      # until a caller supplies edits.
      override_channels:
        type: dict
        required: false
        description: >
          Reviewed standardized channels, in the shape of the single_channel_data
          output, written in place of parsing the manufacturer files. Unset parses
          the files.

    steps:

      - id: initial_setup
        op: initial_setup
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          calibration_outputs: ${inputs.calibration_outputs}
          clear_previous_json_logs: true
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}

      # One instance per raw file, so an interrupted scan resumes from what it
      # already read. verbose is off: one banner per file buries the log.
      - id: scan_raw_config
        op: read_raw_file_config
        map_over: ${initial_setup.raw_file_paths}
        checkpoint: always
        params:
          raw_file_path: ${_item}
          verbose: false
          # Reads a growing prefix, 8 MiB doubling to this cap, until every
          # channel's Parameter datagram is in hand. RL2307's FM files climb past
          # the first rung; a file that never settles is read whole.
          max_scan_bytes: 134217728  # 128 MiB

      - id: record_raw_configs
        op: record_raw_file_configs
        collect: ${scan_raw_config.file_config}
        checkpoint: always
        regenerate: if-missing
        inputs:
          file_configs: ${scan_raw_config.file_config}
        params:
          output_base: ${initial_setup.calibration_output_dir}

      # regenerate: if-missing is safe only because this step records its output
      # directory: wiping the folder rebuilds it, deleting one file inside does
      # not, which the conflict workflow relies on. A run in a fresh container
      # rebuilds the folder, from override_channels or the manufacturer files.
      - id: standardize_cal
        op: standardize_calibration_files
        checkpoint: always
        regenerate: if-missing
        inputs:
          frequencies: ${record_raw_configs.frequencies}
        params:
          cal_input_folder: ${inputs.cal_input_folder}
          output_base: ${initial_setup.calibration_output_dir}
          cruise_id: ${inputs.cruise_id}
          record_author: ${inputs.record_author}
          short_filenames: true
          override_channels: ${inputs.override_channels}

    outputs:
      single_channel_data:
        step_id: standardize_cal
        output_name: single_channel_data
        description: >
          The standardized channels, for a caller to review and edit before the
          mapping runs. Feed the edited version back to calibration_mapping.yaml as
          override_channels.
      single_channel_dir:
        step_id: standardize_cal
        output_name: single_channel_dir
        description: "Folder the standardized single-channel files were written to."
      raw_file_configs:
        step_id: record_raw_configs
        output_name: raw_file_configs
        description: "The scanned channel configuration of every raw file in the window."
    ```

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/calibration-standardize.ipynb).

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import read_raw_file_config
    from aa_si_calibration.calibration import record_raw_file_configs
    from aa_si_calibration.calibration import standardize_calibration_files

    # Inputs
    override_channels = None  # TODO: set this value  # dict

    # initial_setup: Validate the raw files in the input folder, create the
    # calibration output directory if needed, and return the raw file paths and
    # that directory.
    _result = initial_setup_and_validation(
        raw_input_folder='gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/data/raw',
        calibration_outputs_string='calibration',
        clear_previous_json_logs=True,
        file_time_start='2023-07-17T16:30:46',
        file_time_end='2023-07-18T19:50:19',
    )
    raw_file_paths = _result['raw_file_paths']
    calibration_output_dir = _result['calibration_output_dir']

    file_config = []
    for _item in raw_file_paths:
        # scan_raw_config: Read one raw file's channel configuration from its datagram
        # headers.
        _iter_file_config = read_raw_file_config(
            raw_file_path=_item,
            verbose=False,
            max_scan_bytes=134217728,
        )
        file_config.append(_iter_file_config)

    # record_raw_configs: Collect per-file channel configurations into
    # raw_file_configs/raw_file_configs.yaml under the calibration output
    # directory.
    _result = record_raw_file_configs(file_configs=file_config, output_base=calibration_output_dir)
    raw_file_configs = _result['raw_file_configs']
    frequencies = _result['frequencies']
    raw_configs_path = _result['raw_configs_path']

    # standardize_cal: Convert manufacturer calibration files (EK60 .cal or EK80
    # .xml) to standardized single-channel .yaml files under
    # single_channel_calibration_files/.
    _result = standardize_calibration_files(
        frequencies=frequencies,
        cal_input_folder='gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/Calibration/RESULTS',
        output_base=calibration_output_dir,
        cruise_id='RL2307',
        record_author='<Your Name>',
        short_filenames=True,
        override_channels=override_channels,
    )
    single_channel_dir = _result['single_channel_dir']
    channel_count = _result['channel_count']
    skipped = _result['skipped']
    single_channel_data = _result['single_channel_data']
    ```
