=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="calibration-mapping" data-slug="calibration-mapping" markdown>

    - `initial_setup`: [`initial_setup`](../ops/initial_setup.md)
    - `scan_raw_config`: [`read_raw_file_config`](../ops/read_raw_file_config.md)
    - `record_raw_configs`: [`record_raw_file_configs`](../ops/record_raw_file_configs.md)
    - `standardize_cal`: [`standardize_calibration_files`](../ops/standardize_calibration_files.md)
    - `build_cal_mapping`: [`build_calibration_mapping`](../ops/build_calibration_mapping.md)

    </div>

=== "YAML"

    [Download `calibration_mapping.yaml`](../recipes/calibration/calibration_mapping.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/RL2307/calibration_mapping.yaml

    # RL2307 calibration, phase 2: map raw channels to calibration data.
    # Runs after calibration_standardize.yaml, sharing its cache root, so only the
    # mapping runs.
    #
    # The four steps before build_cal_mapping are copied verbatim from
    # calibration_standardize.yaml and must stay that way: once the copies drift,
    # this recipe stops reusing the raw scan and reads every file again, with no
    # error. tests/test_calibration_phase_recipes.py asserts the hashes match; run
    # it after editing either file.
    #
    #   LOCAL      Run it after phase 1 with nothing extra. With "error", delete
    #              the unwanted single-channel file and re-run; "interactive"
    #              prompts.
    #   A CALLER   Pass phase 1's reviewed single_channel_data back as
    #              override_channels and set conflict_resolution to "report", which
    #              returns the conflicts and writes no mapping file. Present them,
    #              then run again with calibration_choices filled in.
    #
    # Artifacts land under <outputs_dir>/<calibration_outputs>/:
    #   mapping_files/                     channel_mapping.yaml (required by NCEI),
    #                                      calibration_configurations.yaml
    #   unused_calibration_files/          calibration files no raw channel matched
    #
    # Both phases must resolve to the same user_cache_dir and outputs_dir or the
    # cache is silently not shared: keep the two <recipe_stem>.config.yaml files
    # identical or pass --config explicitly.
    #
    # Once the mapping is clean, save_calibration.yaml (phase 3) archives it.
    #
    # Usage:
    #   aa-recipe run calibration_mapping.yaml
    #   aa-recipe run calibration_mapping.yaml --input conflict_resolution=report

    recipe:
      name: "rl2307_calibration_mapping"
      description: "Match RL2307 raw channels to their calibration data and write the mapping files."
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
        description: >
          Name recorded as the author of every generated single-channel
          calibration file, and of any record averaged or merged while resolving
          conflicts.
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
      override_channels:
        type: dict
        required: false
        description: >
          Reviewed standardized channels, in the shape of the single_channel_data
          output, written in place of parsing the manufacturer files. Unset parses
          the files.
      conflict_resolution:
        type: str
        default: "interactive"
        description: >
          What to do when one raw channel matches several calibration files:
          "error" lists the conflicts and stops; "interactive" prompts on the
          terminal, so it cannot run unattended. "report" returns the conflicts
          and writes no mapping.
      # An average or merge becomes a new single-channel file. A merge item may
      # itself be {"average": [keys]}.
      calibration_choices:
        type: dict
        required: false
        description: >
          Answers to a "report" run: each conflict id maps to the calibration key
          to keep, a list of keys to average, or {"merge": [keys]} to merge them
          in priority order, EK80 style. Unanswered conflicts are reported again.

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

      # single_channel_dir and raw_configs_path are wired for the ordering edges;
      # both would otherwise default to the same locations under output_base.
      - id: build_cal_mapping
        op: build_calibration_mapping
        checkpoint: always
        regenerate: always
        inputs:
          raw_file_configs: ${record_raw_configs.raw_file_configs}
        params:
          output_base: ${initial_setup.calibration_output_dir}
          single_channel_dir: ${standardize_cal.single_channel_dir}
          raw_configs_path: ${record_raw_configs.raw_configs_path}
          short_filenames: true
          # Keep unmatched calibration files rather than deleting them: they are
          # the evidence when a channel comes out unmapped.
          keep_unused: true
          conflict_resolution: ${inputs.conflict_resolution}
          calibration_choices: ${inputs.calibration_choices}
          record_author: ${inputs.record_author}

    outputs:
      conflicts:
        step_id: build_cal_mapping
        output_name: conflicts
        description: >
          Unresolved conflicts for a caller to present. Empty when the mapping
          completed; non-empty means no mapping file was written.
      mapping_dict:
        step_id: build_cal_mapping
        output_name: mapping_dict
        description: "Each raw file's channels mapped to their calibration key."
      calibration_dict:
        step_id: build_cal_mapping
        output_name: calibration_dict
        description: "The calibration values behind each key in the mapping."
      single_channel_data:
        step_id: build_cal_mapping
        output_name: single_channel_data
        description: >
          The standardized channels the mapping was built from, with their final
          keys, plus any record this run averaged or merged. Every key in
          mapping_dict names one of these.
      provenance:
        step_id: build_cal_mapping
        output_name: provenance
        description: >
          Per-channel account of what calibration was applied where, as time-range
          segments. Each says whether it matched outright, only with a widened
          tolerance, or not at all, and why. JSON-safe, for returning to a client.
      provenance_path:
        step_id: build_cal_mapping
        output_name: provenance_path
        description: >
          Where the same report was written as YAML, under mapping_files/ in the
          calibration outputs.
    ```

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/calibration-mapping.ipynb).

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import read_raw_file_config
    from aa_si_calibration.calibration import record_raw_file_configs
    from aa_si_calibration.calibration import standardize_calibration_files
    from aa_si_calibration.calibration import build_calibration_mapping

    # Inputs
    override_channels = None  # TODO: set this value  # dict
    calibration_choices = None  # TODO: set this value  # dict

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
    standardize_cal_single_channel_data = _result['single_channel_data']

    # build_cal_mapping: Match each raw channel to its calibration data and save
    # the mapping files under mapping_files/.
    _result = build_calibration_mapping(
        raw_file_configs=raw_file_configs,
        output_base=calibration_output_dir,
        single_channel_dir=single_channel_dir,
        raw_configs_path=raw_configs_path,
        short_filenames=True,
        keep_unused=True,
        conflict_resolution='interactive',
        calibration_choices=calibration_choices,
        record_author='<Your Name>',
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
