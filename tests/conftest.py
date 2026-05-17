"""Pytest configuration and fixtures for netlist_tracer tests."""

import os

import pytest


@pytest.fixture
def fixtures_synthetic_dir():
    """Return path to synthetic fixtures directory."""
    return os.path.join(os.path.dirname(__file__), "fixtures", "synthetic")


@pytest.fixture
def synthetic_concat_alias_v(fixtures_synthetic_dir):
    """Path to synthetic concat_alias.v fixture."""
    return os.path.join(fixtures_synthetic_dir, "concat_alias.v")


@pytest.fixture
def synthetic_generate_loop_v(fixtures_synthetic_dir):
    """Path to synthetic generate_loop.v fixture."""
    return os.path.join(fixtures_synthetic_dir, "generate_loop.v")


@pytest.fixture
def synthetic_param_specialize_v(fixtures_synthetic_dir):
    """Path to synthetic param_specialize.v fixture."""
    return os.path.join(fixtures_synthetic_dir, "param_specialize.v")


@pytest.fixture
def synthetic_supply_constant_cdl(fixtures_synthetic_dir):
    """Path to synthetic supply_constant.cdl fixture."""
    return os.path.join(fixtures_synthetic_dir, "supply_constant.cdl")


@pytest.fixture
def synthetic_spice_basic_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_basic.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_basic.sp")


@pytest.fixture
def synthetic_spice_flat_deck_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_flat_deck.sp fixture (flat-deck testbench)."""
    return os.path.join(fixtures_synthetic_dir, "spice_flat_deck.sp")


@pytest.fixture
def synthetic_cdl_basic_cdl(fixtures_synthetic_dir):
    """Path to synthetic cdl_basic.cdl fixture."""
    return os.path.join(fixtures_synthetic_dir, "cdl_basic.cdl")


@pytest.fixture
def synthetic_spectre_basic_scs(fixtures_synthetic_dir):
    """Path to synthetic spectre_basic.scs fixture."""
    return os.path.join(fixtures_synthetic_dir, "spectre_basic.scs")


@pytest.fixture
def synthetic_nested_generate_v(fixtures_synthetic_dir):
    """Path to synthetic nested_generate.v fixture (2-level nested generate-for)."""
    return os.path.join(fixtures_synthetic_dir, "nested_generate.v")


@pytest.fixture
def synthetic_verilog_generate_if_v(fixtures_synthetic_dir):
    """Path to synthetic verilog_generate_if.v fixture (generate if)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_generate_if.v")


@pytest.fixture
def synthetic_verilog_generate_case_v(fixtures_synthetic_dir):
    """Path to synthetic verilog_generate_case.v fixture (generate case)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_generate_case.v")


@pytest.fixture
def synthetic_verilog_gate_primitives_v(fixtures_synthetic_dir):
    """Path to synthetic verilog_gate_primitives.v fixture (gate primitives)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_gate_primitives.v")


@pytest.fixture
def synthetic_verilog_defparam_v(fixtures_synthetic_dir):
    """Path to synthetic verilog_defparam.v fixture (defparam parameter override)."""
    return os.path.join(fixtures_synthetic_dir, "verilog_defparam.v")


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
    """Path to synthetic spice_inline_comments.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_inline_comments.sp")


@pytest.fixture
def synthetic_spice_continuation_across_comment_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_continuation_across_comment.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_continuation_across_comment.sp")


@pytest.fixture
def synthetic_spice_controlled_sources_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_controlled_sources.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_controlled_sources.sp")


@pytest.fixture
def synthetic_spice_coupled_inductor_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_coupled_inductor.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_coupled_inductor.sp")


@pytest.fixture
def synthetic_spice_global_directive_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_global_directive.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_global_directive.sp")


@pytest.fixture
def synthetic_spice_edge_crlf_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_edge_crlf.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_crlf.sp")


@pytest.fixture
def synthetic_spice_edge_utf8_bom_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_edge_utf8_bom.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_utf8_bom.sp")


@pytest.fixture
def synthetic_spice_edge_long_line_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_edge_long_line.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_long_line.sp")


@pytest.fixture
def synthetic_spice_edge_tab_continuation_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_edge_tab_continuation.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_tab_continuation.sp")


@pytest.fixture
def synthetic_spice_edge_mixed_case_sp(fixtures_synthetic_dir):
    """Path to synthetic spice_edge_mixed_case.sp fixture."""
    return os.path.join(fixtures_synthetic_dir, "spice_edge_mixed_case.sp")


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
