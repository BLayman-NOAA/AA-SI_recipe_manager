# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""The echogram pane's server half: EchogramGL's catalog, mounted when installed.

EchogramGL is an optional dependency (the ``echogram`` extra). Without it the
server still runs; the artifacts endpoint says the pane is unavailable and why,
and no ``/echogram`` routes exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from fastapi import APIRouter

INSTALL_HINT = (
    "aa-si-echogram-gl is not installed: pip install 'aa-recipe-manager[echogram]'"
)


class EchogramService:
    """EchogramGL's mounts for the client server, or a stub when it is not installed.

    The mounts start with no readable roots. Each artifacts request adds the
    session's cache folders, so the browser can only read under folders a
    session uses, through mounts the server handed out.

    Attributes:
        available: Whether EchogramGL and its router can be imported.
        reason: Why the pane is unavailable, or None.
        mounts: EchogramGL's ``Mounts``, or None when unavailable.
    """

    def __init__(self) -> None:
        self.available = False
        self.reason: str | None = None
        self.mounts = None
        try:
            from aa_si_echogram_gl import catalog, serve
            from aa_si_echogram_gl.server import build_router
        except ImportError as error:
            missing = getattr(error, "name", None) == "aa_si_echogram_gl"
            self.reason = (
                INSTALL_HINT
                if missing
                else f"aa-si-echogram-gl could not be imported: {error}"
            )
            return
        self._catalog = catalog
        self._serve = serve
        self._build_router = build_router
        self.mounts = serve.Mounts([])
        self.available = True

    def router(self) -> APIRouter | None:
        """EchogramGL's catalog and data routes over the mounts, or None."""
        return self._build_router(self.mounts) if self.available else None

    def allow(self, roots: Iterable[str | None]) -> None:
        """Let the mounts read under these folders or URLs; None is skipped."""
        if self.available:
            self.mounts.allow(str(root) for root in roots if root)

    def resolve(
        self,
        found: dict[str, Any],
        *,
        output: str | None = None,
        var: str | None = None,
        start: str | None = None,
        end: str | None = None,
        storage_options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The echogram block of an artifacts answer: what can be drawn, mounted.

        Args:
            found: ``StepArtifacts.to_dict()`` of the step.
            output: The output to draw, or None for the default.
            var: The variable to draw, or None for the default.
            start: Start of a time window for a mapped step, or None.
            end: End of the time window, or None.
            storage_options: fsspec options for reading the artifacts.

        Returns:
            ``available``, ``reason`` and ``resolution``: EchogramGL's
            resolution with each dataset instance or pyramid mounted, or None
            when EchogramGL is missing, nothing was found, or the artifacts
            could not be read (``reason`` then says why).

        Raises:
            ValueError: ``output``, ``var``, ``start`` or ``end`` does not name
                something to draw (EchogramGL's ``CatalogError``).
        """
        block = {"available": self.available, "reason": self.reason, "resolution": None}
        if not self.available or not found["artifacts"]:
            return block
        window = self._serve.parse_window(start, end)
        try:
            result = self._catalog.resolve_artifacts(
                found["artifacts"],
                found["fanned_out"],
                output,
                window,
                storage_options,
                var,
                step=found["step_id"],
                step_hash=found["step_hash"],
            )
            block["resolution"] = self._serve.mount_resolution(
                result, self.mounts, storage_options
            )
        except self._catalog.CatalogError:
            raise
        except Exception as error:  # noqa: BLE001 - reported in the answer
            block["reason"] = f"{type(error).__name__}: {error}"
        return block
