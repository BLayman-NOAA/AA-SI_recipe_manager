# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the Stage 7 tiered [user, survey] cache and curation write path.

The centerpiece is the curated pre-warm scenario: a curated run populates a
shared survey cache prefix; a later user run hits it automatically (callables
not invoked), computes only what the curator didn't, stores its own work in
the user tier, and never writes the survey prefix. ``memory://`` stands in
for ``gs://`` (same remote code paths, no credentials).
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
    compute_step_hashes,
)
from aa_recipe_manager.executor.sequential import MANIFEST_FILENAME

from test_executor import (  # noqa: F401  (helper scaffolding)
    _dep,
    _linear_inc_dag,
    _linear_multiply_dag,
    _make_dag,
    _meta_names,
    _sink_after_chain_dag,
    helper_module,
)
from test_executor import (
    _HELPER_MODULE_NAME,
)
from aa_recipe_manager.model.types import (
    DAGEdge,
    DAGNode,
    Implementation,
    PortDeclaration,
    Spec,
    Step,
)

USER_ROOT = "memory://cache/user"
CURATOR_ROOT = "memory://cache/curator_user"
SURVEY_ROOT = "memory://cache/survey"


@pytest.fixture(autouse=True)
def clear_memory_fs():
    """MemoryFileSystem's store is process-global; isolate each test."""
    fs = fsspec.filesystem("memory")
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]
    yield fs
    fs.store.clear()
    fs.pseudo_dirs[:] = [""]


def _mem_meta_ids(fs, prefix: str) -> set[str]:
    """Step ids with sidecars under a memory:// cache root."""
    ids: set[str] = set()
    for path in fs.glob(f"{prefix}/*/*/meta.json"):
        ids.add(json.loads(fs.cat_file(path).decode("utf-8"))["step_id"])
    return ids


def _mem_snapshot(fs, prefix: str) -> dict[str, bytes]:
    """Byte-exact snapshot of every object under a memory:// prefix."""
    return {path: fs.cat_file(path) for path in sorted(fs.find(prefix))}


def _prefix_inc_dag():
    """The first two steps of ``_linear_inc_dag`` as their own recipe.

    Because step hashes are Merkle over each step's own fingerprint and its
    parents only, ``start`` and ``first`` hash identically here and in the
    full three-step recipe — which is exactly what makes curated pre-warm of
    a recipe *prefix* reusable by full-recipe runs.
    """
    full = _linear_inc_dag()
    nodes = [full.nodes["start"], full.nodes["first"]]
    edges = [e for e in full.edges if e.target_step_id == "first"]
    return _make_dag(nodes, edges)


# ---------------------------------------------------------------------------
# The acceptance scenario: curated pre-warm -> user run draws from survey
# ---------------------------------------------------------------------------


class TestCuratedPreWarm:
    def test_curated_run_populates_survey_not_user(
        self, helper_module, clear_memory_fs
    ):
        prefix = _prefix_inc_dag()
        SequentialExecutor().execute(
            prefix,
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        fs = clear_memory_fs
        assert _mem_meta_ids(fs, "cache/survey") == {"start", "first"}
        assert _mem_meta_ids(fs, "cache/curator_user") == set()

    def test_user_run_hits_survey_and_writes_only_user_tier(
        self, helper_module, clear_memory_fs
    ):
        fs = clear_memory_fs
        # 1) Curated pre-warm of the two-step prefix.
        SequentialExecutor().execute(
            _prefix_inc_dag(),
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        survey_before = _mem_snapshot(fs, "cache/survey")
        helper_module.call_log.clear()

        # 2) A user runs the FULL recipe against a fresh user root.
        result = SequentialExecutor().execute(
            _linear_inc_dag(),
            inputs={"seed": 1},
            output_dir=USER_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            checkpoint_mode="eager",
        )

        # Only the step the curator didn't run is computed.
        assert [c[0] for c in helper_module.call_log] == ["add_one"]
        assert result.executed_steps == ["second"]
        # The resume frontier hits the survey tier; its ancestors are pruned.
        assert result.step_dispositions["first"].disposition == "hit-survey-cache"
        assert result.step_dispositions["first"].tier == "survey"
        assert result.step_dispositions["start"].disposition == "pruned"
        assert result.step_dispositions["second"].disposition == "computed"
        assert result.step_dispositions["second"].tier == "user"
        assert result.outputs["second"]["out"] == 4

        # The survey prefix is byte-for-byte untouched by the user run.
        assert _mem_snapshot(fs, "cache/survey") == survey_before
        # The user's new work landed in the user tier only.
        assert _mem_meta_ids(fs, "cache/user") == {"second"}

    def test_second_user_run_is_nearly_free(self, helper_module, clear_memory_fs):
        SequentialExecutor().execute(
            _prefix_inc_dag(),
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        for _ in range(2):
            result = SequentialExecutor().execute(
                _linear_inc_dag(),
                inputs={"seed": 1},
                output_dir=USER_ROOT,
                survey_cache_dir=SURVEY_ROOT,
                checkpoint_mode="eager",
            )
        # Second user run: terminal step hits the *user* tier this time.
        assert result.executed_steps == []
        assert result.step_dispositions["second"].disposition == "hit-user-cache"
        assert result.outputs["second"]["out"] == 4

    def test_fork_and_extend_reuses_unchanged_upstream(
        self, helper_module, clear_memory_fs
    ):
        fs = clear_memory_fs
        # Curator blesses the full multiply chain (factor=2 on 'scale').
        SequentialExecutor().execute(
            _linear_multiply_dag(),
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        survey_before = _mem_snapshot(fs, "cache/survey")
        helper_module.call_log.clear()

        # A user forks the recipe: only the last step's param changes.
        forked = _linear_multiply_dag().model_copy(deep=True)
        forked.nodes["scale"].step.params["factor"] = 5
        forked.nodes["scale"].resolved_params["factor"] = 5

        result = SequentialExecutor().execute(
            forked,
            inputs={"seed": 1},
            output_dir=USER_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            checkpoint_mode="eager",
        )

        # Unchanged upstream: survey hit at the frontier; changed step computed.
        assert result.step_dispositions["first"].disposition == "hit-survey-cache"
        assert result.executed_steps == ["scale"]
        assert [c[0] for c in helper_module.call_log] == ["multiply"]
        assert result.outputs["scale"]["out"] == 20  # (1+1)*2*5
        assert _mem_snapshot(fs, "cache/survey") == survey_before
        assert _mem_meta_ids(fs, "cache/user") == {"scale"}


# ---------------------------------------------------------------------------
# Markers: user tier only, unconditionally
# ---------------------------------------------------------------------------


class TestMarkers:
    def test_curated_run_writes_marker_to_user_tier_not_survey(
        self, helper_module, clear_memory_fs
    ):
        fs = clear_memory_fs
        SequentialExecutor().execute(
            _sink_after_chain_dag(),
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        # Data checkpoints -> survey; the sink's marker -> curator's USER tier.
        assert _mem_meta_ids(fs, "cache/survey") == {"start", "first"}
        assert _mem_meta_ids(fs, "cache/curator_user") == {"report"}

    def test_survey_marker_is_never_read(self, helper_module, clear_memory_fs):
        """A marker planted in the survey tier must not skip a user's sink —
        the sink's on-disk artifacts were never produced for *this* user."""
        dag = _sink_after_chain_dag()
        # Pre-warm data steps and hand-plant a marker in the SURVEY tier at
        # the exact hash a user run computes.
        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        hashes = compute_step_hashes(dag, {"seed": 1})
        CheckpointManager(SURVEY_ROOT, hashes).save_marker("report")

        helper_module.call_log.clear()
        result = SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=USER_ROOT,  # fresh user root: no marker here
            survey_cache_dir=SURVEY_ROOT,
            checkpoint_mode="eager",
        )
        # The sink executed despite the survey-tier marker.
        assert "report" in result.executed_steps
        assert ("sink_step") in [c[0] for c in helper_module.call_log]

    def test_user_tier_marker_still_skips(self, helper_module, clear_memory_fs):
        dag = _sink_after_chain_dag()
        for _ in range(2):
            result = SequentialExecutor().execute(
                dag,
                inputs={"seed": 1},
                output_dir=USER_ROOT,
                survey_cache_dir=SURVEY_ROOT,
                checkpoint_mode="eager",
            )
        assert result.step_dispositions["report"].disposition == "marker"


# ---------------------------------------------------------------------------
# Curated-run read policy + shared-tier eligibility
# ---------------------------------------------------------------------------


def _container_step_dag():
    """Single step whose output is neither xarray nor JSON-safe -> pickle."""
    spec = Spec(
        op="make_container",
        description="produce an arbitrary object",
        inputs={"value": PortDeclaration(type="int")},
        outputs={"box": PortDeclaration(type="object")},
    )
    impl = Implementation(
        op="make_container",
        key="default",
        callable_path=f"{_HELPER_MODULE_NAME}.make_container",
        dependency=_dep(),
        output_map={"box": "__return__"},
    )
    node = DAGNode(
        step=Step(id="boxer", op="make_container", inputs={"value": "${inputs.seed}"}),
        spec=spec,
        implementation=impl,
    )
    return _make_dag([node], [])


class TestCurationPolicy:
    def test_curated_run_ignores_private_user_cache(
        self, helper_module, clear_memory_fs
    ):
        """A curated run must recompute rather than trust the curator's own
        private cache — otherwise the survey tier is silently under-warmed."""
        dag = _prefix_inc_dag()
        # Curator has a fully warmed PRIVATE cache from a normal run.
        SequentialExecutor().execute(
            dag, inputs={"seed": 1}, output_dir=CURATOR_ROOT, checkpoint_mode="eager"
        )
        helper_module.call_log.clear()

        SequentialExecutor().execute(
            dag,
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        # Everything re-ran (no private-tier hits) and warmed the survey tier.
        assert [c[0] for c in helper_module.call_log] == ["add_one", "add_one"]
        assert _mem_meta_ids(clear_memory_fs, "cache/survey") == {"start", "first"}

    def test_pickle_output_rejected_from_survey_tier(
        self, helper_module, clear_memory_fs
    ):
        fs = clear_memory_fs
        with pytest.raises(ValueError, match="not eligible for the shared"):
            SequentialExecutor().execute(
                _container_step_dag(),
                inputs={"seed": 1},
                output_dir=CURATOR_ROOT,
                survey_cache_dir=SURVEY_ROOT,
                cache_write_tier="survey",
                checkpoint_mode="eager",
            )
        # Rejected before any artifact write: no cache entries or artifacts
        # land in the survey prefix. (The curated run's provenance file is
        # deliberately published at run START — it describes an environment,
        # not artifacts — so it is the one permitted leftover.)
        leftover = [p for p in fs.find("cache/survey") if "/provenance/" not in p]
        assert leftover == []
        assert _mem_meta_ids(fs, "cache/survey") == set()

    def test_pickle_output_fine_in_user_tier(self, tmp_path):
        """The same non-JSON-safe value the survey tier rejects is accepted
        by the user tier (pickle stays a valid private-cache fallback)."""
        hashes = {"s": "abcd1234"}
        payload = {"vals": {3, 4}}  # a set: picklable, not JSON-safe
        user = CheckpointManager(tmp_path / "user", hashes)
        survey = CheckpointManager(tmp_path / "survey", hashes)

        user_store = TieredCheckpointStore(user=user, survey=survey)
        user_store.save("s", {"obj": payload})
        assert user_store.load("s") == {"obj": payload}

        survey_store = TieredCheckpointStore(
            user=user, survey=survey, write_tier="survey"
        )
        with pytest.raises(ValueError, match="not eligible for the shared"):
            survey_store.save("s", {"obj": payload})

    def test_pickle_checkpoint_format_rejected_for_curated_runs(self, tmp_path):
        with pytest.raises(ValueError, match="pickle"):
            SequentialExecutor().execute(
                _linear_inc_dag(),
                inputs={"seed": 1},
                output_dir=USER_ROOT,
                survey_cache_dir=SURVEY_ROOT,
                cache_write_tier="survey",
                checkpoint_format="pickle",
            )

    def test_survey_write_tier_requires_survey_dir(self):
        with pytest.raises(ValueError, match="requires a survey cache root"):
            SequentialExecutor().execute(
                _linear_inc_dag(),
                inputs={"seed": 1},
                output_dir=USER_ROOT,
                cache_write_tier="survey",
            )

    def test_survey_dir_requires_user_output_dir(self):
        with pytest.raises(ValueError, match="requires output_dir"):
            SequentialExecutor().execute(
                _linear_inc_dag(),
                inputs={"seed": 1},
                survey_cache_dir=SURVEY_ROOT,
            )

    def test_unknown_write_tier_rejected(self):
        with pytest.raises(ValueError, match="cache_write_tier"):
            SequentialExecutor().execute(
                _linear_inc_dag(),
                inputs={"seed": 1},
                output_dir=USER_ROOT,
                cache_write_tier="global",
            )


class TestTieredStoreUnit:
    def test_write_tier_survey_requires_survey_manager(self, tmp_path):
        user = CheckpointManager(tmp_path / "user", {"s": "h"})
        with pytest.raises(ValueError, match="survey cache root"):
            TieredCheckpointStore(user=user, survey=None, write_tier="survey")

    def test_read_order_and_memoized_hit_tier(self, tmp_path):
        hashes = {"s": "abcd1234"}
        user = CheckpointManager(tmp_path / "user", hashes)
        survey = CheckpointManager(tmp_path / "survey", hashes)
        survey.save("s", {"v": {"k": 1}})

        store = TieredCheckpointStore(user=user, survey=survey)
        assert store.has_checkpoint("s")
        assert store.hit_tier("s") == "survey"
        assert store.load("s") == {"v": {"k": 1}}

        # A user-tier entry shadows the survey tier (first hit wins).
        user.save("s", {"v": {"k": 2}})
        fresh = TieredCheckpointStore(user=user, survey=survey)
        assert fresh.has_checkpoint("s")
        assert fresh.hit_tier("s") == "user"
        assert fresh.load("s") == {"v": {"k": 2}}

    def test_save_memoizes_write_tier_for_reload(self, tmp_path):
        hashes = {"s": "abcd1234"}
        user = CheckpointManager(tmp_path / "user", hashes)
        store = TieredCheckpointStore(user=user)
        store.save("s", {"v": {"k": 3}})
        assert store.hit_tier("s") == "user"
        assert store.load("s") == {"v": {"k": 3}}


# ---------------------------------------------------------------------------
# Run manifest
# ---------------------------------------------------------------------------


class TestManifest:
    def test_manifest_records_dispositions_and_artifacts(
        self, helper_module, tmp_path, clear_memory_fs
    ):
        # Curated pre-warm, then a local-user run with a survey tier.
        SequentialExecutor().execute(
            _prefix_inc_dag(),
            inputs={"seed": 1},
            output_dir=CURATOR_ROOT,
            survey_cache_dir=SURVEY_ROOT,
            cache_write_tier="survey",
            checkpoint_mode="eager",
        )
        user_cache = tmp_path / "cache"
        result = SequentialExecutor().execute(
            _linear_inc_dag(),
            inputs={"seed": 1},
            output_dir=user_cache,
            survey_cache_dir=SURVEY_ROOT,
            checkpoint_mode="eager",
        )

        manifest_path = tmp_path / "outputs" / MANIFEST_FILENAME
        assert result.manifest_file == manifest_path
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert manifest["schema_version"] == 1
        assert manifest["run_id"] == result.run_id
        assert manifest["status"] == "completed"
        assert manifest["write_tier"] == "user"
        assert manifest["recipe"] == {"name": "stage6_pipeline", "version": "1.0.0"}
        assert set(manifest["tiers"]) == {"user", "survey"}
        assert manifest["tiers"]["survey"] == SURVEY_ROOT

        steps = manifest["steps"]
        assert steps["start"]["disposition"] == "pruned"
        assert steps["first"]["disposition"] == "hit-survey-cache"
        assert steps["second"]["disposition"] == "computed"
        # Survey-tier artifact URIs point into the survey prefix; the user's
        # computed artifact exists on local disk.
        assert all(
            uri.startswith(SURVEY_ROOT)
            for uri in steps["first"]["artifacts"].values()
        )
        for uri in steps["second"]["artifacts"].values():
            assert Path(uri).exists()

    def test_failed_run_writes_manifest_with_status_failed(
        self, helper_module, tmp_path
    ):
        from aa_recipe_manager.exceptions import PipelineExecutionError

        dag = _linear_inc_dag()
        boom_impl = dag.nodes["second"].implementation.model_copy(
            update={"callable_path": f"{_HELPER_MODULE_NAME}.boom"}
        )
        dag.nodes["second"].implementation = boom_impl
        dag.nodes["second"].step.inputs["value"] = "${first.out}"

        with pytest.raises(PipelineExecutionError):
            SequentialExecutor().execute(
                dag,
                inputs={"seed": 1},
                output_dir=tmp_path / "cache",
                checkpoint_mode="eager",
            )
        manifest = json.loads(
            (tmp_path / "outputs" / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        assert manifest["status"] == "failed"
        # Steps that finished before the failure are still recorded.
        assert manifest["steps"]["start"]["disposition"] == "computed"
        assert "second" not in manifest["steps"]
