=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb2407-herring-per-file" data-slug="hb2407-level-0" data-drill="inc:sub_recipes/processing_lvl_0_calibration.yaml" markdown>

    - `initial_setup`: [`initial_setup`](../ops/initial_setup.md)
    - `gen_cal_mapping`: [`generate_standardized_cal_mapping`](../ops/generate_standardized_cal_mapping.md)

    </div>

=== "YAML"

    [Download `processing_lvl_0_calibration.yaml`](../recipes/HB2407/sub_recipes/processing_lvl_0_calibration.yaml)

    ```yaml
    # Documentation copy of the first two steps of AA-SI_Full_Pipeline_Example/
    # example_recipes/HB2407/sub_recipes/processing_lvl_1_per_file.yaml, split out
    # to match the HB1603 levels.

    # Processing level 0: select the raw files and build the calibration mapping.
    #
    #   initial_setup -> gen_cal_mapping
    #
    # initial_setup lists the raw files in the time window, and levels 1 and 2 map
    # over that list. gen_cal_mapping matches every raw channel to a calibration
    # (.xml files for EK80), keyed by raw filename, and level 2 picks out each
    # file's entry.

    recipe:
      name: "processing_lvl_0_calibration"
      description: "Raw file selection and the survey's calibration mapping (gs:// inputs)."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      raw_input_folder:
        type: path
        description: "Folder of raw .raw files, a local path or a gs:// URL."
      cal_input_folder:
        type: path
        description: "Folder of manufacturer calibration files (.cal for EK60, .xml for EK80), a local path or a gs:// URL."
      calibration_outputs:
        type: str
        default: "calibration"
        description: "Name of the subfolder of the run's outputs folder that calibration artifacts are written to."
      cruise_id:
        type: str
        description: >
          Cruise identifier, such as "HB1603", recorded in every generated
          single-channel calibration file so it stays traceable to the survey.
      record_author:
        type: str
        default: "<Your Name>"
        description: "Name recorded as the author of every generated single-channel calibration file."
      raw_file_names:
        type: list
        default: []
        description: >
          Optional list of file names within raw_input_folder to process. An empty
          list uses every .raw file in the folder.
      # file_time_start also sets the fan-out width: one mapped instance per
      # selected file. On a gs:// folder the filter runs on names, before any download.
      file_time_start:
        type: str
        required: false
        description: >
          Optional inclusive ISO datetime matched to the D{YYYYMMDD}-T{HHMMSS} time
          in each raw file name; files at or after it are processed and scanned
          for calibration. Unset takes every file.
      file_time_end:
        type: str
        required: false
        description: >
          Optional inclusive ISO datetime matched to the D{YYYYMMDD}-T{HHMMSS} time
          in each raw file name; files at or before it are processed and scanned
          for calibration. Unset takes every file.

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

      # Runs once for the survey; each extract_cal_params instance picks its own
      # file's entry out of mapping_dict.
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

=== "Python"

    The function calls this part of the [HB2407 herring, per file notebook](../data/notebooks/hb2407-herring-per-file.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import generate_standardized_cal_mapping

    # initial_setup: Validate the raw files in the input folder, create the
    # calibration output directory if needed, and return the raw file paths and
    # that directory.
    _result = initial_setup_and_validation(
        raw_input_folder='${inputs.raw_input_folder}',
        calibration_outputs_string='calibration',
        raw_file_names=[],
        clear_previous_json_logs=True,
        file_time_start='2024-10-15T13:38:52',
        file_time_end='2024-10-15T23:43:36',
    )
    raw_file_paths = _result['raw_file_paths']
    calibration_output_dir = _result['calibration_output_dir']

    # gen_cal_mapping: Read manufacturer calibration files, convert them to per-
    # channel standardized files, match each raw channel to its calibration data,
    # and return a mapping dict and a calibration dict.
    _result = generate_standardized_cal_mapping(
        raw_input_folder='${inputs.raw_input_folder}',
        cal_input_folder='./HB2407_calibration_files',
        output_base=calibration_output_dir,
        cruise_id='HB2407',
        record_author='<Your Name>',
        short_filenames=True,
        keep_unused=True,
        conflict_resolution='error',
        file_time_start='2024-10-15T13:38:52',
        file_time_end='2024-10-15T23:43:36',
    )
    mapping_dict = _result['mapping_dict']
    calibration_dict = _result['calibration_dict']
    ```
