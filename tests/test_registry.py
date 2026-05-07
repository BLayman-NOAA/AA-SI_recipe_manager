# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: NOAA Fisheries
"""Tests for the spec/implementation Registry and builtin loader."""

from __future__ import annotations

import importlib.metadata

import pytest

from aa_recipe_manager.exceptions import (
    AmbiguousImplementationError,
    DependencyVersionError,
    ImplementationNotFoundError,
    SpecNotFoundError,
)
from aa_recipe_manager.model.types import Spec
from aa_recipe_manager.registry.loader import load_builtin_registry
from aa_recipe_manager.registry.registry import Registry
from conftest import make_dependency, make_implementation, make_spec

# ---------------------------------------------------------------------------
# Registry — spec operations
# ---------------------------------------------------------------------------

class TestRegistrySpecs:
    def test_register_and_get_spec(self):
        reg = Registry()
        spec = make_spec(op="compute_sv")
        reg.register_spec(spec)
        assert reg.get_spec("compute_sv") is spec

    def test_has_spec_false_before_registration(self):
        reg = Registry()
        assert reg.has_spec("nonexistent") is False

    def test_get_spec_raises_if_not_found(self):
        reg = Registry()
        with pytest.raises(SpecNotFoundError):
            reg.get_spec("ghost_op")

    def test_list_ops_sorted(self):
        reg = Registry()
        reg.register_spec(make_spec(op="z_op"))
        reg.register_spec(make_spec(op="a_op"))
        assert reg.list_ops() == ["a_op", "z_op"]

    def test_registering_twice_replaces_spec(self):
        reg = Registry()
        spec1 = make_spec(op="compute_sv", description="v1")
        spec2 = make_spec(op="compute_sv", description="v2")
        reg.register_spec(spec1)
        reg.register_spec(spec2)
        assert reg.get_spec("compute_sv").description == "v2"


# ---------------------------------------------------------------------------
# Registry — implementation operations
# ---------------------------------------------------------------------------

class TestRegistryImplementations:
    def test_get_single_implementation(self):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        impl = make_implementation(op="compute_sv", key="default")
        reg.register_implementation(impl)
        result = reg.get_implementation("compute_sv")
        assert result is impl

    def test_get_implementation_by_key(self):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        impl_a = make_implementation(op="compute_sv", key="alpha")
        impl_b = make_implementation(op="compute_sv", key="beta", default=True)
        reg.register_implementation(impl_a)
        reg.register_implementation(impl_b)
        assert reg.get_implementation("compute_sv", "alpha") is impl_a

    def test_get_default_when_multiple(self):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        impl_a = make_implementation(op="compute_sv", key="alpha")
        impl_b = make_implementation(op="compute_sv", key="beta", default=True)
        reg.register_implementation(impl_a)
        reg.register_implementation(impl_b)
        assert reg.get_implementation("compute_sv") is impl_b

    def test_ambiguous_raises_without_key(self):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        reg.register_implementation(make_implementation(op="compute_sv", key="a"))
        reg.register_implementation(make_implementation(op="compute_sv", key="b"))
        with pytest.raises(AmbiguousImplementationError):
            reg.get_implementation("compute_sv")

    def test_no_impl_raises(self):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        with pytest.raises(ImplementationNotFoundError):
            reg.get_implementation("compute_sv")

    def test_list_implementations_sorted(self):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        reg.register_implementation(make_implementation(op="compute_sv", key="z_impl"))
        reg.register_implementation(make_implementation(op="compute_sv", key="a_impl"))
        assert reg.list_implementations("compute_sv") == ["a_impl", "z_impl"]

    def test_missing_dependency_raises_version_error(self, monkeypatch):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        reg.register_implementation(make_implementation(op="compute_sv", key="default"))

        def _missing_version(_name):
            raise importlib.metadata.PackageNotFoundError

        monkeypatch.setattr(importlib.metadata, "version", _missing_version)

        with pytest.raises(DependencyVersionError, match="is not installed"):
            reg.get_implementation("compute_sv")

    def test_out_of_range_dependency_raises_version_error(self, monkeypatch):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        reg.register_implementation(
            make_implementation(
                op="compute_sv",
                key="default",
                dependency=make_dependency(version=">=9.0,<10.0"),
            )
        )

        monkeypatch.setattr(importlib.metadata, "version", lambda _name: "8.5.0")

        with pytest.raises(DependencyVersionError, match="outside the declared range"):
            reg.get_implementation("compute_sv")

    def test_untested_version_warns_when_in_declared_range(self, monkeypatch):
        reg = Registry()
        reg.register_spec(make_spec(op="compute_sv"))
        reg.register_implementation(
            make_implementation(
                op="compute_sv",
                key="default",
                tested_versions=["9.0.1"],
                dependency=make_dependency(version=">=9.0,<10.0"),
            )
        )

        monkeypatch.setattr(importlib.metadata, "version", lambda _name: "9.0.2")

        with pytest.warns(UserWarning, match="not in the tested versions"):
            result = reg.get_implementation("compute_sv")

        assert result.key == "default"


# ---------------------------------------------------------------------------
# Builtin loader
# ---------------------------------------------------------------------------

EXPECTED_BUILTIN_OPS = {
    "query_ncei_data",
    "download_ncei_data",
    "setup_raw_files",
    "generate_standardized_cal_mapping",
    "open_raw_files",
    "extract_standardized_cal_params",
    "compute_sv",
    "detect_seafloor",
    "create_seafloor_mask",
    "create_surface_mask",
    "create_frequency_mask",
    "combine_masks",
    "create_sv_mask",
    "apply_sv_mask",
    "remove_background_noise",
    "mask_sparse_bins",
    "compute_mvbs",
    "add_line_overlay",
    "plot_sv_echogram",
    "reshape_for_ml",
    "add_auxiliary_features",
    "normalize_ml_data",
    "plot_ml_echogram",
    "run_hdbscan",
    "embed_clustering_results",
    "plot_clustering_report",
    "log_seafloor_detection_stats",
}


class TestBuiltinLoader:
    def test_all_builtin_specs_loaded(self):
        reg = load_builtin_registry()
        loaded = set(reg.list_ops())
        assert loaded == EXPECTED_BUILTIN_OPS

    def test_specs_are_valid_spec_objects(self):
        reg = load_builtin_registry()
        for op in reg.list_ops():
            spec = reg.get_spec(op)
            assert isinstance(spec, Spec)
            assert spec.op == op

    def test_sink_flag_on_plot_ops(self):
        reg = load_builtin_registry()
        assert reg.get_spec("plot_sv_echogram").sink is True
        assert reg.get_spec("plot_ml_echogram").sink is True
        assert reg.get_spec("plot_clustering_report").sink is True

    def test_non_sink_op(self):
        reg = load_builtin_registry()
        assert reg.get_spec("compute_sv").sink is False

    def test_optional_input_on_compute_sv(self):
        reg = load_builtin_registry()
        spec = reg.get_spec("compute_sv")
        cal_port = spec.inputs.get("cal_params")
        assert cal_port is not None
        assert cal_port.required is False

    def test_embed_clustering_results_spec_matches_multi_result_contract(self):
        reg = load_builtin_registry()
        spec = reg.get_spec("embed_clustering_results")

        assert spec.inputs["clustering_results"].type == "dict"
        assert spec.inputs["clustering_results"].many is True
        assert "ds_Sv" not in spec.inputs
        assert spec.outputs["gridded_results"].type == "DataArray"
        assert spec.outputs["gridded_results"].many is True

    def test_plot_clustering_report_is_sink_spec(self):
        reg = load_builtin_registry()
        spec = reg.get_spec("plot_clustering_report")

        assert spec.sink is True
        assert spec.inputs["clustering_results"].many is True
        assert spec.outputs == {}

    def test_calibration_builtin_implementations_resolve(self, monkeypatch):
        original_version = importlib.metadata.version

        def _version(name: str):
            if name == "aa-si-calibration":
                return "0.1.0"
            return original_version(name)

        monkeypatch.setattr(importlib.metadata, "version", _version)

        reg = load_builtin_registry()

        mapping_impl = reg.get_implementation("generate_standardized_cal_mapping")
        assert mapping_impl.callable_path == (
            "aa_si_calibration.calibration.generate_standardized_cal_mapping"
        )
        assert mapping_impl.output_map == {
            "mapping_dict": "['mapping_dict']",
            "calibration_dict": "['calibration_dict']",
        }

        extract_impl = reg.get_implementation("extract_standardized_cal_params")
        assert extract_impl.callable_path == (
            "aa_si_calibration.calibration.extract_standardized_calibration_parameters"
        )
        assert extract_impl.output_map == {
            "cal_params": "['cal_params']",
            "env_params": "['env_params']",
            "other_params": "['other_params']",
        }

    def test_utils_builtin_implementations_resolve(self):
        reg = load_builtin_registry()

        detect_impl = reg.get_implementation("detect_seafloor")
        assert detect_impl.callable_path == "aa_si_utils.utils.detect_seafloor"
        assert detect_impl.output_map == {"seafloor_depth": "__return__"}

        combine_impl = reg.get_implementation("combine_masks")
        assert combine_impl.callable_path == "aa_si_utils.utils.combine_masks"
        assert combine_impl.output_map == {"mask": "__return__"}

        download_impl = reg.get_implementation("download_ncei_data")
        assert download_impl.callable_path == "aa_si_utils.data_retrieval.download_ncei_data"
        assert download_impl.output_map == {"downloaded_paths": "__return__"}

        overlay_impl = reg.get_implementation("add_line_overlay")
        assert overlay_impl.param_map == {
            "ds": "ds_MVBS",
            "line_file_path": "csv_filepath",
            "line_name": "dive_profile_name",
        }

    def test_echopype_and_visualization_builtin_implementations_resolve(self):
        reg = load_builtin_registry()

        compute_sv_impl = reg.get_implementation("compute_sv")
        assert compute_sv_impl.callable_path == "echopype.calibrate.compute_Sv"
        assert compute_sv_impl.output_map == {"ds_Sv": "__return__"}

        compute_mvbs_impl = reg.get_implementation("compute_mvbs")
        assert compute_mvbs_impl.callable_path == "echopype.commongrid.compute_MVBS"
        assert compute_mvbs_impl.output_map == {"ds_MVBS": "__return__"}

        plot_sv_impl = reg.get_implementation("plot_sv_echogram")
        assert plot_sv_impl.callable_path == "aa_si_visualization.echogram.plot_sv_echogram"
        assert plot_sv_impl.param_map == {"ds_Sv_source": "ds_Sv_original"}

        plot_ml_impl = reg.get_implementation("plot_ml_echogram")
        assert plot_ml_impl.callable_path == (
            "aa_si_visualization.echogram.plot_flattened_data_echogram"
        )
        assert plot_ml_impl.param_map == {
            "ds_normalized": "ds_ml",
            "dataset_name": "ml_dataset_name",
            "normalization_name": "ml_specific_data_name",
            "ds_Sv": "ds_Sv_original",
        }

    def test_ml_builtin_implementations_resolve(self):
        reg = load_builtin_registry()

        reshape_impl = reg.get_implementation("reshape_for_ml")
        assert reshape_impl.callable_path == "aa_si_ml.ml.reshape_data_for_ml"
        assert reshape_impl.param_map == {"ds_MVBS": "ds_Sv"}

        aux_impl = reg.get_implementation("add_auxiliary_features")
        assert aux_impl.callable_path == "aa_si_ml.ml.add_auxiliary_features"
        assert aux_impl.param_map == {"ds_ml": "ds_ml_ready"}

        normalize_impl = reg.get_implementation("normalize_ml_data")
        assert normalize_impl.callable_path == "aa_si_ml.ml.normalize_data"
        assert normalize_impl.param_map == {"ds_ml": "ds_ml_ready"}

        run_impl = reg.get_implementation("run_hdbscan")
        assert run_impl.callable_path == "aa_si_ml.ml.run_hdbscan"
        assert run_impl.output_map == {
            "clustering_results": "['clustering_results']",
            "background_label": "['background_label']",
        }

        embed_impl = reg.get_implementation("embed_clustering_results")
        assert embed_impl.callable_path == "aa_si_ml.ml.embed_clustering_results"
        assert embed_impl.output_map == {
            "ds_normalized": "['ds_normalized']",
            "gridded_results": "['gridded_results']",
        }
