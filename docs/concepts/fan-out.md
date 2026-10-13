# Per-file fan-out

A survey is hundreds or thousands of raw files. Most early processing works on
one file at a time, so a recipe can run a step **once per file** and gather the
results back together later. Each file then runs, and is cached, on its own:
an interrupted run resumes from the files it finished, a file added to the
survey later runs only for that file, and the Dask executor can run many files
at once.

## map_over

`map_over` names a list. The step runs once per element, and `${_item}` is the
current element:

```yaml
- id: read_raw
  op: read_raw_files
  map_over: ${initial_setup.raw_file_paths}
  inputs:
    raw_file_paths:
      - ${_item}          # a one-element list: just this file

- id: combine_raw
  op: combine_raw_files
  map_over: ${initial_setup.raw_file_paths}
  inputs:
    raw_stores: ${read_raw.raw_stores}
```

**Consecutive steps mapped over the same list form one chain.** Inside a
chain, `${read_raw.raw_stores}` means this file's stores, not every file's.
Each element runs the whole chain as one unit, in one worker.

A non-mapped step declared between two mapped steps splits the chain, and the
two halves stop sharing a file. Sub-recipes that continue a chain must
therefore be included back to back, which is why the example parents include
levels 1, 2 and 3 one after another.

## collect

`collect` gathers every element's result into a list, for one step that sees
them all:

```yaml
- id: merge_sv
  op: merge_datasets
  collect: ${apply_mask.ds_Sv}
  inputs:
    datasets: ${apply_mask.ds_Sv}
  params:
    dim: "ping_time"
```

`merge_datasets` is the usual collector: it concatenates the per-file datasets
along a dimension. Where to collect is a scientific choice. A step that looks
across pings, such as background noise removal, has to see the merged survey;
steps that work ping by ping can stay per file. The two example surveys make
this choice differently, which their level 2 and 3 pages explain.

## sweep

`sweep` runs one step once per parameter value, to compare settings:

```yaml
- id: run_hdbscan
  op: run_hdbscan
  sweep:
    min_cluster_size_fraction: [0.005, 0.01, 0.02]
```

Several swept parameters pair up element by element (`mode: zip`, the default)
or run every combination (`mode: grid`).

## Scratch files

A per-file chain can write a large intermediate store per file. On a step
with `checkpoint: never`, `dispose_outputs: [raw_stores]` deletes each file's
store as soon as its chain has read it, so the disk holds one store per worker
rather than one per survey file.
