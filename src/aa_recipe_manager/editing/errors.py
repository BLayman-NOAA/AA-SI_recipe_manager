# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Exceptions raised by the recipe editing model."""

from __future__ import annotations

from pathlib import Path


class EditError(Exception):
    """Base class for editing failures."""


class UnknownTarget(EditError, LookupError):
    """An operation names a step, include, document or key that does not exist."""


class EditRejected(EditError):
    """An operation was undone because the result does not parse or validate.

    Attributes:
        problems: What was wrong with the result, one message per problem.
    """

    def __init__(self, problems: list[str]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(self.problems))


class WorkspaceError(EditError):
    """A workspace or save request the editor cannot honour."""


class TargetExists(WorkspaceError):
    """A save would replace existing files and overwriting was not allowed.

    Attributes:
        paths: The existing files.
    """

    def __init__(self, message: str, paths: list[Path]) -> None:
        self.paths = list(paths)
        super().__init__(message)


class ChangedOnDisk(WorkspaceError):
    """Files changed on disk since the workspace last read or wrote them.

    Attributes:
        paths: The files that changed.
    """

    def __init__(self, paths: list[Path]) -> None:
        self.paths = list(paths)
        listed = ", ".join(str(path) for path in self.paths)
        super().__init__(
            f"Changed on disk since they were read: {listed}. Nothing was "
            "written; reload them to take the disk's text, or overwrite them."
        )
