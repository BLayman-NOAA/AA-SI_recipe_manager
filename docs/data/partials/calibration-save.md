=== "Flow diagram"

    <div class="recipe-viewer" data-recipe="calibration-save" data-slug="calibration-save" markdown>

    - `save_calibration`: [`save_calibration_archive`](../ops/save_calibration_archive.md)

    </div>

=== "YAML"

    [Download `save_calibration.yaml`](../recipes/calibration/save_calibration.yaml)

    ```yaml
    # Documentation copy of AA-SI_Full_Pipeline_Example/example_recipes/RL2307/save_calibration.yaml

    # RL2307 calibration, phase 3: archive the finished calibration.
    # Runs after calibration_standardize.yaml and calibration_mapping.yaml and
    # writes what a survey keeps:
    #
    #   <archive_dir>/channel_mapping.yaml
    #   <archive_dir>/calibration_provenance.yaml
    #   <archive_dir>/Standardized_Reports/     one file per standardized channel
    #
    # It shares no steps with the first two phases, so it is outside their
    # cache-sharing contract.
    #
    #   LOCAL      Set only archive_dir. The step reads the folder phase 2 wrote
    #              under outputs/calibration/, so keep this recipe's run config
    #              identical to the other two.
    #   A CALLER   Pass mapping_dict, provenance and single_channel_data from
    #              phase 2's outputs; nothing is read from disk.
    #
    # A destination that is not empty is refused unless overwrite is true.
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
        description: "Folder the calibration archive is written to, a local path or a gs:// URL."
      # calibration_outputs is only consulted when an archive input is left unset.
      calibration_outputs:
        type: str
        default: "calibration"
        description: >
          Subfolder of the outputs folder the earlier calibration phases wrote to.
          Keep it the same as in those phase recipes.
      output_base:
        type: path
        required: false
        description: >
          Calibration outputs folder to read unset inputs from. Defaults to this
          run's own; set it to archive a run made elsewhere.
      overwrite:
        type: bool
        default: false
        description: >
          Replace an archive already in archive_dir, clearing
          Standardized_Reports/ first. False stops the run when the destination is
          not empty.
      single_channel_data:
        type: dict
        required: false
        description: >
          Phase 2's single_channel_data output, the standardized channels the
          mapping was built from. Read from the outputs folder when unset.
      mapping_dict:
        type: dict
        required: false
        description: >
          Phase 2's mapping_dict output: each raw file's channels mapped to their
          calibration key. Read from the outputs folder when unset.
      provenance:
        type: dict
        required: false
        description: "Phase 2's provenance output. Read from the outputs folder when unset."

    steps:

      # regenerate: always: the archive lives outside the run's outputs directory,
      # where the executor cannot check it is still there.
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

=== "Python"

    The function calls this recipe makes, with a comment per step. `aa-recipe generate` writes them as a notebook: [download it](../data/notebooks/calibration-save.ipynb).

    ```python
    from aa_si_calibration.archive import save_calibration_archive

    # Inputs
    single_channel_data = None  # TODO: set this value  # dict
    mapping_dict = None  # TODO: set this value  # dict
    provenance = None  # TODO: set this value  # dict

    # save_calibration: Write the finished calibration outputs (channel mapping,
    # provenance and one file per standardized channel) to a directory the survey
    # keeps, local or remote.
    _result = save_calibration_archive(
        single_channel_data=single_channel_data,
        mapping_dict=mapping_dict,
        provenance=provenance,
        archive_dir='${inputs.archive_dir}',
        output_base='${inputs.output_base}',
        calibration_outputs='calibration',
        overwrite=False,
    )
    archive_dir = _result['archive_dir']
    mapping_path = _result['mapping_path']
    provenance_path = _result['provenance_path']
    reports_dir = _result['reports_dir']
    channel_count = _result['channel_count']
    files_written = _result['files_written']
    ```
