=== "Flow diagram"

    <div class="recipe-flow" data-recipe="hb1603-dive-plan" markdown>

    - `plan_dives`: custom `aa_si_utils.dive_profiles.plan_dive_datasets`

    </div>

=== "YAML"

    [Download `dive_plan.yaml`](../recipes/HB1603/sub_recipes/dive_plan.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/dive_plan.yaml

    # HB1603 dive plan: turn the NEFSC dive configs into recipe windows.
    #
    # Not a processing level: it reads no acoustic data. It decides which dives the
    # analysis covers, where each dive's line files are, and the ping_time window
    # each one spans. Everything user-level below is driven by its windows list.
    #
    # INCLUDE THIS FIRST
    #
    #   plan_dives depends only on inputs, so it can sit anywhere in the DAG, but
    #   not anywhere in the file. Levels 1 to 3 form one per-file chain mapped over
    #   the catalogue, and a non-mapped step declared between two of them splits
    #   that chain. Declared ahead of level 0 it is out of the way.

    recipe:
      name: "hb1603_dive_plan"
      description: "Read the NEFSC dive configs and emit one window per dive."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      json_dir:
        type: path
        default: "./Auxiliary/JSON_Files"
        description: "Folder of SWD_*.json dive configs."
      evl_root:
        type: path
        default: "./Auxiliary"
        description: "Searched recursively for the .evl files the configs name."
      pad_minutes:
        type: float
        default: 0.0
        description: "Minutes added either side of each dive's line span."
      dive_labels:
        type: list
        default:
          - "SWD_20160707-OE20"
          - "SWD_20160707-OE29"
          - "SWD_20160707-OE32"
          - "SWD_20160719-OE07"
          - "SWD_20160725-OE50"
          - "SWD_20160725-OE54"
          - "SWD_20160725-OE61"
          - "SWD_20160725-OE62"
          - "SWD_20160725-OE64"
          - "SWD_20160802-OE16"
          - "SWD_20160814-OE03"
          - "SWD_20160814-OE09"
          - "SWD_20160814-OE44"
        description: >
          The 13 dives in the analysis: every localized dive HB1603 recorded except
          SWD_20160703-OE20, whose dive data is incorrect.

          Spelled as the set that is included rather than the one excluded, which
          is what plan_dive_datasets accepts and is also the more useful record. It
          states the analysis population outright, and plan_dive_datasets raises if
          a label names a dive no config provides, so a renamed or missing config
          stops the run instead of quietly shrinking the study.

          SWD_20160707-OE20-OE29-OE32 is absent because it is not a fourteenth
          dive. It is a combined view of three July 7 dives that each have their
          own config, and it carries no 99% confidence lines, so it cannot drive
          the coding step. plan_dive_datasets skips it by default.

    steps:

      # Declared first: it depends only on inputs, and anything non-mapped that
      # lands between the survey include and dive_sv would split the per-file
      # chain (see the header).
      - id: plan_dives
        op: custom
        checkpoint: always
        params:
          json_dir: ${inputs.json_dir}
          evl_root: ${inputs.evl_root}
          pad_minutes: ${inputs.pad_minutes}
          include_labels: ${inputs.dive_labels}
        custom_spec:
          description: Turn the NEFSC dive configs into recipe windows.
          callable_path: aa_si_utils.dive_profiles.plan_dive_datasets
          cache_key: dive_profiles_plan_dive_datasets
          version: "1"
          params:
            json_dir: {type: path}
            evl_root: {type: path}
            pad_minutes: {type: float}
            include_labels: {type: list}
          outputs:
            windows: {type: list}
            raw_files: {type: list}
          output_map:
            windows: "['windows']"
            raw_files: "['raw_files']"
          dependency:
            name: aa-si-utils
            version: ">=0.1.0"
            source: git
            url: https://github.com/BLayman-NOAA/AA-SI_Utils.git
    ```

=== "Notebook"

    These are the cells that run this level, taken from the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb). They use variables earlier levels define, so run them from the full notebook.

    #### Step: `plan_dives`
    **Op:** `custom` (custom / unregistered)

    Turn the NEFSC dive configs into recipe windows.

    **Parameters:**
    - `json_dir` path
    - `evl_root` path
    - `pad_minutes` float
    - `include_labels` list

    ```python
    # --- Parameters ---
    _plan_dives__json_dir = './Auxiliary/JSON_Files'
    _plan_dives__evl_root = './Auxiliary'
    _plan_dives__pad_minutes = 0.0
    _plan_dives__include_labels = ['SWD_20160707-OE20', 'SWD_20160707-OE29', 'SWD_20160707-OE32', 'SWD_20160719-OE07', 'SWD_20160725-OE50', 'SWD_20160725-OE54', 'SWD_20160725-OE61', 'SWD_20160725-OE62', 'SWD_20160725-OE64', 'SWD_20160802-OE16', 'SWD_20160814-OE03', 'SWD_20160814-OE09', 'SWD_20160814-OE44']

    _result = plan_dive_datasets(
        json_dir=_plan_dives__json_dir,
        evl_root=_plan_dives__evl_root,
        pad_minutes=_plan_dives__pad_minutes,
        include_labels=_plan_dives__include_labels,
    )
    windows = _result['windows']
    raw_files = _result['raw_files']
    ```
