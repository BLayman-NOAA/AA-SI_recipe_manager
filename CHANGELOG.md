# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Project scaffold customized from AA-SI Python template
- Package renamed to `aa-recipe-manager` (import as `aa_recipe_manager`)
- Core dependencies: pydantic, ruamel.yaml, click, nbformat
- CLI entry point (`aa-recipe-manager`)
- Full directory structure matching the layered architecture (model, parser, registry, resolver, provenance, tracker, generator, executor, orchestrator)
- Development tooling: ruff, mypy, pytest, pre-commit
- Optional dependency groups for dask and prefect executors

## [0.1.0] - YYYY-MM-DD

### Added
- Initial release
- Basic package structure with src layout
- Development tooling (pytest, ruff, mypy, pre-commit)

<!--
=============================================================================
CHANGELOG GUIDELINES
=============================================================================

When adding entries, use the following categories:
- Added: for new features
- Changed: for changes in existing functionality
- Deprecated: for soon-to-be removed features
- Removed: for now removed features
- Fixed: for any bug fixes
- Security: in case of vulnerabilities

Each release should have a version number and date in the format:
## [X.Y.Z] - YYYY-MM-DD

Link definitions should be added at the bottom (optional)
