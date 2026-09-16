# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Resuming a mapped chain from the last member that has an instance checkpoint.

A chain instance runs its members in order and consults the cache one member at
a time, so a chain whose only checkpointed member is its last one used to re-run
every earlier member on every run even when that last member was a hit for every
instance. On a survey-scale fan-out that is most of the cost of the run. These
tests pin the skip, and pin the guard that gives it up when a skipped member's
output is still read by something.
"""

from __future__ import annotations

import shutil
import sys
import types
from pathlib import Path

import pytest

from aa_recipe_manager import api

MOD = "ar_chain_resume_helpers"
DEP = '{name: pytest, version: ">=7.0", source: pypi}'


@pytest.fixture
def helpers() -> types.ModuleType:
    """Throwaway callables that record which members actually executed."""
    module = types.ModuleType(MOD)
    module.calls = []  # type: ignore[attr-defined]

    def make_items(n: int = 3) -> list[int]:
        return list(range(n))

    def fetch(item: int) -> int:
        module.calls.append(("fetch", item))  # type: ignore[attr-defined]
        return item * 10

    def consume(value: int) -> int:
        module.calls.append(("consume", value))  # type: ignore[attr-defined]
        return value + 1

    def audit_effect(value: int) -> None:
        module.calls.append(("audit_effect", value))  # type: ignore[attr-defined]

    def summarize(value: int) -> int:
        module.calls.append(("summarize", value))  # type: ignore[attr-defined]
        return value * 2

    def total(values: list[int]) -> int:
        module.calls.append(("total", tuple(values)))  # type: ignore[attr-defined]
        return sum(v for v in values if v is not None)

    module.make_items = make_items  # type: ignore[attr-defined]
    module.fetch = fetch  # type: ignore[attr-defined]
    module.consume = consume  # type: ignore[attr-defined]
    module.summarize = summarize  # type: ignore[attr-defined]
    module.audit_effect = audit_effect  # type: ignore[attr-defined]
    module.total = total  # type: ignore[attr-defined]
    sys.modules[MOD] = module
    yield module
    sys.modules.pop(MOD, None)


def _recipe(
    *,
    extra_consumer: bool = False,
    second_checkpoint: bool = False,
    side_effect_member: bool = False,
) -> str:
    """fetch -> consume (checkpointed) -> total.

    ``extra_consumer`` adds a second fan-in reading ``fetch`` directly, which
    must stop the skip: its input has to be computed.
    """
    tail = ""
    middle = ""
    if second_checkpoint:
        # consume is checkpointed AND fanned in from outside, and a second
        # checkpointed member sits below it. This is HB1603's per-file chain:
        # survey_file_mvbs feeds merge_survey_mvbs while survey_cell_stats sits
        # below it, and the skip has to survive that.
        middle = f"""
  - id: summarize
    op: custom
    map_over: ${{seg.items}}
    checkpoint: always
    inputs:
      value: ${{consume.size}}
    custom_spec:
      description: a second checkpointed member below the fanned-in one
      callable_path: {MOD}.summarize
      inputs:
        value: {{type: int}}
      outputs:
        doubled: {{type: int}}
      output_map: {{doubled: __return__}}
      dependency: {DEP}
"""
        tail += f"""
  - id: total_doubled
    op: custom
    collect: ${{summarize.doubled}}
    inputs:
      values: ${{summarize.doubled}}
    custom_spec:
      description: fan in the second checkpointed member
      callable_path: {MOD}.total
      inputs:
        values: {{type: int, many: true}}
      outputs:
        n: {{type: int}}
      output_map: {{n: __return__}}
      dependency: {DEP}
"""
    if side_effect_member:
        # No outputs, so nothing can read it and need propagation alone would
        # drop it. It sits BETWEEN the two cached members so being last is not
        # what saves it.
        middle = f"""
  - id: audit_effect
    op: custom
    map_over: ${{seg.items}}
    inputs:
      value: ${{consume.size}}
    custom_spec:
      description: a side-effect-only member with no outputs
      callable_path: {MOD}.audit_effect
      inputs:
        value: {{type: int}}
      dependency: {DEP}
""" + middle
    if extra_consumer:
        tail = f"""
  - id: audit
    op: custom
    collect: ${{fetch.value}}
    inputs:
      values: ${{fetch.value}}
    custom_spec:
      description: read the skippable member from outside the chain
      callable_path: {MOD}.total
      inputs:
        values: {{type: int, many: true}}
      outputs:
        n: {{type: int}}
      output_map: {{n: __return__}}
      dependency: {DEP}
"""
    return f"""
recipe:
  name: chain_resume
  version: "1.0"
  description: resume a mapped chain from its checkpointed tail
  author: t
  schema_version: "1"
steps:
  - id: seg
    op: custom
    custom_spec:
      description: produce items
      callable_path: {MOD}.make_items
      outputs:
        items: {{type: list}}
      output_map: {{items: __return__}}
      dependency: {DEP}
  - id: fetch
    op: custom
    map_over: ${{seg.items}}
    params:
      item: ${{_item}}
    custom_spec:
      description: the expensive upstream member
      callable_path: {MOD}.fetch
      params:
        item: {{type: int}}
      outputs:
        value: {{type: int}}
      output_map: {{value: __return__}}
      dependency: {DEP}
  - id: consume
    op: custom
    map_over: ${{seg.items}}
    checkpoint: always
    inputs:
      value: ${{fetch.value}}
    custom_spec:
      description: the chain's surviving output
      callable_path: {MOD}.consume
      inputs:
        value: {{type: int}}
      outputs:
        size: {{type: int}}
      output_map: {{size: __return__}}
      dependency: {DEP}
{middle}  - id: total
    op: custom
    collect: ${{consume.size}}
    inputs:
      values: ${{consume.size}}
    custom_spec:
      description: sum sizes
      callable_path: {MOD}.total
      inputs:
        values: {{type: int, many: true}}
      outputs:
        n: {{type: int}}
      output_map: {{n: __return__}}
      dependency: {DEP}
{tail}"""


def _run(tmp_path: Path, text: str):
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(text, encoding="utf-8")
    return api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
    )


def _ran(helpers, step: str) -> int:
    return sum(1 for name, _ in helpers.calls if name == step)


def test_first_run_executes_every_member(tmp_path, helpers):
    result = _run(tmp_path, _recipe())

    assert _ran(helpers, "fetch") == 3
    assert _ran(helpers, "consume") == 3
    assert result.outputs["total"]["n"] == 33


def test_second_run_skips_the_members_below_the_checkpoint(tmp_path, helpers):
    _run(tmp_path, _recipe())
    helpers.calls.clear()

    result = _run(tmp_path, _recipe())

    # consume is a per-instance cache hit, so nothing above it needs to run.
    assert _ran(helpers, "fetch") == 0
    assert _ran(helpers, "consume") == 0
    # The chain really was scheduled: its collector still ran, on cached values.
    assert _ran(helpers, "total") == 1
    assert result.outputs["total"]["n"] == 33


def test_an_outside_consumer_of_a_skippable_member_blocks_the_skip(tmp_path, helpers):
    """``audit`` reads ``fetch`` directly, so ``fetch`` still has to be computed."""
    _run(tmp_path, _recipe(extra_consumer=True))
    helpers.calls.clear()

    result = _run(tmp_path, _recipe(extra_consumer=True))

    assert _ran(helpers, "fetch") == 3
    assert _ran(helpers, "consume") == 0, "the checkpointed member still loads"
    assert result.outputs["audit"]["n"] == 30


def test_force_recomputes_the_whole_chain(tmp_path, helpers):
    _run(tmp_path, _recipe())
    helpers.calls.clear()

    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(_recipe(), encoding="utf-8")
    api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
        force=True,
    )

    assert _ran(helpers, "fetch") == 3
    assert _ran(helpers, "consume") == 3


def test_a_fanned_in_checkpointed_member_still_lets_the_chain_resume(
    tmp_path, helpers
):
    """A cached member read from outside is loaded, not computed.

    The first version of this guard bailed whenever any skippable member had a
    reader outside the chain, without asking whether that member was itself a
    cache hit. On HB1603 survey_file_mvbs is exactly that - checkpointed, and
    fanned in by merge_survey_mvbs - so the frontier never fired and all 3322
    instances re-read their raw file to reach a checkpoint they already had.
    """
    _run(tmp_path, _recipe(second_checkpoint=True))
    helpers.calls.clear()

    result = _run(tmp_path, _recipe(second_checkpoint=True))

    assert _ran(helpers, "fetch") == 0, "the expensive member must not re-run"
    assert _ran(helpers, "consume") == 0
    assert _ran(helpers, "summarize") == 0
    assert result.outputs["total"]["n"] == 33
    assert result.outputs["total_doubled"]["n"] == 66


def test_resume_needs_a_cached_member_to_stop_at(tmp_path, helpers):
    """With nothing cached for the instance, every member is computed."""
    _run(tmp_path, _recipe(second_checkpoint=True))
    helpers.calls.clear()

    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(_recipe(second_checkpoint=True), encoding="utf-8")
    api.execute(
        recipe,
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
        force=True,
    )

    assert _ran(helpers, "fetch") == 3
    assert _ran(helpers, "summarize") == 3


def test_a_mid_chain_gap_resumes_from_each_cached_member_separately(
    tmp_path, helpers
):
    """Need stops at every cache hit, not just the last one.

    ``summarize``'s checkpoint is deleted while ``consume``'s is kept, so the
    chain has to recompute ``summarize`` from a loaded ``consume`` without
    reaching back to ``fetch``.
    """
    _run(tmp_path, _recipe(second_checkpoint=True))
    cache = tmp_path / "cache"
    removed = [p for p in cache.rglob("*") if p.is_dir() and p.name == "summarize"]
    assert removed, "expected a summarize checkpoint directory to drop"
    for path in removed:
        shutil.rmtree(path)
    helpers.calls.clear()

    result = _run(tmp_path, _recipe(second_checkpoint=True))

    assert _ran(helpers, "fetch") == 0, "consume is still a hit, so stop there"
    assert _ran(helpers, "consume") == 0
    assert _ran(helpers, "summarize") == 3, "its checkpoint is gone, so recompute"
    assert result.outputs["total_doubled"]["n"] == 66


def test_a_regenerating_member_is_never_skipped(tmp_path, helpers):
    """``regenerate`` is a step saying that running it writes artifacts.

    ``fetch`` feeds only a cached member, so need propagation alone would skip
    it. The artifacts are the point, so it has to run anyway.

    This covers an uncheckpointed member deliberately. A *checkpointed* chain
    member with a regenerate policy is a plain cache hit in the member loop,
    which has never consulted regenerate - a pre-existing limitation that the
    resume rule neither creates nor can fix from here.
    """
    text = _recipe(second_checkpoint=True).replace(
        """  - id: fetch
    op: custom""",
        """  - id: fetch
    op: custom
    regenerate: always""",
    )
    assert "regenerate: always" in text
    _run(tmp_path, text)
    helpers.calls.clear()

    result = _run(tmp_path, text)

    assert _ran(helpers, "fetch") == 3, "a regenerating member must re-run"
    assert _ran(helpers, "consume") == 0, "its consumer is still a hit"
    assert result.outputs["total"]["n"] == 33


def test_an_unread_branch_inside_the_chain_is_skipped(tmp_path, helpers):
    """A member nothing reads is skipped even though it is not upstream of a hit.

    ``fetch`` feeds ``consume`` (cached) and nothing else, so the whole branch
    above the hit goes, which is the point. This pins that the rule is
    reachability and not a positional frontier.
    """
    _run(tmp_path, _recipe(second_checkpoint=True))
    helpers.calls.clear()

    _run(tmp_path, _recipe(second_checkpoint=True))

    assert sorted(helpers.calls) == [
        ("total", (1, 11, 21)),
        ("total", (2, 22, 42)),
    ], f"only the two collectors should run, got {helpers.calls}"


def test_a_member_with_no_outputs_is_never_skipped(tmp_path, helpers):
    """A step with nothing to read exists only for its side effect.

    Nothing can reference it, so need propagation alone would drop it, and it
    is not the last member either. Sinks are kept for the same reason.
    """
    text = _recipe(second_checkpoint=True, side_effect_member=True)
    _run(tmp_path, text)
    helpers.calls.clear()

    _run(tmp_path, text)

    assert _ran(helpers, "audit_effect") == 3, "a side effect must still happen"
    assert _ran(helpers, "fetch") == 0, "and it must not drag the chain with it"


def test_the_skip_is_the_same_under_the_dask_executor(tmp_path, helpers):
    """The resume rule lives in the chain task, so every backend gets it.

    Worth pinning separately: the survey runs that this was written for use
    ``--executor dask``, and a rule that only held under the inline backend
    would be silently useless there.
    """
    pytest.importorskip("distributed")
    text = _recipe(second_checkpoint=True)
    recipe = tmp_path / "recipe.yaml"
    recipe.write_text(text, encoding="utf-8")
    kwargs = dict(
        user_cache_dir=str(tmp_path / "cache"),
        outputs_dir=str(tmp_path / "out"),
        temp_dir=str(tmp_path / "tmp"),
        executor="dask",
        executor_options={"n_workers": 2},
    )
    api.execute(recipe, **kwargs)
    helpers.calls.clear()

    result = api.execute(recipe, **kwargs)

    assert _ran(helpers, "fetch") == 0
    assert result.outputs["total"]["n"] == 33
    assert result.outputs["total_doubled"]["n"] == 66


def test_a_skipped_member_is_not_reported_as_a_cache_hit(tmp_path, helpers):
    """Provenance has to tell "never needed" apart from "loaded from cache".

    ``fetch`` is not checkpointed at all, so calling it a hit would claim a
    cache entry that does not exist. ``consume`` genuinely was loaded.
    """
    _run(tmp_path, _recipe(second_checkpoint=True))
    helpers.calls.clear()

    result = _run(tmp_path, _recipe(second_checkpoint=True))

    assert result.step_dispositions["fetch"].disposition == "skipped"
    assert result.step_dispositions["consume"].disposition == "hit-user-cache"
    assert result.step_dispositions["summarize"].disposition == "hit-user-cache"
    # Both are "not executed this run", which is what skipped_steps means.
    assert set(result.skipped_steps) >= {"fetch", "consume", "summarize"}


def test_a_skipped_member_still_reports_its_instance_count(tmp_path, helpers):
    """The run log must not claim a skipped member had zero instances.

    Counting skipped members apart from cache hits is what keeps the
    disposition honest, but both summary lines add the counts up, so dropping
    the third state turned "3322 instances, all skipped" into "0 instances".
    """
    _run(tmp_path, _recipe(second_checkpoint=True))
    helpers.calls.clear()

    result = _run(tmp_path, _recipe(second_checkpoint=True))

    line = next(entry for entry in result.logs if entry.startswith("mapped fetch:"))
    assert line == "mapped fetch: 3 instance(s) (0 computed, 0 cached, 3 skipped)"
    hit = next(entry for entry in result.logs if entry.startswith("mapped consume:"))
    assert hit == "mapped consume: 3 instance(s) (0 computed, 3 cached, 0 skipped)"


def test_a_hit_nothing_in_the_chain_reads_is_not_loaded_twice(tmp_path, helpers):
    """The worker only opens a cache hit that a surviving member will read.

    The client reloads every checkpointed member itself when it folds the
    chain in, so a worker-side load exists purely to satisfy an in-chain
    reference. ``summarize`` has none - it is fanned in from outside - and was
    being opened from the store twice per instance and used once.
    """
    from aa_recipe_manager.executor.tiered import TieredCheckpointStore

    loads: list[str] = []
    original = TieredCheckpointStore.load

    def counting_load(self, step_id, **kwargs):
        loads.append(step_id)
        return original(self, step_id, **kwargs)

    text = _recipe(second_checkpoint=True)
    _run(tmp_path, text)
    TieredCheckpointStore.load = counting_load
    try:
        result = _run(tmp_path, text)
    finally:
        TieredCheckpointStore.load = original

    # Three instances, one client-side load each, and no worker-side load.
    assert loads.count("summarize") == 3
    # consume is read by summarize, which is not skipped, so its load stays.
    assert loads.count("consume") == 6
    assert result.outputs["total_doubled"]["n"] == 66
