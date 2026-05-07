# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Registry class for spec and implementation lookup."""

from __future__ import annotations

import importlib.metadata
import warnings

from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
    SpecNotFoundError,
)
from aa_recipe_manager.model.types import Implementation, Spec


class Registry:
    """In-memory store for specs and their implementation mappings.

    Specs are keyed by op name. Implementations are grouped by op name
    and further keyed by implementation key.
    """

    def __init__(self) -> None:
        self._specs: dict[str, Spec] = {}
        self._implementations: dict[str, dict[str, Implementation]] = {}

    def register_spec(self, spec: Spec) -> None:
        """Add a spec to the registry, overwriting any existing entry for the same op."""
        self._specs[spec.op] = spec
        if spec.op not in self._implementations:
            self._implementations[spec.op] = {}

    def register_implementation(self, impl: Implementation) -> None:
        """Add an implementation to the registry.

        The corresponding spec must already be registered.
        """
        if impl.op not in self._implementations:
            self._implementations[impl.op] = {}
        self._implementations[impl.op][impl.key] = impl

    def has_spec(self, op: str) -> bool:
        """Return True if a spec is registered for the given op name."""
        return op in self._specs

    def get_spec(self, op: str) -> Spec:
        """Return the spec for op. Raises SpecNotFoundError if not registered."""
        if op not in self._specs:
            raise SpecNotFoundError(f"No spec registered for op '{op}'.")
        return self._specs[op]

    def get_implementation(
        self, op: str, key: str | None = None, check_versions: bool = True
    ) -> Implementation:
        """Return an implementation for op.

        Selection rules:
        - If key is provided, return the matching implementation or raise
          ImplementationNotFoundError.
        - If only one implementation exists, return it regardless of the default flag.
        - If multiple exist and none is marked default, raise AmbiguousImplementationError.
        - If multiple exist and one is marked default (and no key is specified), return it.

        Raises DependencyVersionError if the resolved implementation's dependency is not
        installed or the installed version falls outside the declared range.
        When check_versions is False, the dependency installation check is skipped.
        """
        impls = self._implementations.get(op, {})
        if not impls:
            raise ImplementationNotFoundError(f"No implementations registered for op '{op}'.")

        if key is not None:
            if key not in impls:
                available = list(impls.keys())
                raise ImplementationNotFoundError(
                    f"Implementation '{key}' not found for op '{op}'. "
                    f"Available: {available}"
                )
            impl = impls[key]
        elif len(impls) == 1:
            impl = next(iter(impls.values()))
        else:
            defaults = [i for i in impls.values() if i.default]
            if len(defaults) == 1:
                impl = defaults[0]
            else:
                available = list(impls.keys())
                raise AmbiguousImplementationError(
                    f"Multiple implementations for op '{op}' and none is marked default. "
                    f"Specify one of: {available}"
                )

        if check_versions:
            self._check_version(impl)
        return impl

    def list_ops(self) -> list[str]:
        """Return a sorted list of all registered op names."""
        return sorted(self._specs.keys())

    def list_implementations(self, op: str) -> list[str]:
        """Return a sorted list of implementation keys for the given op."""
        return sorted(self._implementations.get(op, {}).keys())

    def _check_version(self, impl: Implementation) -> None:
        """Check that the implementation's dependency is installed and in range."""
        from packaging.specifiers import SpecifierSet

        dep = impl.dependency
        try:
            installed = importlib.metadata.version(dep.name)
        except importlib.metadata.PackageNotFoundError:
            raise DependencyVersionError(
                f"Dependency '{dep.name}' required by implementation '{impl.key}' "
                f"(op '{impl.op}') is not installed."
            )

        if installed not in SpecifierSet(dep.version):
            raise DependencyVersionError(
                f"Installed version of '{dep.name}' ({installed}) is outside the "
                f"declared range '{dep.version}' for implementation '{impl.key}' "
                f"(op '{impl.op}')."
            )

        if impl.tested_versions and installed not in impl.tested_versions:
            warnings.warn(
                f"Installed '{dep.name}' ({installed}) is not in the tested versions "
                f"{impl.tested_versions} for implementation '{impl.key}' (op '{impl.op}'). "
                "Results may differ from tested behavior.",
                stacklevel=3,
            )

