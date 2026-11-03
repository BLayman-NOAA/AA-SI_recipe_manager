=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="hb1603-dive-analysis" data-slug="hb1603-dive-plan" data-drill="inc:sub_recipes/dive_plan.yaml" markdown>

    - `plan_dives`: custom `aa_si_utils.dive_profiles.plan_dive_datasets`

    </div>

=== "YAML"

    [Download `dive_plan.yaml`](../recipes/HB1603/sub_recipes/dive_plan.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/HB1603/UC1/sub_recipes/dive_plan.yaml

    # HB1603 dive plan: turn the NEFSC dive configs into recipe windows.
    #
    # Reads no acoustic data. It picks the dives, finds each dive's line files and
    # emits one ping_time window per dive, which drives everything user-level.
    #
    # INCLUDE THIS FIRST
    #
    #   Levels 1 to 3 are one per-file chain, and a non-mapped step declared
    #   between two of them splits it. Declared ahead of level 0, plan_dives is out
    #   of the way.

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
        description: "Folder of the SWD_*.json dive configs."
      evl_root:
        type: path
        default: "./Auxiliary"
        description: "Folder searched recursively for the .evl line files the dive configs name."
      pad_minutes:
        type: float
        default: 0.0
        description: "Minutes added either side of each dive's line span."
      # SWD_20160707-OE20-OE29-OE32 is a combined view of three listed dives with
      # no 99% confidence lines, so it is not a dive of its own. Listing the
      # included set states the population and makes a missing config fail loudly.
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
          Labels of the dives to analyse, one per dive config. The default is every
          localized HB1603 dive except SWD_20160703-OE20, whose dive data is
          incorrect. A label with no matching config stops the run.

    steps:

      # Declared first; see INCLUDE THIS FIRST in the header.
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

=== "Python"

    The function calls this part of the [HB1603 dive analysis notebook](../data/notebooks/hb1603-dive-analysis.ipynb) makes. They use variables earlier steps define, so run them from the full notebook.

    ```python
    from aa_si_utils.dive_profiles import plan_dive_datasets

    # plan_dives: Turn the NEFSC dive configs into recipe windows.
    _result = plan_dive_datasets(
        json_dir='./Auxiliary/JSON_Files',
        evl_root='./Auxiliary',
        pad_minutes=0.0,
        include_labels=['SWD_20160707-OE20', 'SWD_20160707-OE29', 'SWD_20160707-OE32', 'SWD_20160719-OE07', 'SWD_20160725-OE50', 'SWD_20160725-OE54', 'SWD_20160725-OE61', 'SWD_20160725-OE62', 'SWD_20160725-OE64', 'SWD_20160802-OE16', 'SWD_20160814-OE03', 'SWD_20160814-OE09', 'SWD_20160814-OE44'],
    )
    windows = _result['windows']
    raw_files = _result['raw_files']
    ```
