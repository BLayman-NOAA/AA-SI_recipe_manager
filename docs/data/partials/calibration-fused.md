=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="calibration-fused" data-slug="calibration-fused" markdown>

    - `initial_setup`: [`initial_setup`](../ops/initial_setup.md)
    - `gen_cal_mapping`: [`generate_standardized_cal_mapping`](../ops/generate_standardized_cal_mapping.md)

    </div>

=== "YAML"

    [Download `calibration_pipeline_fused.yaml`](../recipes/calibration/calibration_pipeline_fused.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB2407/calibration_pipeline.yaml

    # HB2407 calibration only: standardize the calibration files and build the mapping.
    #
    # Artifacts land under <outputs_dir>/<calibration_outputs>/:
    #   raw_file_configs/                  channel config scanned from the raw files
    #   single_channel_calibration_files/  one standardized .yaml per channel (required by NCEI)
    #   mapping_files/                     channel_mapping.yaml (required by NCEI),
    #                                      calibration_configurations.yaml
    #   unused_calibration_files/          calibration files no raw channel matched
    #   logs/                              calibration_flags.json
    #
    # outputs_dir comes from the run config and must stay local. Remote input
    # folders need the gcs extra: pip install aa-si-calibration[gcs].
    #
    # gen_cal_mapping is regenerate: always: its real product is the files on
    # disk, and a cache hit would return the dicts without rewriting them.
    #
    # The saved raw_file_configs.yaml is reused only while the raw folder and the
    # window match what produced it; the manufacturer files are re-parsed only when
    # single_channel_calibration_files/ is empty.
    #
    # Usage:
    #   aa-recipe run calibration_pipeline.yaml
    # Set raw_input_folder and cal_input_folder in calibration_pipeline.config.yaml;
    # its values override the recipe input defaults.


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
        description: >
          Folder of raw .raw files, a local path or a gs:// URL, read for channel
          configuration only.
      cal_input_folder:
        type: path
        default: "./HB2407_calibration_files"
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
        default: "HB2407"
        description: >
          Cruise identifier, such as "HB1603", recorded in every generated
          single-channel calibration file so it stays traceable to the survey.
      record_author:
        type: str
        default: "Brett Layman"
        description: "Name recorded as the author of every generated single-channel calibration file."
      file_time_start:
        type: str
        required: false
        description: >
          Optional inclusive ISO datetime, for debugging, matched to the
          D{YYYYMMDD}-T{HHMMSS} time in each raw file name; files at or after it
          are scanned for calibration. Unset takes every file.
      file_time_end:
        type: str
        required: false
        description: >
          Optional inclusive ISO datetime, for debugging, matched to the
          D{YYYYMMDD}-T{HHMMSS} time in each raw file name; files at or before it
          are scanned for calibration. Unset takes every file.
      # The local workflow for "error": delete the unwanted single-channel file and
      # re-run. "interactive" fails before moving any file when the run has no
      # interactive input.
      conflict_resolution:
        type: str
        default: "error"
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

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/calibration-fused.ipynb).

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import generate_standardized_cal_mapping

    # Inputs
    file_time_start = None  # TODO: set this value  # str
    file_time_end = None  # TODO: set this value  # str

    # initial_setup: Validate the raw files in the input folder, create the
    # calibration output directory if needed, and return the raw file paths and
    # that directory.
    _result = initial_setup_and_validation(
        raw_input_folder='./raw',
        calibration_outputs_string='calibration',
        clear_previous_json_logs=True,
        file_time_start=file_time_start,
        file_time_end=file_time_end,
    )
    raw_file_paths = _result['raw_file_paths']
    calibration_output_dir = _result['calibration_output_dir']

    # gen_cal_mapping: Read manufacturer calibration files, convert them to per-
    # channel standardized files, match each raw channel to its calibration data,
    # and return a mapping dict and a calibration dict.
    _result = generate_standardized_cal_mapping(
        raw_input_folder='./raw',
        cal_input_folder='./HB2407_calibration_files',
        output_base=calibration_output_dir,
        cruise_id='HB2407',
        record_author='Brett Layman',
        short_filenames=True,
        keep_unused=True,
        conflict_resolution='error',
        file_time_start=file_time_start,
        file_time_end=file_time_end,
    )
    mapping_dict = _result['mapping_dict']
    calibration_dict = _result['calibration_dict']
    ```
