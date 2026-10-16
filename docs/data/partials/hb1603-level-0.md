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
    # The same steps as processing_lvl_0_survey.yaml without the regenerate
    # settings, so the hashes match and the survey's cached mapping is loaded.
    # processing_lvl_0_survey.yaml explains the steps and their settings.
    #
    # This file cannot build the calibration. If build_cal_mapping fails with
    # "Calibration files directory not found", run survey_sv.yaml first.
    # Keep the two files in step: a change to a step belongs in both or neither.

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
        description: "CRUISE_NAME filter for the NCEI catalogue query."
      file_time_start:
        type: str
        default: "2016-06-01T00:00:00"
        description: >
          Lower bound of the catalogue query, set wider than the cruise. Moving it
          reruns every per-file step.
      file_time_end:
        type: str
        default: "2016-09-01T00:00:00"
        description: "Upper bound of the catalogue query. See file_time_start."
      cal_input_folder:
        type: path
        description: "Folder of manufacturer calibration files (.cal for EK60), local or gs://."
      calibration_output_dir:
        type: path
        default: "./outputs/calibration"
        description: "Root folder the calibration artifacts are written under."
      cruise_id:
        type: str
        default: "HB1603"
        description: "Recorded in every generated single-channel file."
      record_author:
        type: str
        default: "<Your Name>"
        description: "Recorded as the author of every generated single-channel file."
      max_scan_bytes:
        type: int
        default: 1048576
        description: "Leading bytes read per raw file. Read the survey variant before lowering it."
      match_tolerances:
        type: dict
        default:
          transmit_power: 1001.0
        description: >
          Per-field tolerances for matching a raw channel to a calibration file.
          transmit_power is widened for HB1603's leg 1; see processing_lvl_0_survey.yaml.

    steps:

      - id: query_ncei
        op: query_ncei_data
        checkpoint: always
        params:
          file_time_start: ${inputs.file_time_start}
          file_time_end: ${inputs.file_time_end}
          filters:
            CRUISE_NAME: ${inputs.cruise_name}

      # One instance per file, so an interrupted scan resumes where it stopped.
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

      # Loads the mapping the survey run cached, so the scan and the two steps
      # after it are skipped and no calibration file is read from disk.
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
          # Keeps unmatched calibration files, to help when a channel is unmapped.
          keep_unused: true
          conflict_resolution: "error"
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb). They use variables earlier levels define, so run them from the full notebook.

    #### Step: `query_ncei`
    **Op:** `query_ncei_data`

    Query an NCEI acoustic data catalog for file listings within a time window. Returns a list of result records (file metadata), not the files themselves.

    **Parameters:**
    - `file_time_start` str - ISO 8601 start of window, e.g. '2016-07-25T20:58'
    - `file_time_end` str - ISO 8601 end of window
    - `filters` dict - Key-value filters applied to NCEI metadata (e.g. CRUISE_NAME)
    - `file_names` list - Keep only these files, matched on the D{date}-T{time} stamp in the name, so a bare D20160725-T205832.raw finds the archive's HB1603_L1- form and the reverse. Applied after the time window, client side, in catalogue order. A name the query did not return raises rather than silently shrinking the result. Wire it from a planning step, for example ${plan_dives.raw_files}, to map a per-file chain over an analysis's files alone. Empty keeps everything.

    ```python
    # --- Parameters ---
    _query_ncei__file_time_start = '2016-06-01T00:00:00'
    _query_ncei__file_time_end = '2016-09-01T00:00:00'

    _result = query_ncei_data(
        file_time_start=_query_ncei__file_time_start,
        file_time_end=_query_ncei__file_time_end,
        filters={'CRUISE_NAME': 'HB1603'},
    )
    ncei_results = _result['records']
    query_label = _result['query_label']
    raw_urls = _result['raw_urls']
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
    for _item in raw_urls:
        _iter_file_config = read_raw_file_config(
        raw_file_path=_item,
        verbose=False,
        max_scan_bytes=1048576,
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
    _record_raw_configs__output_base = './outputs/calibration'

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
    _standardize_cal__cal_input_folder = '../calibration_files/HB201607_cal'
    _standardize_cal__output_base = './outputs/calibration'
    _standardize_cal__cruise_id = 'HB1603'
    _standardize_cal__record_author = '<Your Name>'
    _standardize_cal__short_filenames = True

    _result = standardize_calibration_files(
        frequencies=frequencies,
        cal_input_folder=_standardize_cal__cal_input_folder,
        output_base=_standardize_cal__output_base,
        cruise_id=_standardize_cal__cruise_id,
        record_author=_standardize_cal__record_author,
        short_filenames=_standardize_cal__short_filenames,
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
    _build_cal_mapping__output_base = './outputs/calibration'
    _build_cal_mapping__single_channel_dir = single_channel_dir
    _build_cal_mapping__raw_configs_path = raw_configs_path
    _build_cal_mapping__short_filenames = True
    _build_cal_mapping__keep_unused = True
    _build_cal_mapping__conflict_resolution = 'error'

    _result = build_calibration_mapping(
        raw_file_configs=raw_file_configs,
        output_base=_build_cal_mapping__output_base,
        single_channel_dir=_build_cal_mapping__single_channel_dir,
        raw_configs_path=_build_cal_mapping__raw_configs_path,
        short_filenames=_build_cal_mapping__short_filenames,
        tolerances={'transmit_power': 1001.0},
        keep_unused=_build_cal_mapping__keep_unused,
        conflict_resolution=_build_cal_mapping__conflict_resolution,
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
