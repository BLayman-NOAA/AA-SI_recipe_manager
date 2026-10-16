=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-level-5" data-drill="inc:sub_recipes/processing_lvl_5_sv_codes.yaml" markdown>

    - `generate_sv_codes`: custom `aa_si_utils.dive_profiles.generate_sv_codes`
    - `sv_code_depth_table`: custom `aa_si_utils.dive_profiles.sv_code_depth_table`

    </div>

=== "YAML"

    [Download `processing_lvl_5_sv_codes.yaml`](../recipes/HB1603/sub_recipes/processing_lvl_5_sv_codes.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/processing_lvl_5_sv_codes.yaml

    # Processing level 5: dive products. SVCodes and the depth-interval tables.
    #
    #   generate_sv_codes -> sv_code_depth_table
    #
    # Projects the clustering back onto each dive as per-ping SVCodes, and builds
    # the NEFSC report's percent-of-dive-duration tables. Overlapping dives are
    # each coded over their shared minutes.
    #
    # Cluster ids change between runs, so read the codes against their per-code
    # Sv in the CSVs, not against an earlier analysis's numbering.

    recipe:
      name: "hb1603_processing_lvl_5_sv_codes"
      description: "Per-dive SVCode CSVs and the depth-interval duration tables."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      clustered:
        type: Dataset
        description: "Dataset with the embedded cluster labels. Wire it to embed_results.ds_normalized."
      dive_windows:
        type: list
        description: "Windows with start and end, one per dive. Wire it to plan_dives.windows."
      ping_time_bin:
        type: str
        default: "10s"
        description: >
          Level 3's ping-time bin, the time each ping adds to its code's duration.
          Wire both levels from one parent input.
      code_output_dir:
        type: path
        default: "./outputs/sv_codes"
        description: "Where the per-dive CSVs and the duration tables are written."
      depth_interval_m:
        type: float
        default: 200.0
        description: "Depth bin of the duration table. 200 m matches the report."

    steps:

      # Writes per-cell CSVs with each cell's 18 and 38 kHz Sv next to its code,
      # plus a per-ping summary. Each ping counts toward the code that dominates
      # between its dive's 99% lines, read per dive from the window's paths.
      - id: generate_sv_codes
        op: custom
        checkpoint: always
        inputs:
          ds: ${inputs.clustered}
        params:
          windows: ${inputs.dive_windows}
          output_dir: ${inputs.code_output_dir}
          dataset_name: "ml_dataset"
          ml_result_name: "hdbscan_results"
          range_var: "depth"
          ping_time_bin: ${inputs.ping_time_bin}
        custom_spec:
          description: Write per-dive SVCode CSVs from the embedded clustering.
          callable_path: aa_si_utils.dive_profiles.generate_sv_codes
          cache_key: dive_profiles_generate_sv_codes
          version: "3"
          inputs:
            ds: {type: Dataset}
          params:
            windows: {type: list}
            output_dir:
              type: path
              constraints:
                must_exist: false
            dataset_name: {type: str}
            ml_result_name: {type: str}
            range_var: {type: str}
            ping_time_bin: {type: str}
          outputs:
            code_csv_paths: {type: list}
            summary_csv_path: {type: path}
            dive_labels: {type: list}
          output_map:
            code_csv_paths: "['code_csv_paths']"
            summary_csv_path: "['summary_csv_path']"
            dive_labels: "['dive_labels']"
          dependency:
            name: aa-si-utils
            version: ">=0.1.0"
            source: git
            url: https://github.com/BLayman-NOAA/AA-SI_Utils.git

      # Percent of dive duration per depth interval and code. Depth comes from the
      # fitted dive profile, not from the cells.
      - id: sv_code_depth_table
        op: custom
        checkpoint: always
        params:
          summary_csv_path: ${generate_sv_codes.summary_csv_path}
          output_dir: ${inputs.code_output_dir}
          depth_interval_m: ${inputs.depth_interval_m}
        custom_spec:
          description: Percent of dive duration per depth interval per code.
          callable_path: aa_si_utils.dive_profiles.sv_code_depth_table
          cache_key: dive_profiles_sv_code_depth_table
          version: "1"
          params:
            summary_csv_path: {type: path}
            output_dir:
              type: path
              constraints:
                must_exist: false
            depth_interval_m: {type: float}
          outputs:
            per_dive_csv_path: {type: path}
            combined_csv_path: {type: path}
            totals_csv_path: {type: path}
          output_map:
            per_dive_csv_path: "['per_dive_csv_path']"
            combined_csv_path: "['combined_csv_path']"
            totals_csv_path: "['totals_csv_path']"
          dependency:
            name: aa-si-utils
            version: ">=0.1.0"
            source: git
            url: https://github.com/BLayman-NOAA/AA-SI_Utils.git
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb). They use variables earlier levels define, so run them from the full notebook.

    #### Step: `generate_sv_codes`
    **Op:** `custom` (custom / unregistered)

    Write per-dive SVCode CSVs from the embedded clustering.

    **Parameters:**
    - `windows` list
    - `output_dir` path
    - `dataset_name` str
    - `ml_result_name` str
    - `range_var` str
    - `ping_time_bin` str

    ```python
    # --- Parameters ---
    _generate_sv_codes__windows = windows
    _generate_sv_codes__output_dir = './outputs/sv_codes'
    _generate_sv_codes__dataset_name = 'ml_dataset'
    _generate_sv_codes__ml_result_name = 'hdbscan_results'
    _generate_sv_codes__range_var = 'depth'
    _generate_sv_codes__ping_time_bin = '10s'

    _result = generate_sv_codes(
        ds=embed_results_ds_normalized,
        windows=_generate_sv_codes__windows,
        output_dir=_generate_sv_codes__output_dir,
        dataset_name=_generate_sv_codes__dataset_name,
        ml_result_name=_generate_sv_codes__ml_result_name,
        range_var=_generate_sv_codes__range_var,
        ping_time_bin=_generate_sv_codes__ping_time_bin,
    )
    code_csv_paths = _result['code_csv_paths']
    summary_csv_path = _result['summary_csv_path']
    dive_labels = _result['dive_labels']
    ```

    #### Step: `sv_code_depth_table`
    **Op:** `custom` (custom / unregistered)

    Percent of dive duration per depth interval per code.

    **Parameters:**
    - `summary_csv_path` path
    - `output_dir` path
    - `depth_interval_m` float

    ```python
    # --- Parameters ---
    _sv_code_depth_table__summary_csv_path = summary_csv_path
    _sv_code_depth_table__output_dir = './outputs/sv_codes'
    _sv_code_depth_table__depth_interval_m = 200.0

    _result = sv_code_depth_table(
        summary_csv_path=_sv_code_depth_table__summary_csv_path,
        output_dir=_sv_code_depth_table__output_dir,
        depth_interval_m=_sv_code_depth_table__depth_interval_m,
    )
    per_dive_csv_path = _result['per_dive_csv_path']
    combined_csv_path = _result['combined_csv_path']
    totals_csv_path = _result['totals_csv_path']
    ```
