# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""A run borrows process-global state and must give it back.

Under the CLI the process belongs to the run, so leaking a headless matplotlib
backend or a mutated environment costs nothing. Called in-process through
``api.execute`` (a notebook, a long-lived service) both are the caller's, and
both used to survive the run.
"""

from __future__ import annotations

import os
import sys
import threading
import time

import pytest

from aa_recipe_manager.executor.engine.mplbackend import preserve_backend


@pytest.fixture(autouse=True)
def _restore_process_backend():
    """These tests move the process-global backend; hand it back to the rest of
    the suite so ordering never matters."""
    matplotlib = sys.modules.get("matplotlib")
    saved = (
        dict.__getitem__(matplotlib.rcParams, "backend")
        if matplotlib is not None
        else None
    )
    yield
    matplotlib = sys.modules.get("matplotlib")
    if matplotlib is not None and saved is not None:
        dict.__setitem__(matplotlib.rcParams, "backend", saved)


class TestPreserveMatplotlibBackend:
    def test_no_op_when_matplotlib_is_not_imported(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "matplotlib", raising=False)
        with preserve_backend():
            pass
        assert "matplotlib" not in sys.modules

    def test_restores_a_backend_the_caller_had_selected(self):
        matplotlib = pytest.importorskip("matplotlib")
        matplotlib.use("Agg", force=True)
        # Stand in for a notebook's inline backend: any non-Agg name the
        # caller deliberately chose and expects to still have afterwards.
        caller_backend = "pdf"
        matplotlib.use(caller_backend, force=True)

        with preserve_backend():
            matplotlib.use("Agg", force=True)
            assert matplotlib.get_backend().lower() == "agg"

        assert matplotlib.get_backend().lower() == caller_backend

    def test_hands_back_auto_selection_when_matplotlib_arrives_mid_run(self):
        """A plotting op imports matplotlib during the run and forces Agg. With
        no caller setting to restore, the auto sentinel goes back so the next
        plot selects normally instead of silently inheriting Agg."""
        matplotlib = pytest.importorskip("matplotlib")
        sentinel = matplotlib.rcParamsDefault["backend"]

        # Enter as though matplotlib were absent, exit with it present.
        saved = sys.modules.pop("matplotlib")
        try:
            cm = preserve_backend()
            cm.__enter__()
        finally:
            sys.modules["matplotlib"] = saved

        matplotlib.use("Agg", force=True)
        cm.__exit__(None, None, None)

        assert dict.__getitem__(matplotlib.rcParams, "backend") is sentinel

    def test_restores_even_when_the_run_raises(self):
        matplotlib = pytest.importorskip("matplotlib")
        matplotlib.use("pdf", force=True)
        with pytest.raises(RuntimeError):
            with preserve_backend():
                matplotlib.use("Agg", force=True)
                raise RuntimeError("step failed")
        assert matplotlib.get_backend().lower() == "pdf"

    def test_a_failing_step_still_raises_when_there_is_nothing_to_restore(self):
        """The guard must be invisible to a failing run on *every* path, not
        just the one that restores. An early ``return`` in its ``finally``
        would silently swallow the step's error."""
        matplotlib = pytest.importorskip("matplotlib")

        # Path 1: matplotlib not imported at all.
        saved = sys.modules.pop("matplotlib")
        try:
            with pytest.raises(RuntimeError, match="no matplotlib"):
                with preserve_backend():
                    raise RuntimeError("no matplotlib")
        finally:
            sys.modules["matplotlib"] = saved

        # Path 2: imported, but the backend never changed.
        matplotlib.use("pdf", force=True)
        with pytest.raises(RuntimeError, match="unchanged"):
            with preserve_backend():
                raise RuntimeError("unchanged")

    def test_reading_the_backend_does_not_resolve_the_auto_sentinel(self):
        """``matplotlib.get_backend()`` resolves the sentinel as a side effect,
        which can initialize a GUI toolkit. The guard must not trigger that."""
        matplotlib = pytest.importorskip("matplotlib")
        sentinel = matplotlib.rcParamsDefault["backend"]
        # matplotlib.use() and rcParams both ignore the sentinel once a backend
        # is set, so put the unresolved state back the same raw way the guard
        # restores it.
        dict.__setitem__(matplotlib.rcParams, "backend", sentinel)

        with preserve_backend():
            pass

        assert dict.__getitem__(matplotlib.rcParams, "backend") is sentinel


class TestWorkerProcessDetection:
    """``_dispatch`` sets MPLBACKEND for a spawned worker. Doing that for a
    thread worker mutates the caller's own environment, permanently."""

    def _wctx(self, **overrides):
        from aa_recipe_manager.executor.engine.context import WorkerContext

        base = dict(
            dag=None,
            pipeline_inputs={},
            step_hashes={},
            payloads={},
            run_id="r1",
            cache_root=None,
            survey_root=None,
            write_tier="user",
            checkpoint_format="zarr",
            user_cache_dir=None,
            outputs_dir=None,
            temp_dir=None,
            storage_options=None,
            recipe_info={},
        )
        base.update(overrides)
        return WorkerContext(**base)

    def test_context_built_here_reports_the_client_process(self):
        assert self._wctx().in_client_process() is True

    def test_a_different_pid_reads_as_a_worker_process(self):
        assert self._wctx(client_pid=os.getpid() + 1).in_client_process() is False

    @pytest.mark.parametrize("backend", ["dask", "prefect"])
    def test_thread_worker_leaves_the_callers_environment_alone(
        self, backend, monkeypatch
    ):
        import importlib

        module = importlib.import_module(
            f"aa_recipe_manager.executor.engine.backends.{backend}"
        )
        monkeypatch.delenv("MPLBACKEND", raising=False)
        wctx = self._wctx()  # same PID: a thread in the caller's process

        with pytest.raises(Exception):
            # Fails inside run_step_task on the None dag; the env write we care
            # about happens before that, so its absence is the assertion.
            module._dispatch(object(), wctx)
        assert "MPLBACKEND" not in os.environ


class TestStoreBuildIsSerialized:
    """Every task under a threaded backend shares one WorkerContext. Two stores
    would each keep their own hit-tier map, and the losing one would take its
    step's tier with it into the manifest."""

    def test_concurrent_open_store_returns_one_shared_instance(
        self, tmp_path, monkeypatch
    ):
        from aa_recipe_manager.executor.engine import context as ctx_mod
        from aa_recipe_manager.executor.engine.context import WorkerContext

        # Hold the build window open so the race is exercised every run rather
        # than only when the threads happen to interleave.
        real_init = ctx_mod.CheckpointManager.__init__

        def slow_init(self, *args, **kwargs):
            time.sleep(0.05)
            real_init(self, *args, **kwargs)

        monkeypatch.setattr(ctx_mod.CheckpointManager, "__init__", slow_init)

        wctx = WorkerContext(
            dag=None,
            pipeline_inputs={},
            step_hashes={},
            payloads={},
            run_id="r1",
            cache_root=str(tmp_path / "cache"),
            survey_root=None,
            write_tier="user",
            checkpoint_format="zarr",
            user_cache_dir=None,
            outputs_dir=None,
            temp_dir=None,
            storage_options=None,
            recipe_info={},
        )
        n = 8
        barrier = threading.Barrier(n)
        seen: list[object] = []
        lock = threading.Lock()

        def worker() -> None:
            barrier.wait(timeout=5)
            store = wctx.open_store()
            with lock:
                seen.append(store)

        threads = [threading.Thread(target=worker) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert len(seen) == n
        assert all(s is seen[0] for s in seen)
