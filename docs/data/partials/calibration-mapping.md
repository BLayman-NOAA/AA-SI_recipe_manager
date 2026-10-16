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

    # RL2307 calibration, phase 2: map raw channels to calibration data
    #
    # Runs after calibration_standardize.yaml and reuses its cached steps, so only
    # the mapping runs. Then save_calibration.yaml (phase 3) archives the result.
    #
    #   aa-recipe run calibration_mapping.yaml
    #   aa-recipe run calibration_mapping.yaml --input conflict_resolution=interactive
    #
    # Keep the four steps before build_cal_mapping identical to
    # calibration_standardize.yaml, or this recipe rescans every raw file.
    # tests/test_calibration_phase_recipes.py checks this.
    # Both phases need the same user_cache_dir and outputs_dir: keep their config
    # files identical or pass --config.
    #
    # Run locally, resolve a conflict with "error" by deleting the unwanted
    # single-channel file and re-running, or with "interactive" at the prompt.
    # From a caller, pass the reviewed channels as override_channels and use
    # "report", then run again with calibration_choices.
    #
    # Outputs under <outputs_dir>/<calibration_outputs>/:
    #
    #   mapping_files/                     channel_mapping.yaml (required by NCEI),
    #                                      calibration_configurations.yaml
    #   unused_calibration_files/          cal files no raw channel matched

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
        description: "Folder of .raw files, read for channel configuration only."
      cal_input_folder:
        type: path
        default: "gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/Calibration/RESULTS"
        description: "Folder of manufacturer calibration files (.cal for EK60, .xml for EK80)."
      calibration_outputs:
        type: str
        default: "calibration"
        description: "Subdirectory under the run's outputs folder for the calibration artifacts."
      cruise_id:
        type: str
        default: "RL2307"
        description: "Recorded in every generated single-channel file so it stays traceable to the survey."
      record_author:
        type: str
        default: "<Your Name>"
        description: "Recorded as the author of every generated single-channel file."
      file_time_start:
        type: str
        required: false
        default: "2023-07-17T16:30:46"
        description: "Used to narrow file set down for Debugging purposes. Leave unassigned for full results."
      file_time_end:
        type: str
        required: false
        default: "2023-07-18T19:50:19"
        description: "Used to narrow file set down for Debugging purposes. Leave unassigned for full results."
      override_channels:
        type: dict
        required: false
        description: >
          Reviewed channels from single_channel_data, written in place of the
          manufacturer files. Unset, both phases share standardize_cal's cache.
      conflict_resolution:
        type: str
        default: "interactive"
        description: >
          When a raw channel matches several calibration files: "report" returns the
          conflicts and writes no mapping, "error" lists them and stops, and
          "interactive" prompts, which needs a terminal.
      calibration_choices:
        type: dict
        required: false
        description: >
          Answers to a "report" run: each conflict id maps to the calibration key to
          keep, or a list of keys to average. Unanswered conflicts are reported again.

    steps:

      - id: initial_setup
        op: initial_setup
        params:
          raw_input_folder: ${inputs.raw_input_folder}
          calibration_outputs: ${inputs.calibration_outputs}
          clear_previous_json_logs: true
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}

      # One cached instance per raw file, so an interrupted scan resumes.
      - id: scan_raw_config
        op: read_raw_file_config
        map_over: ${initial_setup.raw_file_paths}
        checkpoint: always
        params:
          raw_file_path: ${_item}
          verbose: false
          # Reads a prefix that doubles from 8 MiB up to this cap, not the whole
          # file. A file that needs more is read whole.
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

      # Deleting the whole folder rebuilds it; deleting one file inside it does not,
      # which is how the conflict workflow drops a channel.
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

      # single_channel_dir and raw_configs_path are wired only to order the steps.
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
          # Keep unmatched calibration files, to explain any unmapped channel.
          keep_unused: true
          conflict_resolution: ${inputs.conflict_resolution}
          calibration_choices: ${inputs.calibration_choices}
          record_author: ${inputs.record_author}

    outputs:
      conflicts:
        step_id: build_cal_mapping
        output_name: conflicts
        description: "Unresolved conflicts. When not empty, no mapping file was written."
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
          The standardized channels the mapping was built from, including any
          averaged records. Every key in mapping_dict names one of these.
      provenance:
        step_id: build_cal_mapping
        output_name: provenance
        description: >
          Per channel, which calibration was applied over which time range, and
          whether it matched cleanly, matched with a widened tolerance, or not at all.
      provenance_path:
        step_id: build_cal_mapping
        output_name: provenance_path
        description: "The provenance report as YAML, under mapping_files/."
    ```

=== "Notebook"

    [Download the notebook](../data/notebooks/calibration-mapping.ipynb), as `aa-recipe generate` writes it.

    ### rl2307_calibration_mapping

    Match RL2307 raw channels to their calibration data and write the mapping files.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_Utils.git
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_calibration.git
    ```

    ```python
    from aa_si_utils.utils import initial_setup_and_validation
    from aa_si_calibration.calibration import read_raw_file_config
    from aa_si_calibration.calibration import record_raw_file_configs
    from aa_si_calibration.calibration import standardize_calibration_files
    from aa_si_calibration.calibration import build_calibration_mapping
    ```

    ```python
    raw_input_folder = 'gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/data/raw'  # path
    cal_input_folder = 'gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/Calibration/RESULTS'  # path
    calibration_outputs = 'calibration'  # str
    cruise_id = 'RL2307'  # str
    record_author = '<Your Name>'  # str
    file_time_start = '2023-07-17T16:30:46'  # str
    file_time_end = '2023-07-18T19:50:19'  # str
    override_channels = None  # TODO: set this value  # dict
    conflict_resolution = 'interactive'  # str
    calibration_choices = None  # TODO: set this value  # dict
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
    _initial_setup__raw_input_folder = 'gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/data/raw'
    _initial_setup__calibration_outputs = 'calibration'
    _initial_setup__clear_previous_json_logs = True
    _initial_setup__file_time_start = '2023-07-17T16:30:46'
    _initial_setup__file_time_end = '2023-07-18T19:50:19'

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

    #### Step: `scan_raw_config`
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
    for _item in raw_file_paths:
        _iter_file_config = read_raw_file_config(
        raw_file_path=_item,
        verbose=False,
        max_scan_bytes=134217728,
    )
        file_config.append(_iter_file_config)
    ```

    #### Step: `record_raw_configs`
    **Op:** `record_raw_file_configs`

    Collect per-file channel configurations into the survey's raw config file.
    The fan-in half of the raw scan. Drops the files that could not be read, sorts the rest by metadata_start_time, and writes raw_file_configs/raw_file_configs.yaml under the calibration output directory. Declare it with collect on the scan step's file_config output.

    **Parameters:**
    - `output_base` path - Root directory the calibration artifacts are written under. Normally wired to initial_setup's calibration_output_dir.

    - `verbose` bool - Print a summary of what was recorded.

    ```python
    # --- Parameters ---
    _record_raw_configs__output_base = calibration_output_dir

    _result = record_raw_file_configs(file_configs=file_config, output_base=_record_raw_configs__output_base)
    raw_file_configs = _result['raw_file_configs']
    frequencies = _result['frequencies']
    raw_configs_path = _result['raw_configs_path']
    ```

    #### Step: `standardize_cal`
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
    _standardize_cal__cal_input_folder = 'gs://ggn-nmfs-aa-prod-1-data/HDD/Reuben_Lasker/RL2307/EK80/Calibration/RESULTS'
    _standardize_cal__output_base = calibration_output_dir
    _standardize_cal__cruise_id = 'RL2307'
    _standardize_cal__record_author = '<Your Name>'
    _standardize_cal__short_filenames = True
    _standardize_cal__override_channels = override_channels

    _result = standardize_calibration_files(
        frequencies=frequencies,
        cal_input_folder=_standardize_cal__cal_input_folder,
        output_base=_standardize_cal__output_base,
        cruise_id=_standardize_cal__cruise_id,
        record_author=_standardize_cal__record_author,
        short_filenames=_standardize_cal__short_filenames,
        override_channels=_standardize_cal__override_channels,
    )
    single_channel_dir = _result['single_channel_dir']
    channel_count = _result['channel_count']
    skipped = _result['skipped']
    standardize_cal_single_channel_data = _result['single_channel_data']
    ```

    #### Step: `build_cal_mapping`
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
    _build_cal_mapping__output_base = calibration_output_dir
    _build_cal_mapping__single_channel_dir = single_channel_dir
    _build_cal_mapping__raw_configs_path = raw_configs_path
    _build_cal_mapping__short_filenames = True
    _build_cal_mapping__keep_unused = True
    _build_cal_mapping__conflict_resolution = 'interactive'
    _build_cal_mapping__calibration_choices = calibration_choices
    _build_cal_mapping__record_author = '<Your Name>'

    _result = build_calibration_mapping(
        raw_file_configs=raw_file_configs,
        output_base=_build_cal_mapping__output_base,
        single_channel_dir=_build_cal_mapping__single_channel_dir,
        raw_configs_path=_build_cal_mapping__raw_configs_path,
        short_filenames=_build_cal_mapping__short_filenames,
        keep_unused=_build_cal_mapping__keep_unused,
        conflict_resolution=_build_cal_mapping__conflict_resolution,
        calibration_choices=_build_cal_mapping__calibration_choices,
        record_author=_build_cal_mapping__record_author,
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
