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
