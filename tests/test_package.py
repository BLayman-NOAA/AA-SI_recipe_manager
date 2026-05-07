# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Basic package tests for aa_recipe_manager."""

import aa_recipe_manager


def test_version_exists():
    """Test that the package has a version string."""
    assert hasattr(aa_recipe_manager, "__version__")
    assert isinstance(aa_recipe_manager.__version__, str)


def test_version_format():
    """Test that version follows semantic versioning format (X.Y.Z)."""
    version = aa_recipe_manager.__version__
    if version == "0.0.0.dev":
        return
    parts = version.split(".")
    assert len(parts) >= 2, "Version should have at least major.minor"
    assert parts[0].isdigit(), "Major version should be numeric"
    assert parts[1].isdigit(), "Minor version should be numeric"


def test_import_isolation():
    """Verify that importing the package does not require scientific libraries."""
    # This test passes if we got here without ImportError.
    # It guards against accidental coupling to echopype, xarray, etc.
    assert aa_recipe_manager.__version__


# =============================================================================
# TODO: Add your own tests below
# =============================================================================
#
# Example test structure:
#
# def test_my_function():
#     """Test description."""
#     from mypackagename.module import my_function
#     result = my_function(input_value)
#     assert result == expected_value
#
# For more pytest features, see: https://docs.pytest.org/
# =============================================================================
