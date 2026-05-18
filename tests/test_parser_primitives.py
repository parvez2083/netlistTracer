"""Unit tests for primitive SubcktDef synthesis."""

import os
import tempfile

from netlist_tracer import NetlistParser
from netlist_tracer.model import Instance
from netlist_tracer.parsers.primitives import (
    KNOWN_PASSIVE_PREFIXES,
    _derive_pins_from_instances,
    _leaf_first_char,
    _pins_by_terminal_count,
)


def test_known_passive_prefixes():
    """Test that KNOWN_PASSIVE_PREFIXES skip-list is defined (AC34).

    AC34 uses a skip-list to preserve lateral-walk behavior. Passive devices
    (R/C/L/K/V/I) must remain unsynthesized so the tracer's BFS can detect
    walk-thru or skip behavior.
    """
    assert KNOWN_PASSIVE_PREFIXES is not None
    assert isinstance(KNOWN_PASSIVE_PREFIXES, frozenset)
    assert "R" in KNOWN_PASSIVE_PREFIXES
    assert "C" in KNOWN_PASSIVE_PREFIXES
    assert "L" in KNOWN_PASSIVE_PREFIXES
    assert "K" in KNOWN_PASSIVE_PREFIXES
    assert "V" in KNOWN_PASSIVE_PREFIXES
    assert "I" in KNOWN_PASSIVE_PREFIXES
    # Non-passive prefixes should not be in the skip list
    assert "M" not in KNOWN_PASSIVE_PREFIXES
    assert "D" not in KNOWN_PASSIVE_PREFIXES
    assert "Q" not in KNOWN_PASSIVE_PREFIXES
    assert "J" not in KNOWN_PASSIVE_PREFIXES


def test_leaf_first_char_simple():
    """Test _leaf_first_char with simple instance names."""
    assert _leaf_first_char("M1") == "M"
    assert _leaf_first_char("m1") == "M"
    assert _leaf_first_char("R1") == "R"
    assert _leaf_first_char("d1") == "D"


def test_leaf_first_char_hierarchical_spf():
    """Test _leaf_first_char with hierarchical flat-SPF names (AC34)."""
    # Xtop/Isub/M1 should extract leaf M1 -> M
    assert _leaf_first_char("Xtop/Isub/M1") == "M"
    # X-prefixed instance with nested path
    assert _leaf_first_char("X1/subcell/M2") == "M"
    # SPICE-style dot separators
    assert _leaf_first_char("top.sub.R1") == "R"


def test_leaf_first_char_empty():
    """Test _leaf_first_char with empty/degenerate names."""
    assert _leaf_first_char("") == ""
    assert _leaf_first_char("/") == ""


def test_pins_by_terminal_count_2term():
    """Test pin labeling for 2-terminal devices (diode-like)."""
    pins = _pins_by_terminal_count(2)
    assert pins == ["A", "K"]


def test_pins_by_terminal_count_3term():
    """Test pin labeling for 3-terminal devices (FET-like)."""
    pins = _pins_by_terminal_count(3)
    assert pins == ["D", "G", "S"]


def test_pins_by_terminal_count_4term():
    """Test pin labeling for 4-terminal devices (4-term FET)."""
    pins = _pins_by_terminal_count(4)
    assert pins == ["D", "G", "S", "B"]


def test_pins_by_terminal_count_positional():
    """Test pin labeling for 5+ terminals (positional fallback)."""
    pins = _pins_by_terminal_count(5)
    assert pins == ["1", "2", "3", "4", "5"]


def test_derive_pins_no_annotations_uses_terminal_count():
    """Test AC36: when no SPF :pin annotations present, use terminal-count fallback."""
    # Instances with NO :pin annotations (pure SPICE, not SPF)
    inst1 = Instance(name="M1", cell_type="nch_model", nets=["d", "g", "s", "b"], parent_cell="top")
    inst2 = Instance(
        name="M2", cell_type="nch_model", nets=["d2", "g2", "s2", "b2"], parent_cell="top"
    )

    pins = _derive_pins_from_instances([inst1, inst2], 4)
    # No annotations, so should use terminal-count fallback: ['D', 'G', 'S', 'B']
    assert pins == ["D", "G", "S", "B"], f"Expected terminal-count fallback, got {pins}"


def test_derive_pins_mixed_annotations():
    """Test AC36: mix of annotated and unannotated positions."""
    # Create instances with partial :pin annotations
    inst1 = Instance(
        name="Xblk1/M_n1",
        cell_type="nmos_model",
        nets=["blk1/M_n1:D", "G_net", "S_net", "B_net"],
        parent_cell="top",
    )

    pins = _derive_pins_from_instances([inst1], 4)
    # Position 0: matches "blk1/M_n1:D" -> extract "D"
    # Positions 1-3: no annotations -> use terminal-count fallback (G, S, B)
    assert pins[0] == "D", f"Position 0 should extract 'D' from annotation, got {pins[0]}"
    assert pins[1] == "G", f"Position 1 should use terminal-count fallback 'G', got {pins[1]}"
    assert pins[2] == "S", f"Position 2 should use terminal-count fallback 'S', got {pins[2]}"
    assert pins[3] == "B", f"Position 3 should use terminal-count fallback 'B', got {pins[3]}"


def test_primitive_synthesis_spf_mosfet(synthetic_primitive_mosfet_spf):
    """Test primitive synthesis on an SPF with MOSFET primitives (AC34, AC36)."""
    # Parse the SPF fixture with real MOSFET primitives
    parser = NetlistParser(synthetic_primitive_mosfet_spf, format="spf")
    # After parsing, the MOSFET cell_type should have been synthesized
    assert "nmos_model" in parser.subckts, "MOSFET cell_type not synthesized"
    sub = parser.subckts["nmos_model"]
    # AC36: Check that pins are derived from SPF :pin annotations or positional fallback
    # Expected pins from AC36 extraction (with fallback)
    assert len(sub.pins) == 4, f"Expected 4 pins, got {len(sub.pins)}"
    # Verify pin_to_pos mapping
    pin_names = sub.pins
    assert len(pin_names) == 4, f"MOSFET should have 4 pins, got {pin_names}"


def test_primitive_synthesis_no_overwrite():
    """Test that primitive synthesis doesn't overwrite existing SubcktDefs."""
    # Create a synthetic SPF with explicit subckt + primitive instance
    spf_content = """.SUBCKT custom_model D G S B
* Custom MOSFET model
.ENDS custom_model

.SUBCKT top_cell
M1 d_net g_net s_net b_net custom_model W=1u L=0.1u
.ENDS top_cell
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sp", delete=False) as f:
        f.write(spf_content)
        f.flush()
        temp_file = f.name

    try:
        parser = NetlistParser(temp_file)
        # custom_model should exist and NOT be overwritten
        assert "custom_model" in parser.subckts
        custom = parser.subckts["custom_model"]
        # Should have the pins from the explicit .SUBCKT line (D G S B)
        assert "D" in custom.pins
        assert len(custom.pins) == 4
    finally:
        os.unlink(temp_file)


def test_primitive_synthesis_verilog_no_op(vendored_picorv32_v):
    """Test that primitive synthesis is a no-op on Verilog (AC14)."""
    parser = NetlistParser(vendored_picorv32_v)
    # picorv32 is pure Verilog; no primitives should be synthesized
    # (synthesis only applies to SPICE primitives with known prefixes)
    # AC34: synthesis only triggers if instance leaf name starts with a prefix
    # that is NOT in KNOWN_PASSIVE_PREFIXES. Verilog instances don't use
    # SPICE prefixes, so synthesis is a no-op.
    initial_subckt_count = len(parser.subckts)
    assert initial_subckt_count > 0, "picorv32 should have subckts"
    # Confirm no Verilog module names were synthesized as primitives
    # by checking that none are single-character SPICE device names
    spice_primitive_names = {"M", "D", "Q", "J", "R", "C", "L", "V", "I", "K"}
    synthetic_primitives = [
        name for name in parser.subckts.keys() if name.upper() in spice_primitive_names
    ]
    # Should be zero (Verilog has no SPICE primitives)
    assert len(synthetic_primitives) == 0, (
        f"Found SPICE primitives in Verilog: {synthetic_primitives}"
    )


def test_primitive_leaf_name_extraction():
    """Test AC29: leaf instance name extraction for hierarchical SPF names."""
    # Test Xtop/Isub/M1 -> extracts leaf M1 -> M prefix
    spf_content = """.SUBCKT top_cell d_net g_net s_net b_net
* Hierarchical instance names (flat SPF style)
Xtop/Isub/M1 d_net g_net s_net b_net nmos_model W=1u L=0.1u
.ENDS top_cell
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spf", delete=False) as f:
        f.write(spf_content)
        f.flush()
        temp_file = f.name

    try:
        parser = NetlistParser(temp_file, format="spf")
        # The nmos_model should be synthesized (M prefix in leaf M1)
        assert "nmos_model" in parser.subckts, (
            "nmos_model should be synthesized (AC29 leaf extraction)"
        )
        sub = parser.subckts["nmos_model"]
        # Should have 4 pins (AC36 extraction or fallback)
        assert len(sub.pins) == 4, f"Expected 4 pins, got {len(sub.pins)}"
    finally:
        os.unlink(temp_file)
