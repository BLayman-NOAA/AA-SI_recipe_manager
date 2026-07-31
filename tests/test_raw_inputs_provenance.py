# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Raw-input file provenance: harvest, serialization, and cross-tier recovery.

Covers recording the raw files a run read (names + sizes, no folder path) into
provenance, and the survey->user propagation where a user extending a curated
survey cache recovers what raw files produced the cached artifacts.
"""

from __future__ import annotations

import json
from pathlib import Path

import fsspec
import pytest

from aa_recipe_manager.executor import (
    CheckpointManager,
    SequentialExecutor,
    TieredCheckpointStore,
)
from aa_recipe_manager.model.types import (
    DAGNode,
    Dependency,
    Implementation,
    InputDeclaration,
    PipelineDAG,
    PortDeclaration,
    Recipe,
    Spec,
    Step,
)


def _consume(files: list) -> int:
    """A trivial reader stand-in: returns how many raw files it was given."""
    return len(files)
from aa_recipe_manager.provenance.recorder import (
    ProvenanceRecorder,
    build_raw_inputs_record,
    raw_file_list_step_ids,
    to_netcdf_attrs,
    to_yaml,
)

SURVEY_ROOT = "memory://cache/survey"
USER_ROOT = "memory://cache/user"


@pytest.fixture(autouse=True)
def _clear_memory_fs():
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    yield
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]


def _reader_output_dag() -> PipelineDAG:
    """One step whose output port is tagged as the raw file list."""
    spec = Spec(
        op="reader",
        description="",
        outputs={
            "raw_file_paths": PortDeclaration(
                type="list", provenance_role="raw_file_list"
            )
        },
    )
    node = DAGNode(step=Step(id="reader", op="reader"), spec=spec)
    recipe = Recipe(
        name="r", version="1", schema_version="1", steps=[node.step]
    )
    return PipelineDAG(
        recipe=recipe,
        nodes={"reader": node},
        edges=[],
        topological_order=["reader"],
    )


def _pipeline_input_dag() -> PipelineDAG:
    """A recipe that reads raw files straight from a tagged pipeline input."""
    spec = Spec(op="read", description="", inputs={"x": PortDeclaration(type="list")})
    node = DAGNode(
        step=Step(id="read", op="read", inputs={"x": "${inputs.raw_files}"}),
        spec=spec,
    )
    recipe = Recipe(
        name="r",
        version="1",
        schema_version="1",
        steps=[node.step],
        inputs={
            "raw_files": InputDeclaration(
                type="list", provenance_role="raw_file_list"
            )
        },
    )
    return PipelineDAG(
        recipe=recipe,
        nodes={"read": node},
        edges=[],
        topological_order=["read"],
    )


def _write_raw_files(tmp_path) -> list[str]:
    # Intentionally out of name order to exercise sorting; distinct sizes.
    (tmp_path / "b.raw").write_text("bb", encoding="utf-8")  # 2 bytes
    (tmp_path / "a.raw").write_text("alpha", encoding="utf-8")  # 5 bytes
    return [str(tmp_path / "b.raw"), str(tmp_path / "a.raw")]


def test_harvest_from_tagged_output_port(tmp_path):
    paths = _write_raw_files(tmp_path)
    dag = _reader_output_dag()

    record = build_raw_inputs_record(
        dag, {"reader": {"raw_file_paths": paths}}, {}, run_id="run-1"
    )

    assert record is not None
    assert record.count == 2
    # Sorted by basename; sizes resolved; no directory retained.
    assert [(f.name, f.size) for f in record.files] == [("a.raw", 5), ("b.raw", 2)]
    assert record.source == "resolved"
    assert record.origin_run_id == "run-1"
    assert record.producing_step == "reader.raw_file_paths"
    assert record.digest  # non-empty, deterministic
    assert "/" not in record.files[0].name and "\\" not in record.files[0].name


def test_harvest_from_tagged_pipeline_input(tmp_path):
    paths = _write_raw_files(tmp_path)
    dag = _pipeline_input_dag()

    record = build_raw_inputs_record(dag, {}, {"raw_files": paths})

    assert record is not None
    assert record.count == 2
    assert record.producing_step == "pipeline_input:raw_files"


def test_no_tagged_source_yields_none():
    spec = Spec(op="plain", description="", outputs={"out": PortDeclaration(type="int")})
    node = DAGNode(step=Step(id="plain", op="plain"), spec=spec)
    dag = PipelineDAG(
        recipe=Recipe(name="r", version="1", schema_version="1", steps=[node.step]),
        nodes={"plain": node},
        edges=[],
        topological_order=["plain"],
    )
    assert build_raw_inputs_record(dag, {"plain": {"out": 1}}, {}) is None


def test_digest_stable_across_folder_and_order(tmp_path):
    # Same names+sizes under a different folder, listed in a different order,
    # produce the same digest (identity is names+data, not location or order).
    first = _write_raw_files(tmp_path)
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "a.raw").write_text("alpha", encoding="utf-8")
    (other / "b.raw").write_text("bb", encoding="utf-8")
    dag = _reader_output_dag()

    rec_a = build_raw_inputs_record(dag, {"reader": {"raw_file_paths": first}}, {})
    rec_b = build_raw_inputs_record(
        dag,
        {"reader": {"raw_file_paths": [str(other / "a.raw"), str(other / "b.raw")]}},
        {},
    )
    assert rec_a.digest == rec_b.digest


def test_capture_and_serialization(tmp_path):
    paths = _write_raw_files(tmp_path)
    dag = _reader_output_dag()
    record = build_raw_inputs_record(
        dag, {"reader": {"raw_file_paths": paths}}, {}, run_id="run-1"
    )

    prov = ProvenanceRecorder.capture(dag, raw_inputs=record)
    assert prov.raw_inputs is not None
    assert prov.raw_inputs.count == 2

    # YAML carries the file list; NetCDF attrs carry only count + digest.
    yaml_text = to_yaml(prov)
    assert "a.raw" in yaml_text and "raw_inputs" in yaml_text

    attrs = to_netcdf_attrs(prov)
    assert attrs["provenance_raw_input_count"] == "2"
    assert attrs["provenance_raw_inputs_digest"] == record.digest
    assert "a.raw" not in " ".join(attrs.values())  # full list not inlined


def test_survey_raw_inputs_propagate_to_user_provenance():
    # A curated survey run stamps a sidecar with the raw file list; a user run
    # that hits the survey entry recovers it as inherited, with sizes intact and
    # without needing the original raw source.
    hashes = {"reader": "hash-reader"}
    survey_record = {
        "files": [{"name": "a.raw", "size": 5}, {"name": "b.raw", "size": 2}],
        "count": 2,
        "digest": "deadbeef",
        "source": "resolved",
        "producing_step": "reader.raw_file_paths",
        "origin_run_id": "survey-run",
    }
    survey_mgr = CheckpointManager(
        SURVEY_ROOT,
        hashes,
        preferred_format="json",
        run_id="survey-run",
        recipe_info={"name": "r", "version": "1"},
    )
    survey_mgr.save("reader", {"out": 1}, raw_inputs=survey_record)

    # A user run: user tier empty, survey tier read-only, this run's id differs.
    user_store = TieredCheckpointStore(
        user=CheckpointManager(
            USER_ROOT, hashes, preferred_format="json", run_id="user-run"
        ),
        survey=CheckpointManager(
            SURVEY_ROOT, hashes, preferred_format="json", run_id="user-run"
        ),
        write_tier="user",
    )
    assert user_store.has_checkpoint("reader")  # registers the survey hit

    recovered = user_store.recovered_raw_inputs(("reader",))
    assert recovered is not None
    assert recovered["source"] == "inherited"
    assert recovered["origin_run_id"] == "survey-run"
    assert recovered["files"] == survey_record["files"]  # names + sizes intact


def test_recovered_marks_resolved_when_this_run_wrote_it():
    hashes = {"reader": "hash-reader"}
    record = {
        "files": [{"name": "a.raw", "size": 5}],
        "count": 1,
        "digest": "d",
        "source": "resolved",
        "producing_step": "reader.raw_file_paths",
        "origin_run_id": "run-x",
    }
    store = TieredCheckpointStore(
        user=CheckpointManager(
            USER_ROOT, hashes, preferred_format="json", run_id="run-x"
        ),
        write_tier="user",
    )
    store.save("reader", {"out": 1}, raw_inputs=record)  # write sets the hit tier

    recovered = store.recovered_raw_inputs(("reader",))
    assert recovered is not None
    assert recovered["source"] == "resolved"
    assert recovered["origin_run_id"] == "run-x"


def test_raw_file_list_step_ids_finds_tagged_steps():
    dag = _reader_output_dag()
    assert raw_file_list_step_ids(dag) == ("reader",)


def _consume_dag() -> PipelineDAG:
    """Runnable single step reading a tagged raw_files pipeline input."""
    spec = Spec(
        op="consume",
        description="",
        inputs={"files": PortDeclaration(type="list")},
        outputs={"out": PortDeclaration(type="int")},
    )
    impl = Implementation(
        op="consume",
        key="default",
        callable_path="test_raw_inputs_provenance._consume",
        dependency=Dependency(name="pytest", version=">=7.0", source="pypi"),
        output_map={"out": "__return__"},
    )
    node = DAGNode(
        step=Step(id="consume", op="consume", inputs={"files": "${inputs.raw_files}"}),
        spec=spec,
        implementation=impl,
    )
    recipe = Recipe(
        name="consume_pipeline",
        version="1.0.0",
        schema_version="1",
        steps=[node.step],
        inputs={
            "raw_files": InputDeclaration(
                type="list", provenance_role="raw_file_list"
            )
        },
    )
    return PipelineDAG(
        recipe=recipe,
        nodes={"consume": node},
        edges=[],
        topological_order=["consume"],
    )


def test_executor_run_records_and_stamps_raw_inputs(tmp_path):
    # End-to-end: a real run harvests the tagged pipeline input into provenance
    # AND stamps the checkpoint sidecar (task-carry -> save -> meta.json).
    (tmp_path / "a.raw").write_text("alpha", encoding="utf-8")  # 5
    (tmp_path / "b.raw").write_text("bb", encoding="utf-8")  # 2
    paths = [str(tmp_path / "a.raw"), str(tmp_path / "b.raw")]
    cache = tmp_path / "cache"

    result = SequentialExecutor().execute(
        _consume_dag(),
        inputs={"raw_files": paths},
        user_cache_dir=str(cache),
        checkpoint_mode="eager",
    )

    assert result.provenance.raw_inputs is not None
    assert result.provenance.raw_inputs.count == 2
    assert {(f.name, f.size) for f in result.provenance.raw_inputs.files} == {
        ("a.raw", 5),
        ("b.raw", 2),
    }

    # The sidecar this run wrote carries the same list.
    metas = list(cache.glob("**/meta.json"))
    assert metas, "no checkpoint sidecar written"
    stamped = [
        json.loads(Path(m).read_text(encoding="utf-8")) for m in metas
    ]
    consume_meta = next(m for m in stamped if m["step_id"] == "consume")
    assert consume_meta["raw_inputs"]["count"] == 2
