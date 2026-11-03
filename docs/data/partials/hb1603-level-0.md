=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-level-0" data-drill="inc:sub_recipes/processing_lvl_0_user.yaml" markdown>

    - `query_ncei`: [`query_ncei_data`](../ops/query_ncei_data.md)
    - `scan_raw_config`: [`read_raw_file_config`](../ops/read_raw_file_config.md)
    - `record_raw_configs`: [`record_raw_file_configs`](../ops/record_raw_file_configs.md)
    - `standardize_cal`: [`standardize_calibration_files`](../ops/standardize_calibration_files.md)
    - `build_cal_mapping`: [`build_calibration_mapping`](../ops/build_calibration_mapping.md)

    </div>

=== "YAML"

    [Download `processing_lvl_0_user.yaml`](../recipes/HB1603/sub_recipes/processing_lvl_0_user.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/processing_lvl_0_user.yaml

    # Processing level 0 (user): the survey's catalogue and calibration, loaded.
    #
    # processing_lvl_0_survey.yaml's five steps, wired identically, without the
    # regenerate attributes. regenerate is not fingerprinted, so every hash equals
    # the survey variant's: build_cal_mapping loads from the survey cache, the
    # three steps between it and query_ncei are pruned, and no calibration file is
    # read or written.
    #
    # It cannot build the calibration. If the survey has not been run, or a step
    # here no longer matches the survey variant, build_cal_mapping fails with
    # "Calibration files directory not found": run survey_sv.yaml rather than
    # editing this file. processing_lvl_0_survey.yaml explains the steps, the scan
    # cap and the widened power tolerance.
    #
    # KEEP THE TWO VARIANTS IN STEP
    #
    #   A change to a step's wiring or params belongs in both files or neither.
    #   Every step hash in dive_analysis.yaml must equal the same step's hash in
    #   survey_sv.yaml.

    recipe:
      name: "hb1603_processing_lvl_0_user"
      description: "The survey's catalogue and calibration mapping, loaded from the cache and never regenerated."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      cruise_name:
        type: str
        default: "HB1603"
        description: >
          Cruise name searched for in the NCEI catalogue, such as "HB1603".
      file_time_start:
        type: str
        default: "2016-06-01T00:00:00"
        description: >
          Start of the NCEI catalogue query, an inclusive ISO UTC datetime such as
          "2016-07-25T20:58". Every file overlapping the window is fetched.
      file_time_end:
        type: str
        default: "2016-09-01T00:00:00"
        description: >
          End of the NCEI catalogue query, an inclusive ISO UTC datetime such as
          "2016-07-25T21:45". Every file overlapping the window is fetched.
      cal_input_folder:
        type: path
        description: >
          Folder of manufacturer calibration files (.cal for EK60, .xml for EK80),
          a local path or a gs:// URL.
      calibration_output_dir:
        type: path
        default: "./outputs/calibration"
        description: "Root folder the calibration artifacts are written under."
      cruise_id:
        type: str
        default: "HB1603"
        description: >
          Cruise identifier, such as "HB1603", recorded in every generated
          single-channel calibration file so it stays traceable to the survey.
      record_author:
        type: str
        default: "<Your Name>"
        description: "Name recorded as the author of every generated single-channel calibration file."
      max_scan_bytes:
        type: int
        default: 1048576
        description: >
          Most leading bytes read from each raw file to find its channel
          configuration. The default, 1 MiB, settles every HB1603 file in one
          small read.
      match_tolerances:
        type: dict
        default:
          transmit_power: 1001.0
        description: >
          Per-field tolerances for matching a raw channel to a calibration file
          (Hz, W, or s), merged over exact defaults. The default widens
          transmit_power to 1001 W.

    steps:

      - id: query_ncei
        op: query_ncei_data
        checkpoint: always
        params:
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          filters:
            CRUISE_NAME: ${inputs.cruise_name}

      # One instance per file, content-addressed by its URL, so an interrupted
      # scan resumes from what it already read rather than starting over.
      # verbose is off because one banner per file buries the rest of the log.
      - id: scan_raw_config
        op: read_raw_file_config
        map_over: ${query_ncei.raw_urls}
        checkpoint: always
        params:
          raw_file_path: ${_item}
          verbose: false
          max_scan_bytes: ${inputs.max_scan_bytes}

      - id: record_raw_configs
        op: record_raw_file_configs
        collect: ${scan_raw_config.file_config}
        checkpoint: always
        inputs:
          file_configs: ${scan_raw_config.file_config}
        params:
          output_base: ${inputs.calibration_output_dir}

      - id: standardize_cal
        op: standardize_calibration_files
        checkpoint: always
        inputs:
          frequencies: ${record_raw_configs.frequencies}
        params:
          cal_input_folder: ${inputs.cal_input_folder}
          output_base: ${inputs.calibration_output_dir}
          cruise_id: ${inputs.cruise_id}
          record_author: ${inputs.record_author}
          short_filenames: true

      # No regenerate, unlike the survey variant, so this loads the checkpointed
      # mapping and nothing below reads a calibration file: calibration_output_dir
      # need not exist on this machine.
      - id: build_cal_mapping
        op: build_calibration_mapping
        checkpoint: always
        inputs:
          raw_file_configs: ${record_raw_configs.raw_file_configs}
        params:
          output_base: ${inputs.calibration_output_dir}
          single_channel_dir: ${standardize_cal.single_channel_dir}
          raw_configs_path: ${record_raw_configs.raw_configs_path}
          short_filenames: true
          tolerances: ${inputs.match_tolerances}
          # Keep unmatched calibration files rather than deleting them: they are
          # the evidence when a channel comes out unmapped.
          keep_unused: true
          conflict_resolution: "error"
    ```

=== "Python"

    The function calls this part of the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_utils.data_retrieval import query_ncei_data
    from aa_si_calibration.calibration import read_raw_file_config
    from aa_si_calibration.calibration import record_raw_file_configs
    from aa_si_calibration.calibration import standardize_calibration_files
    from aa_si_calibration.calibration import build_calibration_mapping

    # query_ncei: Query an NCEI acoustic data catalog for file listings within a
    # time window.
    _result = query_ncei_data(
        file_time_start='2016-06-01T00:00:00',
        file_time_end='2016-09-01T00:00:00',
        filters={'CRUISE_NAME': 'HB1603'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']

    file_config = []
    for _item in raw_urls:
        # scan_raw_config: Read one raw file's channel configuration from its datagram
        # headers.
        _iter_file_config = read_raw_file_config(
            raw_file_path=_item,
            verbose=False,
            max_scan_bytes=1048576,
        )
        file_config.append(_iter_file_config)

    # record_raw_configs: Collect per-file channel configurations into
    # raw_file_configs/raw_file_configs.yaml under the calibration output
    # directory.
    _result = record_raw_file_configs(file_configs=file_config, output_base='./outputs/calibration')
    raw_file_configs = _result['raw_file_configs']
    frequencies = _result['frequencies']
    raw_configs_path = _result['raw_configs_path']

    # standardize_cal: Convert manufacturer calibration files (EK60 .cal or EK80
    # .xml) to standardized single-channel .yaml files under
    # single_channel_calibration_files/.
    _result = standardize_calibration_files(
        frequencies=frequencies,
        cal_input_folder='../calibration_files/HB201607_cal',
        output_base='./outputs/calibration',
        cruise_id='HB1603',
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
        output_base='./outputs/calibration',
        single_channel_dir=single_channel_dir,
        raw_configs_path=raw_configs_path,
        short_filenames=True,
        tolerances={'transmit_power': 1001.0},
        keep_unused=True,
        conflict_resolution='error',
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
