# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Package-wide exception classes."""


class RecipeParseError(Exception):
    """Raised when a recipe file cannot be read or parsed into a Recipe object."""


class RecipeValidationError(Exception):
    """Raised when a recipe's DAG fails structural validation.

    Attributes:
        errors: List of error messages that blocked DAG construction.
        warnings: List of warning messages (non-blocking issues).
    """

    def __init__(self, errors: list[str], warnings: list[str] | None = None) -> None:
        self.errors = errors
        self.warnings = warnings or []
        lines = [f"{len(errors)} validation error(s):"]
        lines.extend(f"  - {e}" for e in errors)
        if self.warnings:
            lines.append(f"{len(self.warnings)} warning(s):")
            lines.extend(f"  - {w}" for w in self.warnings)
        super().__init__("\n".join(lines))


class SpecNotFoundError(LookupError):
    """Raised when an op name is not found in the registry."""


class ImplementationNotFoundError(LookupError):
    """Raised when no implementation is registered for an op."""


class AmbiguousImplementationError(LookupError):
    """Raised when multiple implementations exist for an op and none is marked default."""


class DependencyVersionError(Exception):
    """Raised when an implementation's dependency is missing or outside the declared version range."""


class DependencyConflictError(Exception):
    """Raised when a recipe's steps require irreconcilable versions of a package.

    A Python environment holds one build of any package, so a recipe whose
    steps disagree has no valid environment. Installing one of the two anyway
    would silently give some step something other than what it declared.

    Attributes:
        conflicts: One message per package that could not be reconciled.
    """

    def __init__(self, conflicts: list[str]) -> None:
        self.conflicts = conflicts
        lines = [f"{len(conflicts)} dependency conflict(s):"]
        lines.extend(f"  - {c}" for c in conflicts)
        super().__init__("\n".join(lines))


class PipelineExecutionError(Exception):
    """Raised when a step callable fails during direct pipeline execution.

    Attributes:
        step_id: Recipe step id where the failure occurred.
        callable_path: Dotted import path of the failing callable, when known.
        original: The original exception raised by the callable.
    """

    def __init__(
        self,
        step_id: str,
        message: str,
        *,
        callable_path: str | None = None,
        original: BaseException | None = None,
    ) -> None:
        self.step_id = step_id
        self.callable_path = callable_path
        self.original = original
        super().__init__(message)
