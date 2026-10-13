=== "Flow diagram"

    <div class="recipe-flow" data-recipe="calibration-save" markdown>

    - `save_calibration`: [`save_calibration_archive`](../ops/save_calibration_archive.md)

    </div>

=== "YAML"

    [Download `save_calibration.yaml`](../recipes/calibration/save_calibration.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/RL2307/save_calibration.yaml

    # RL2307 Calibration, phase 3: archive the finished calibration
    #
    # Runs after calibration_standardize.yaml and calibration_mapping.yaml, and
    # writes what a survey keeps into a directory of the user's choosing:
    #
    #   <archive_dir>/channel_mapping.yaml
    #   <archive_dir>/calibration_provenance.yaml
    #   <archive_dir>/Standardized_Reports/     one file per standardized channel,
    #                                           the same files phase 1 writes to
    #                                           single_channel_calibration_files/
    #
    # It shares no steps with the first two phases, so nothing here participates in
    # the cache-sharing contract those two keep with each other.
    #
    # Two ways in, and they do not interfere:
    #
    #   LOCAL      Run it after phase 2 with nothing but archive_dir. Every data
    #              input is unset, so the step reads the folder phase 2 just wrote
    #              under outputs/calibration/. Keeping this recipe's run config
    #              identical to the other two is what makes that folder findable.
    #
    #   A CALLER   Pass mapping_dict, provenance and single_channel_data back from
    #              phase 2's outputs, and point archive_dir at a bucket. Nothing is
    #              read from disk, which is the whole point: the container the
    #              phases ran in has no folder left to read.
    #
    # The destination is refused when it is not empty. An archive is written once
    # and kept, so replacing one is a decision, not a default: pass overwrite=true
    # to make it.
    #
    # Usage:
    #   aa-recipe run save_calibration.yaml --input archive_dir=./archive
    #   aa-recipe run save_calibration.yaml --input archive_dir=gs://bucket/RL2307/calibration

    recipe:
      name: "rl2307_save_calibration"
      description: "Archive the finished RL2307 calibration outputs to a user-specified directory."
      version: "1.0"
      author: "<Your Name>"
      schema_version: "1"

    inputs:
      archive_dir:
        type: path
        required: true
        description: >
          Directory the calibration is archived into. A local path, or a gs:// URL
          for a remote client that wants the outputs kept on the bucket.
      calibration_outputs:
        type: str
        default: "calibration"
        description: >
          Subdirectory under the run's outputs folder the earlier phases wrote to.
          Only consulted when an input below is left unset. Keep it the same as in
          the other two phase recipes.
      output_base:
        type: path
        required: false
        description: >
          Calibration outputs folder to read anything left unset from. Defaults to
          the folder this run's own outputs directory holds, which is what a local
          run straight after phase 2 wants. Set it to archive the outputs of a run
          that happened somewhere else.
      overwrite:
        type: bool
        default: false
        description: "Replace an archive already in archive_dir, clearing Standardized_Reports/ first."
      single_channel_data:
        type: dict
        required: false
        description: >
          The standardized channels the mapping was built from, as returned on
          either phase's single_channel_data output. Read from the outputs folder
          when unset.
      mapping_dict:
        type: dict
        required: false
        description: >
          Each raw file's channels mapped to their calibration key, from phase 2's
          mapping_dict output. Read from the outputs folder when unset.
      provenance:
        type: dict
        required: false
        description: >
          Phase 2's provenance output. Read from the outputs folder when unset.

    steps:

      # regenerate: always because the archive lives outside the run's outputs
      # directory, where the executor cannot check whether it is still there. A
      # cache hit would hand back the paths without writing anything.
      - id: save_calibration
        op: save_calibration_archive
        checkpoint: never
        regenerate: always
        inputs:
          single_channel_data: ${inputs.single_channel_data}
          mapping_dict: ${inputs.mapping_dict}
          provenance: ${inputs.provenance}
        params:
          archive_dir: ${inputs.archive_dir}
          output_base: ${inputs.output_base}
          calibration_outputs: ${inputs.calibration_outputs}
          overwrite: ${inputs.overwrite}

    outputs:
      archive_dir:
        step_id: save_calibration
        output_name: archive_dir
        description: "The directory the calibration was archived to."
      mapping_path:
        step_id: save_calibration
        output_name: mapping_path
        description: "Full path of the archived channel_mapping.yaml."
      provenance_path:
        step_id: save_calibration
        output_name: provenance_path
        description: >
          Full path of the archived calibration_provenance.yaml. Unlike the mapping
          recipe's output of the same name, this one outlives the run.
      reports_dir:
        step_id: save_calibration
        output_name: reports_dir
        description: "The Standardized_Reports folder holding the channel files."
      channel_count:
        step_id: save_calibration
        output_name: channel_count
        description: "How many standardized channel files were archived."
      files_written:
        step_id: save_calibration
        output_name: files_written
        description: >
          Every file the archive holds, in write order, so a caller can report the
          archive without listing the destination itself.
    ```

=== "Notebook"

    [Download the notebook](../data/notebooks/calibration-save.ipynb), as `aa-recipe generate` writes it.

    ### rl2307_save_calibration

    Archive the finished RL2307 calibration outputs to a user-specified directory.

    **Author:** <Your Name>
    **Version:** 1.0

    ```python
    # Uncomment the lines below to install required packages.
    # %pip install git+https://github.com/BLayman-NOAA/AA-SI_calibration.git
    ```

    ```python
    from aa_si_calibration.archive import save_calibration_archive
    ```

    ```python
    archive_dir = None  # TODO: set this value  # path
    calibration_outputs = 'calibration'  # str
    output_base = None  # TODO: set this value  # path
    overwrite = False  # bool
    single_channel_data = None  # TODO: set this value  # dict
    mapping_dict = None  # TODO: set this value  # dict
    provenance = None  # TODO: set this value  # dict
    ```

    #### Step: `save_calibration`
    **Op:** `save_calibration_archive`

    Write the finished calibration outputs to a directory the survey keeps.
    Writes channel_mapping.yaml and calibration_provenance.yaml at the top of archive_dir, and one file per standardized channel under Standardized_Reports/ - the same files a local run writes to single_channel_calibration_files/. The channel files are named by each channel's calibration file key, which is what the mapping refers to, so the archive resolves against itself.
    Everything is rendered from the data the standardization and mapping steps return, so a caller that never sees the run's outputs folder can archive what those steps handed it. Omit an input and it is read from the outputs folder of the run in progress instead, which is how a local run archives with nothing but a directory.
    archive_dir may be a remote fsspec URL, which is the point for a client whose run directory disappears when the container does.
    Mark it regenerate: always in a recipe. Its product is files outside the run's outputs directory, where the executor cannot verify them, so a cache hit would return the paths without writing anything.

    **Parameters:**
    - `archive_dir` path - Directory to write the archive into. May be a local path or a remote fsspec URL (e.g. gs://bucket/surveys/RL2307/calibration). Not fingerprinted: it is where this step writes, not something it reads.

    - `output_base` path - Calibration outputs folder to read anything omitted from. Defaults to the folder the run in progress is writing, resolved the way initial_setup resolves it, so a local run after the mapping phase finds the files it just wrote.

    - `calibration_outputs` str - Subdirectory name under the run's outputs folder, used only to build the default output_base. Match the value the earlier phases ran with.

    - `overwrite` bool - Replace an archive already in archive_dir. Off by default: an archive directory is written once and kept, so a non-empty destination stops the run rather than overwriting it. When on, Standardized_Reports/ is cleared first, so a channel dropped during review leaves no orphan file behind.

    - `verbose` bool - Print what was written.

    ```python
    # --- Parameters ---
    _save_calibration__archive_dir = '${inputs.archive_dir}'
    _save_calibration__output_base = '${inputs.output_base}'
    _save_calibration__calibration_outputs = 'calibration'
    _save_calibration__overwrite = False

    _result = save_calibration_archive(
        single_channel_data=single_channel_data,
        mapping_dict=mapping_dict,
        provenance=provenance,
        archive_dir=_save_calibration__archive_dir,
        output_base=_save_calibration__output_base,
        calibration_outputs=_save_calibration__calibration_outputs,
        overwrite=_save_calibration__overwrite,
    )
    archive_dir = _result['archive_dir']
    mapping_path = _result['mapping_path']
    provenance_path = _result['provenance_path']
    reports_dir = _result['reports_dir']
    channel_count = _result['channel_count']
    files_written = _result['files_written']
    ```
