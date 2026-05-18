"""Pytest configuration and fixtures for netlist_tracer tests."""

import os

import pytest


@pytest.fixture
def fixtures_synthetic_dir():
    """Return path to synthetic fixtures directory."""
    return os.path.join(os.path.dirname(__file__), "fixtures", "synthetic")


@pytest.fixture
def synthetic_verilog_features_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v fixture."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_concat_alias_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains concat_alias_mod)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_generate_loop_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains generate_loop_mod)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_param_specialize_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains param_specialize_mod)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_spice_features_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_features.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_features.sp")


@pytest.fixture
def synthetic_spice_basic_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_features.sp (contains spice_basic_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_features.sp")


@pytest.fixture
def synthetic_spice_flat_deck_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_flat_deck.sp fixture (flat-deck testbench)."""
    return os.path.join(fixtures_synthetic_dir, "spice_flat_deck.sp")


@pytest.fixture
def synthetic_spice_edge_cases_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_edge_cases.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_cases.sp")


@pytest.fixture
def synthetic_spice_edge_long_line_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_edge_cases.sp (contains edge_long_line_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_cases.sp")


@pytest.fixture
def synthetic_spice_edge_mixed_case_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_edge_cases.sp (contains edge_mixed_case_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_cases.sp")


@pytest.fixture
def synthetic_spice_edge_tab_continuation_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_edge_cases.sp (contains edge_tab_continuation_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_cases.sp")


@pytest.fixture
def synthetic_cdl_features_cdl(fixtures_synthetic_dir):
    """Path to consolidated cdl_features.cdl fixture."""
    return os.path.join(fixtures_synthetic_dir, "cdl_features.cdl")


@pytest.fixture
def synthetic_cdl_basic_cdl(fixtures_synthetic_dir):
    """Path to consolidated cdl_features.cdl (contains cdl_basic_cell)."""
    return os.path.join(fixtures_synthetic_dir, "cdl_features.cdl")


@pytest.fixture
def synthetic_cdl_internal_nets_cdl(fixtures_synthetic_dir):
    """Path to consolidated cdl_features.cdl (contains cdl_internal_nets_parent)."""
    return os.path.join(fixtures_synthetic_dir, "cdl_features.cdl")


@pytest.fixture
def synthetic_supply_constant_cdl(fixtures_synthetic_dir):
    """Path to consolidated cdl_features.cdl (contains cdl_supply_constant)."""
    return os.path.join(fixtures_synthetic_dir, "cdl_features.cdl")


@pytest.fixture
def synthetic_spectre_basic_scs(fixtures_synthetic_dir):
    """Path to synthetic spectre_basic.scs fixture."""
    return os.path.join(fixtures_synthetic_dir, "spectre_basic.scs")


@pytest.fixture
def synthetic_nested_generate_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains nested_generate_mod)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_verilog_generate_if_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains generate_if_mod)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_verilog_generate_case_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains generate_case_mod)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_verilog_gate_primitives_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains gate_primitives_mod)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_verilog_defparam_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_features.v (contains defparam_top)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_features.v")


@pytest.fixture
def synthetic_verilog_udp_genvar_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_udp_genvar.v fixture."""
    return os.path.join(fixtures_synthetic_dir, "verilog_udp_genvar.v")


@pytest.fixture
def synthetic_udp_simple_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_udp_genvar.v (contains UDP definitions)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_udp_genvar.v")


@pytest.fixture
def synthetic_genvar_for_label_v(fixtures_synthetic_dir):
    """Path to consolidated verilog_udp_genvar.v (contains genvar_for_top)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_udp_genvar.v")


@pytest.fixture
def fixtures_vendored_dir():
    """Return path to vendored fixtures directory."""
    return os.path.join(os.path.dirname(__file__), "fixtures", "vendored")


@pytest.fixture
def vendored_picorv32_v(fixtures_vendored_dir):
    """Path to vendored picorv32.v fixture."""
    return os.path.join(fixtures_vendored_dir, "picorv32.v")


@pytest.fixture
def vendored_AND_gate_edf(fixtures_vendored_dir):
    """Path to vendored AND_gate.edf fixture."""
    return os.path.join(fixtures_vendored_dir, "AND_gate.edf")


@pytest.fixture
def vendored_n_bit_counter_edf(fixtures_vendored_dir):
    """Path to vendored n_bit_counter.edf fixture."""
    return os.path.join(fixtures_vendored_dir, "n_bit_counter.edf")


@pytest.fixture
def vendored_one_counter_edf(fixtures_vendored_dir):
    """Path to vendored one_counter.edf fixture."""
    return os.path.join(fixtures_vendored_dir, "one_counter.edf")


@pytest.fixture
def vendored_hic2_ft_sp(fixtures_vendored_dir):
    """Path to vendored hic2_ft.sp NGSpice fixture."""
    return os.path.join(fixtures_vendored_dir, "ngspice", "hic2_ft.sp")


@pytest.fixture
def synthetic_spice_inline_comments_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_features.sp (contains spice_inline_comments_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_features.sp")


@pytest.fixture
def synthetic_spice_continuation_across_comment_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_features.sp (contains spice_continuation_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_features.sp")


@pytest.fixture
def synthetic_spice_controlled_sources_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_features.sp (contains spice_controlled_sources_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_features.sp")


@pytest.fixture
def synthetic_spice_coupled_inductor_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_features.sp (contains spice_coupled_inductor_cell)."""
    return os.path.join(fixtures_synthetic_dir, "spice_features.sp")


@pytest.fixture
def synthetic_spice_global_directive_sp(fixtures_synthetic_dir):
    """Path to consolidated spice_features.sp (contains .GLOBAL directive)."""
    return os.path.join(fixtures_synthetic_dir, "spice_features.sp")


@pytest.fixture
def synthetic_spice_edge_crlf_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_edge_crlf.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_crlf.sp")


@pytest.fixture
def synthetic_spice_edge_utf8_bom_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_edge_utf8_bom.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_utf8_bom.sp")


@pytest.fixture
def synthetic_verilog_a_leaf_va(fixtures_synthetic_dir):
    """Path to synthetic verilog_a_leaf.va fixture (Verilog-A leaf cell)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_a_leaf.va")


@pytest.fixture
def synthetic_verilog_a_parent_v(fixtures_synthetic_dir):
    """Path to synthetic verilog_a_parent.v fixture (parent instantiating Verilog-A)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_a_parent.v")


@pytest.fixture
def synthetic_primitive_mosfet_spf(fixtures_synthetic_dir):
    """Path to synthetic primitive_mosfet.spf fixture (SPF with MOSFET primitive)."""
    return os.path.join(fixtures_synthetic_dir, "primitive_mosfet.spf")


@pytest.fixture
def synthetic_simple_inv_spef(fixtures_synthetic_dir):
    """Path to synthetic simple_inv.spef fixture (basic SPEF for testing)."""
    return os.path.join(fixtures_synthetic_dir, "simple_inv.spef")


@pytest.fixture
def synthetic_simple_inv_spef_gz(fixtures_synthetic_dir):
    """Path to synthetic simple_inv.spef.gz fixture (gzipped SPEF for testing)."""
    return os.path.join(fixtures_synthetic_dir, "simple_inv.spef.gz")


@pytest.fixture
def synthetic_sv_interfaces_sv(fixtures_synthetic_dir):
    """Path to consolidated sv_interfaces.sv fixture."""
    return os.path.join(fixtures_synthetic_dir, "sv_interfaces.sv")


@pytest.fixture
def synthetic_edif_features_edif(fixtures_synthetic_dir):
    """Path to consolidated edif_features.edif fixture."""
    return os.path.join(fixtures_synthetic_dir, "edif_features.edif")


@pytest.fixture
def synthetic_intf_pkg_header_sv(fixtures_synthetic_dir):
    """Path to synthetic intf_pkg_header.sv fixture (module with package imports in header)."""
    return os.path.join(fixtures_synthetic_dir, "intf_pkg_header.sv")
