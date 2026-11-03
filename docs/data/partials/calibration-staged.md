=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="calibration-staged" data-slug="calibration-staged" markdown>

    - `initial_setup`: [`initial_setup`](../ops/initial_setup.md)
    - `scan_raw_config`: [`read_raw_file_config`](../ops/read_raw_file_config.md)
    - `record_raw_configs`: [`record_raw_file_configs`](../ops/record_raw_file_configs.md)
    - `standardize_cal`: [`standardize_calibration_files`](../ops/standardize_calibration_files.md)
    - `build_cal_mapping`: [`build_calibration_mapping`](../ops/build_calibration_mapping.md)

    </div>

=== "YAML"

    [Download `calibration_pipeline_staged.yaml`](../recipes/calibration/calibration_pipeline_staged.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/RL2307/calibration_pipeline_staged.yaml.
    # Its title and recipe name said HB2407; they are corrected to RL2307, which its defaults point at.

    # RL2307 calibration only, staged: the outputs of the fused
    # generate_standardized_cal_mapping step (see HB2407/calibration_pipeline.yaml)
    # as separately cached steps.
    #
    #   initial_setup        resolve the raw file set and the output directory
    #   scan_raw_config      one instance per raw file, reading its channel config
    #   record_raw_configs   fan the per-file results in, write raw_file_configs.yaml
    #   standardize_cal      parse the manufacturer files, one file per channel
    #   build_cal_mapping    match each raw channel to its calibration data
    #
    # Artifacts land under <outputs_dir>/<calibration_outputs>/:
    #   raw_file_configs/                  raw_file_configs.yaml
    #   single_channel_calibration_files/  one standardized .yaml per channel (required by NCEI)
    #   mapping_files/                     channel_mapping.yaml (required by NCEI),
    #                                      calibration_configurations.yaml
    #   unused_calibration_files/          calibration files no raw channel matched
    #   logs/                              calibration_flags.json
    #   standardization.fingerprint.json   which manufacturer files produced the
    #                                      single-channel files above
    #
    # Split, each stage answers to its own inputs. On a re-run:
    #   nothing changed          everything cached except build_cal_mapping
    #   scan interrupted         only the unscanned files are read
    #   a calibration file       standardize_cal and build_cal_mapping re-run, the
    #     corrected or replaced  raw scan does not
    #   raw_file_configs.yaml    rebuilt from the cached per-file scans
    #     deleted
    #   the single-channel       rebuilt in full
    #     folder deleted
    #   one single-channel       not put back (the conflict workflow);
    #     file deleted by hand   build_cal_mapping re-runs against what is left
    #
    # Adding a raw file re-scans every file: initial_setup fingerprints the raw
    # folder, and every scan_raw_config instance descends from it.
    #
    # The default checkpoint mode is "explicit", so the cached steps say
    # checkpoint: always. Steps record the files they write, which is what lets
    # regenerate: if-missing tell that an output was deleted.
    #
    # outputs_dir comes from the run config and must stay local. Remote input
    # folders need the gcs extra: pip install aa-si-calibration[gcs].
    #
    # Usage:
    #   aa-recipe run calibration_pipeline_staged.yaml

    recipe:
      name: "rl2307_calibration_staged"
      description: "Standardize manufacturer calibration files and build the raw-channel mapping for RL2307, as separately cached stages."
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
      conflict_resolution:
        type: str
        default: "interactive"
        description: >
          What to do when one raw channel matches several calibration files:
          "error" lists the conflicts and stops; "interactive" prompts on the
          terminal, so it cannot run unattended.

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
      # not, which the conflict workflow relies on.
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
    ```

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/calibration-staged.ipynb).

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import read_raw_file_config
    from aa_si_calibration.calibration import record_raw_file_configs
    from aa_si_calibration.calibration import standardize_calibration_files
    from aa_si_calibration.calibration import build_calibration_mapping

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
