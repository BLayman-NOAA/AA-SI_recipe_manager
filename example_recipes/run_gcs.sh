#!/usr/bin/env bash
../.venv/Scripts/aa-recipe.exe run processing_levels_pipeline_gcs.yaml \
  --output-dir gs://ggn-nmfs-aa-dev-1-data/Test_Layman/HB1603/recipe_cache \
  --temp-dir   gs://ggn-nmfs-aa-dev-1-data/Test_Layman/HB1603/exe_temp \
  --outputs-dir ./outputs "$@"
