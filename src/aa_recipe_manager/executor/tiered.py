# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""TieredCheckpointStore: ordered-tier cache lookup with one write tier.

Wraps single-root :class:`~aa_recipe_manager.executor.checkpoint.
CheckpointManager` instances behind the same narrow interface the executor
and planner already consume (``has_checkpoint`` / ``has_marker`` / ``load``
/ ``save`` / ``save_marker``), so tiering is invisible to them.

Tier policy (see ``global_cache_plan.md`` §4.2, §9.3):

* **Normal runs** (``write_tier="user"``) read ``[user, survey]`` — first
  hit wins — and write only to the user tier.
* **Curated runs** (``write_tier="survey"``) read ``[survey]`` only (a hit
  in the curator's private cache would silently fail to warm the shared
  tier) and write to the survey tier. Pickle-format artifacts are rejected
  before anything is written: pickles are not portable across environments,
  so they are ineligible for the shared tier.
* **Side-effect markers** are read from and written to the *user* tier
  unconditionally, in both modes: a shared marker would let one user's run
  skip generating plots/exports that another user never received.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from aa_recipe_manager.executor.checkpoint import CheckpointManager

USER_TIER = "user"
SURVEY_TIER = "survey"
CACHE_WRITE_TIERS = (USER_TIER, SURVEY_TIER)


@runtime_checkable
class CheckpointStore(Protocol):
    """The narrow checkpoint interface consumed by the planner and executors."""

    def has_checkpoint(self, step_id: str) -> bool: ...

    def has_marker(self, step_id: str) -> bool: ...

    def load(self, step_id: str) -> dict[str, Any]: ...

    def save(self, step_id: str, outputs: dict[str, Any]) -> None: ...

    def save_marker(self, step_id: str) -> None: ...


class TieredCheckpointStore:
    """Ordered read tiers over content-addressed checkpoint managers.

    Because entries are content-addressed, a curated (survey-tier) entry
    written by one run has exactly the key a later user run computes for the
    same work — the cross-user hit is pure hash coincidence, no linking.
    """

    def __init__(
        self,
        *,
        user: CheckpointManager,
        survey: CheckpointManager | None = None,
        write_tier: str = USER_TIER,
    ) -> None:
        if write_tier not in CACHE_WRITE_TIERS:
            raise ValueError(
                f"unknown cache write tier {write_tier!r}; expected one of "
                f"{CACHE_WRITE_TIERS}"
            )
        if write_tier == SURVEY_TIER and survey is None:
            raise ValueError(
                "cache_write_tier='survey' requires a survey cache root "
                "(set survey_cache_dir in the run config or pass "
                "--survey-cache-dir)"
            )
        self._managers: dict[str, CheckpointManager] = {USER_TIER: user}
        if survey is not None:
            self._managers[SURVEY_TIER] = survey
        self.write_tier = write_tier
        if write_tier == SURVEY_TIER:
            # Curated runs must warm the shared tier, never their private one.
            self._read_order = [SURVEY_TIER]
        else:
            self._read_order = [USER_TIER] + (
                [SURVEY_TIER] if survey is not None else []
            )
        self._hit_tier: dict[str, str] = {}

    # -- introspection (manifest / logging) ----------------------------------

    @property
    def run_id(self) -> str:
        return self._managers[self.write_tier].run_id

    def tier_roots(self) -> dict[str, str]:
        """Tier name -> cache root URL, for the run manifest."""
        return {name: str(mgr.location) for name, mgr in self._managers.items()}

    def hit_tier(self, step_id: str) -> str | None:
        """Which tier served (or received) this step, once known."""
        return self._hit_tier.get(step_id)

    def survey_root(self):
        """The survey tier's cache root location, when configured."""
        survey = self._managers.get(SURVEY_TIER)
        return None if survey is None else survey.location

    def survey_hit_provenance_refs(self) -> set[str]:
        """Provenance refs recorded on the sidecars of survey-tier hits.

        Curated runs publish their provenance next to the survey cache and
        stamp each entry they write with a ``provenance_ref``; collecting the
        distinct refs across this run's survey hits tells us which curated
        environments produced the artifacts we just reused.
        """
        survey = self._managers.get(SURVEY_TIER)
        if survey is None:
            return set()
        refs: set[str] = set()
        for step_id, tier in self._hit_tier.items():
            if tier != SURVEY_TIER:
                continue
            meta = survey.read_meta(step_id)
            if meta is not None and meta.provenance_ref:
                refs.add(meta.provenance_ref)
        return refs

    def artifact_urls(self, step_id: str) -> dict[str, str]:
        """Absolute artifact locations from the step's hit/write tier."""
        manager = self._manager_for(step_id)
        return {} if manager is None else manager.artifact_urls(step_id)

    def _manager_for(self, step_id: str) -> CheckpointManager | None:
        tier = self._hit_tier.get(step_id)
        return self._managers.get(tier) if tier is not None else None

    # -- CheckpointStore interface -------------------------------------------

    def has_checkpoint(self, step_id: str) -> bool:
        for tier in self._read_order:
            if self._managers[tier].has_checkpoint(step_id):
                self._hit_tier[step_id] = tier
                return True
        return False

    def load(self, step_id: str) -> dict[str, Any]:
        manager = self._manager_for(step_id)
        if manager is not None:
            return manager.load(step_id)
        # No memoized tier (direct load without a prior probe): probe now.
        if self.has_checkpoint(step_id):
            return self._managers[self._hit_tier[step_id]].load(step_id)
        raise FileNotFoundError(
            f"no checkpoint for step {step_id!r} in tiers {self._read_order}"
        )

    def save(self, step_id: str, outputs: dict[str, Any]) -> None:
        writer = self._managers[self.write_tier]
        if self.write_tier == SURVEY_TIER:
            # Shared-tier eligibility: only portable formats (zarr/json).
            # Checked before any artifact write so a rejected entry leaves
            # nothing behind (the commit protocol stays intact).
            from aa_recipe_manager.executor.checkpoint import _would_pickle

            for out_name, value in outputs.items():
                if _would_pickle(value, str(writer.preferred_format)):
                    raise ValueError(
                        f"step {step_id!r} output {out_name!r} would be "
                        "serialized as pickle, which is not eligible for the "
                        "shared survey cache (pickles are not portable across "
                        "environments; only zarr/json artifacts are shared). "
                        "Run with the user write tier instead."
                    )
        writer.save(step_id, outputs)
        # The immediate save-then-reload round trip (and the manifest) must
        # resolve against the tier that now holds the entry.
        self._hit_tier[step_id] = self.write_tier

    # -- markers: user tier only, unconditionally ----------------------------

    def has_marker(self, step_id: str) -> bool:
        return self._managers[USER_TIER].has_marker(step_id)

    def save_marker(self, step_id: str) -> None:
        self._managers[USER_TIER].save_marker(step_id)
        self._hit_tier.setdefault(step_id, USER_TIER)
