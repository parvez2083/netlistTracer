"""Unit tests for SPICE parser (PHASE 10)."""

from __future__ import annotations

import os
import tempfile

import pytest

from netlist_tracer import NetlistParser
from netlist_tracer.exceptions import NetlistParseError

SYNTHETIC_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "synthetic")


def test_spice_basic_parse(synthetic_spice_basic_sp):
    """Test parsing basic SPICE netlist."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    assert parser.format == "spice"
    assert len(parser.subckts) > 0, "SPICE parser should find subcircuits"


def test_spice_basic_instances(synthetic_spice_basic_sp):
    """Test that SPICE parser finds instances."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    total_instances = sum(len(v) for v in parser.instances_by_parent.values())
    assert total_instances > 0, "SPICE netlist should have instances"


def test_spice_basic_pins(synthetic_spice_basic_sp):
    """Test that SPICE parser extracts pins."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    for sub in parser.subckts.values():
        assert hasattr(sub, "pins"), "Subckt should have pins attribute"
        assert isinstance(sub.pins, list), "Pins should be a list"


def test_spice_validation(synthetic_spice_basic_sp):
    """Test SPICE parser connection validation."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    mismatches = parser.validate_connections(verbose=False)
    # Basic fixture should have no pin mismatches
    assert isinstance(mismatches, list), "validate_connections should return a list"


def test_cdl_supply_constant_subckts(synthetic_supply_constant_cdl):
    """Test parsing CDL supply_constant subcircuit definitions."""
    parser = NetlistParser(synthetic_supply_constant_cdl)
    assert "supply_constant" in parser.subckts
    assert "top_supply" in parser.subckts
    assert parser.subckts["supply_constant"].pins == ["VDD", "VSS"]


def test_cdl_supply_constant_instance(synthetic_supply_constant_cdl):
    """Test parsing CDL supply_constant instances."""
    parser = NetlistParser(synthetic_supply_constant_cdl)
    insts = parser.instances_by_parent.get("top_supply", [])
    assert len(insts) == 1
    assert insts[0].cell_type == "supply_constant"


def test_spice_flat_deck_synthesizes_top(synthetic_spice_flat_deck_sp):
    """Test that SPICE flat-deck testbench synthesizes a synthetic top-level cell."""
    parser = NetlistParser(synthetic_spice_flat_deck_sp)
    assert "__spice_flat_deck__" in parser.subckts, (
        f"Synthetic top '__spice_flat_deck__' not found. Subckts: {list(parser.subckts.keys())}"
    )
    synthetic_top = parser.subckts["__spice_flat_deck__"]
    assert synthetic_top.pins == [], (
        f"Synthetic top should have empty pins, got {synthetic_top.pins}"
    )


def test_spice_flat_deck_top_level_instances_captured(synthetic_spice_flat_deck_sp):
    """Test that top-level X-instances are captured into the synthetic top."""
    parser = NetlistParser(synthetic_spice_flat_deck_sp)
    top_instances = parser.instances_by_parent.get("__spice_flat_deck__", [])
    assert len(top_instances) == 2, (
        f"Expected 2 top-level instances, got {len(top_instances)}: {top_instances}"
    )
    cell_types = {inst.cell_type for inst in top_instances}
    assert cell_types == {"top_block_a", "top_block_b"}, (
        f"Expected cell types {{'top_block_a', 'top_block_b'}}, got {cell_types}"
    )


def test_spice_hierarchical_no_synthesis(synthetic_spice_basic_sp):
    """Test that pure hierarchical SPICE decks do not synthesize a synthetic top."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    synthetic_tops = [name for name in parser.subckts.keys() if name.startswith("__")]
    assert len(synthetic_tops) == 0, (
        f"Hierarchical deck should not have synthetic tops, but found: {synthetic_tops}"
    )
    # Verify expected cells are still present
    assert "nand2" in parser.subckts
    assert "top_spice" in parser.subckts


class TestSpiceQuickwins:
    """Tests for SPICE parsing helpers: inline comments, continuations, controlled sources,
    coupled inductors, global directives, numerical parsing, and edge cases.
    """

    def test_spice_inline_comments_semicolon(self, synthetic_spice_inline_comments_sp):
        """Test inline semicolon comment stripping."""
        parser = NetlistParser(synthetic_spice_inline_comments_sp)
        # Verify X1 instance is parsed (line has ; comment at end)
        insts = parser.instances_by_parent.get("spice_inline_comments_cell", [])
        assert len(insts) >= 1, f"Expected at least 1 instance, got {len(insts)}"
        # Check first instance nets (should be stripped of comment)
        assert insts[0].nets == ["A", "B", "C"], f"Got nets {insts[0].nets}"

    def test_spice_inline_comments_dollar(self, synthetic_spice_inline_comments_sp):
        """Test inline dollar-sign comment stripping."""
        parser = NetlistParser(synthetic_spice_inline_comments_sp)
        insts = parser.instances_by_parent.get("spice_inline_comments_cell", [])
        # X2 has $ comment; verify it's parsed correctly
        x2_insts = [inst for inst in insts if inst.name == "X2"]
        assert len(x2_insts) == 1, "Expected X2 instance"
        assert x2_insts[0].nets == ["D", "E", "F"], f"Got nets {x2_insts[0].nets}"

    def test_spice_continuation_across_comment(
        self, synthetic_spice_continuation_across_comment_sp
    ):
        """Test continuation lines are merged properly across comment lines."""
        parser = NetlistParser(synthetic_spice_continuation_across_comment_sp)
        insts = parser.instances_by_parent.get("spice_continuation_cell", [])
        assert len(insts) == 1, f"Expected 1 instance, got {len(insts)}"
        # X1 should have all nets from continuation lines merged (A B C D E)
        assert insts[0].cell_type == "test_cell", f"Got cell_type {insts[0].cell_type}"
        assert len(insts[0].nets) == 5, (
            f"Expected 5 nets from merged continuations, got {len(insts[0].nets)}"
        )
        assert insts[0].nets == ["A", "B", "C", "D", "E"], f"Got nets {insts[0].nets}"

    def test_spice_b_element_behavioral_source(self, synthetic_spice_controlled_sources_sp):
        """Test B (behavioral source) element parsing."""
        parser = NetlistParser(synthetic_spice_controlled_sources_sp)
        # Look for B1 element
        b_insts = parser.instances_by_celltype.get("B_BSRC", [])
        assert len(b_insts) >= 1, f"Expected B_BSRC instance, got {len(b_insts)}"
        b1 = [inst for inst in b_insts if inst.name == "B1"]
        assert len(b1) == 1, "Expected B1 instance"
        assert b1[0].nets == ["OUT", "VSS"], f"Got nets {b1[0].nets}"

    def test_spice_e_element_vcvs(self, synthetic_spice_controlled_sources_sp):
        """Test E (voltage-controlled voltage source) element parsing."""
        parser = NetlistParser(synthetic_spice_controlled_sources_sp)
        e_insts = parser.instances_by_celltype.get("E_VCVS", [])
        assert len(e_insts) >= 1, f"Expected E_VCVS instance, got {len(e_insts)}"
        e1 = [inst for inst in e_insts if inst.name == "E1"]
        assert len(e1) == 1, "Expected E1 instance"
        # E1 has 4 nets: N1, VSS (output), IN, VSS (input)
        assert len(e1[0].nets) == 4, f"Expected 4 nets, got {len(e1[0].nets)}"

    def test_spice_g_element_vccs(self, synthetic_spice_controlled_sources_sp):
        """Test G (voltage-controlled current source) element parsing."""
        parser = NetlistParser(synthetic_spice_controlled_sources_sp)
        g_insts = parser.instances_by_celltype.get("G_VCCS", [])
        assert len(g_insts) >= 1, f"Expected G_VCCS instance, got {len(g_insts)}"
        g1 = [inst for inst in g_insts if inst.name == "G1"]
        assert len(g1) == 1, "Expected G1 instance"
        assert len(g1[0].nets) == 4, f"Expected 4 nets, got {len(g1[0].nets)}"

    def test_spice_f_element_cccs(self, synthetic_spice_controlled_sources_sp):
        """Test F (current-controlled current source) element parsing."""
        parser = NetlistParser(synthetic_spice_controlled_sources_sp)
        f_insts = parser.instances_by_celltype.get("F_CCCS", [])
        assert len(f_insts) >= 1, f"Expected F_CCCS instance, got {len(f_insts)}"
        f1 = [inst for inst in f_insts if inst.name == "F1"]
        assert len(f1) == 1, "Expected F1 instance"
        assert f1[0].nets == ["N3", "VSS"], f"Got nets {f1[0].nets}"
        assert "_vctrl" in f1[0].params, "Expected _vctrl in params"

    def test_spice_h_element_ccvs(self, synthetic_spice_controlled_sources_sp):
        """Test H (current-controlled voltage source) element parsing."""
        parser = NetlistParser(synthetic_spice_controlled_sources_sp)
        h_insts = parser.instances_by_celltype.get("H_CCVS", [])
        assert len(h_insts) >= 1, f"Expected H_CCVS instance, got {len(h_insts)}"
        h1 = [inst for inst in h_insts if inst.name == "H1"]
        assert len(h1) == 1, "Expected H1 instance"
        assert h1[0].nets == ["N4", "VSS"], f"Got nets {h1[0].nets}"
        assert "_vctrl" in h1[0].params, "Expected _vctrl in params"

    def test_spice_k_element_coupled_inductor(self, synthetic_spice_coupled_inductor_sp):
        """Test K (coupled inductor) element parsing."""
        parser = NetlistParser(synthetic_spice_coupled_inductor_sp)
        k_insts = parser.instances_by_celltype.get("K_COUPLED", [])
        assert len(k_insts) >= 1, f"Expected K_COUPLED instance, got {len(k_insts)}"
        k1 = [inst for inst in k_insts if inst.name == "K1"]
        assert len(k1) == 1, "Expected K1 instance"
        # nets should hold inductor names, not nodes
        assert k1[0].nets == ["L1", "L2"], f"Got nets {k1[0].nets}"
        assert "_k_coeff" in k1[0].params, "Expected _k_coeff in params"

    def test_spice_global_directive_captured(self, synthetic_spice_global_directive_sp):
        """Test .global directive is captured into parser.global_nets."""
        parser = NetlistParser(synthetic_spice_global_directive_sp)
        assert hasattr(parser, "global_nets"), "Parser should have global_nets attribute"
        assert len(parser.global_nets) == 3, (
            f"Expected 3 global nets, got {len(parser.global_nets)}"
        )
        assert "VDD" in parser.global_nets, "VDD should be in global_nets"
        assert "VSS" in parser.global_nets, "VSS should be in global_nets"
        assert "GND" in parser.global_nets, "GND should be in global_nets"

    def test_parse_numerical_meg_vs_m(self):
        """Test MEG (mega, 1e6) vs M (milli, 1e-3) distinction."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("1MEG") == 1e6, "1MEG should be 1,000,000"
        assert parse_numerical("1M") == 1e-3, "1M should be 0.001"
        assert parse_numerical("1meg") == 1e6, "1meg (lowercase) should be 1,000,000"
        assert parse_numerical("1m") == 1e-3, "1m (lowercase) should be 0.001"

    def test_parse_numerical_all_suffixes(self):
        """Test all SPICE/HSPICE unit suffixes."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("1T") == 1e12, "T (tera)"
        assert parse_numerical("1G") == 1e9, "G (giga)"
        assert parse_numerical("1K") == 1e3, "K (kilo)"
        assert parse_numerical("1U") == 1e-6, "U (micro)"
        assert parse_numerical("1N") == 1e-9, "N (nano)"
        assert parse_numerical("1P") == 1e-12, "P (pico)"
        assert parse_numerical("1F") == 1e-15, "F (femto)"
        assert parse_numerical("1A") == 1e-18, "A (atto)"

    def test_parse_numerical_unicode_mu(self):
        """Test Unicode micro symbol (μ)."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("1μ") == 1e-6, "Unicode μ should be micro"

    def test_parse_numerical_case_insensitivity(self):
        """Test case insensitivity for all suffixes."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("1K") == parse_numerical("1k"), "K/k equivalence"
        assert parse_numerical("1T") == parse_numerical("1t"), "T/t equivalence"
        assert parse_numerical("1MEG") == parse_numerical("1Meg"), "MEG/Meg equivalence"

    def test_parse_numerical_scientific_notation(self):
        """Test plain scientific notation (no suffix)."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("1.5e-9") == 1.5e-9, "Scientific notation 1.5e-9"
        assert parse_numerical("2.0e6") == 2.0e6, "Scientific notation 2.0e6"
        assert parse_numerical("1e12") == 1e12, "Scientific notation 1e12"

    def test_parse_numerical_plain_floats(self):
        """Test plain floating point numbers."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("3.14") == 3.14, "Plain float 3.14"
        assert parse_numerical("0.001") == 0.001, "Plain float 0.001"
        assert parse_numerical("1000") == 1000.0, "Plain integer 1000"

    def test_parse_numerical_none_on_garbage(self):
        """Test None return on garbage input."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("abc") is None, "Garbage 'abc' should return None"
        assert parse_numerical("12X34") is None, "Invalid '12X34' should return None"
        assert parse_numerical("M2") is None, "Invalid 'M2' should return None"

    def test_parse_numerical_none_on_empty_string(self):
        """Test None return on empty string."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical("") is None, "Empty string should return None"
        assert parse_numerical("   ") is None, "Whitespace-only should return None"

    def test_parse_numerical_none_on_non_string(self):
        """Test None return on non-string input."""
        from netlist_tracer.parsers._numerics import parse_numerical

        assert parse_numerical(None) is None, "None input should return None"
        assert parse_numerical(123) is None, "Integer input should return None"
        assert parse_numerical(1.5) is None, "Float input should return None"

    def test_spice_edge_case_crlf(self, synthetic_spice_edge_crlf_sp):
        """Test CRLF line endings parse without error."""
        parser = NetlistParser(synthetic_spice_edge_crlf_sp)
        assert parser.format == "spice", f"Format should be spice, got {parser.format}"
        assert "test_crlf" in parser.subckts, "test_crlf should be in subckts"

    def test_spice_edge_case_utf8_bom(self, synthetic_spice_edge_utf8_bom_sp):
        """Test UTF-8 BOM handling."""
        parser = NetlistParser(synthetic_spice_edge_utf8_bom_sp)
        assert parser.format == "spice", f"Format should be spice, got {parser.format}"
        assert "test_bom" in parser.subckts, "test_bom should be in subckts"

    def test_spice_edge_case_long_line(self, synthetic_spice_edge_long_line_sp):
        """Test very long netlist line."""
        parser = NetlistParser(synthetic_spice_edge_long_line_sp)
        insts = parser.instances_by_parent.get("edge_long_line_cell", [])
        assert len(insts) >= 1, f"Expected at least 1 instance, got {len(insts)}"

    def test_spice_edge_case_tab_continuation(self, synthetic_spice_edge_tab_continuation_sp):
        """Test tab characters in continuation lines."""
        parser = NetlistParser(synthetic_spice_edge_tab_continuation_sp)
        insts = parser.instances_by_parent.get("edge_tab_continuation_cell", [])
        assert len(insts) >= 1, f"Expected at least 1 instance, got {len(insts)}"

    def test_spice_edge_case_mixed_case(self, synthetic_spice_edge_mixed_case_sp):
        """Test mixed case keywords and identifiers."""
        parser = NetlistParser(synthetic_spice_edge_mixed_case_sp)
        assert "edge_mixed_case_cell" in parser.subckts, "edge_mixed_case_cell should be in subckts"


class TestSpiceIncludeSupport:
    """Tests for nested includes, cycle detection, and search paths."""

    def test_nested_include_2_levels(self) -> None:
        """Parse top.sp which includes mid.sp which includes leaf.sp."""
        top_file = os.path.join(SYNTHETIC_DIR, "include_2level_top.sp")
        parser = NetlistParser(top_file)

        # All three subckts should be visible: TOP, MID, LEAF
        assert "TOP" in parser.subckts
        assert "MID" in parser.subckts
        assert "LEAF" in parser.subckts

    def test_include_cycle_3_files_raises(self) -> None:
        """Parse a.sp -> b.sp -> c.sp -> a.sp cycle; must raise NetlistParseError."""
        cycle_file = os.path.join(SYNTHETIC_DIR, "include_cycle_a.sp")

        with pytest.raises(NetlistParseError) as exc_info:
            NetlistParser(cycle_file)

        error_msg = str(exc_info.value)
        assert "cycle" in error_msg.lower()

    def test_include_search_path_relative_to_includer(self) -> None:
        """Parent.sp in tmpdir/ includes child.sp in tmpdir/sub/.
        Should resolve relative-path includes without needing -I flag.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create child.sp in tmpdir/sub/
            sub_dir = os.path.join(tmpdir, "sub")
            os.makedirs(sub_dir)
            child_file = os.path.join(sub_dir, "child.sp")
            with open(child_file, "w") as f:
                f.write(".subckt CHILD a b\n")
                f.write("R1 a b res=1k\n")
                f.write(".ends CHILD\n")

            # Create parent.sp in tmpdir/
            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".include 'sub/child.sp'\n")
                f.write(".subckt PARENT a b\n")
                f.write("X1 a b CHILD\n")
                f.write(".ends PARENT\n")

            # Resolve should succeed via relative path from parent's directory
            parser = NetlistParser(parent_file)
            assert "PARENT" in parser.subckts
            assert "CHILD" in parser.subckts

    def test_include_search_path_via_include_paths(self) -> None:
        """Parent.sp includes child.sp. With include_paths=[dir2],
        should resolve child.sp from dir2.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create parent.sp in tmpdir
            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".subckt PARENT a b\n")
                f.write(".include 'child.sp'\n")
                f.write("X1 a b CHILD\n")
                f.write(".ends PARENT\n")

            # Create a separate directory with child.sp
            child_dir = os.path.join(tmpdir, "child_dir")
            os.makedirs(child_dir)
            child_file = os.path.join(child_dir, "child.sp")
            with open(child_file, "w") as f:
                f.write(".subckt CHILD a b\n")
                f.write("R1 a b res=1k\n")
                f.write(".ends CHILD\n")

            # Parse parent.sp with include_paths=[child_dir]
            parser = NetlistParser(parent_file, include_paths=[child_dir])
            assert "PARENT" in parser.subckts
            assert "CHILD" in parser.subckts

    def test_include_env_var_expansion(self, monkeypatch) -> None:
        """`.include` with `$VAR/...` form resolves via os.path.expandvars.

        v0.3.1 added environment variable expansion to _resolve_include_path so
        PDK-style paths like `.include '$PDK_ROOT/models.lib'` resolve at parse
        time using the current process environment.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            child_dir = os.path.join(tmpdir, "child_dir")
            os.makedirs(child_dir)
            child_file = os.path.join(child_dir, "child.sp")
            with open(child_file, "w") as f:
                f.write(".subckt CHILD a b\n")
                f.write("R1 a b 1k\n")
                f.write(".ends CHILD\n")

            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".include '$NETTRACE_TEST_DIR/child.sp'\n")
                f.write(".subckt TOP a b\n")
                f.write("X1 a b CHILD\n")
                f.write(".ends TOP\n")

            monkeypatch.setenv("NETTRACE_TEST_DIR", child_dir)
            parser = NetlistParser(parent_file)
            assert "TOP" in parser.subckts
            assert "CHILD" in parser.subckts, "Env-var-expanded include path should have resolved"

    def test_include_unresolvable_raises(self) -> None:
        """Parent.sp includes non-existent file. Must raise NetlistParseError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".subckt PARENT a b\n")
                f.write(".include 'does_not_exist.sp'\n")
                f.write(".ends PARENT\n")

            with pytest.raises(NetlistParseError) as exc_info:
                NetlistParser(parent_file)

            error_msg = str(exc_info.value)
            assert "does_not_exist.sp" in error_msg or "not found" in error_msg.lower()

    def test_include_diamond_not_cycle(self) -> None:
        """a.sp includes b.sp and c.sp; b.sp includes d.sp; c.sp includes d.sp.
        This is NOT a cycle (d.sp appears in only one branch's stack at a time).
        Parsing must succeed.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create d.sp
            d_file = os.path.join(tmpdir, "d.sp")
            with open(d_file, "w") as f:
                f.write(".subckt D a b\n")
                f.write("R1 a b res=1k\n")
                f.write(".ends D\n")

            # Create b.sp (includes d)
            b_file = os.path.join(tmpdir, "b.sp")
            with open(b_file, "w") as f:
                f.write(".include 'd.sp'\n")
                f.write(".subckt B a b\n")
                f.write("X1 a b D\n")
                f.write(".ends B\n")

            # Create c.sp (includes d)
            c_file = os.path.join(tmpdir, "c.sp")
            with open(c_file, "w") as f:
                f.write(".include 'd.sp'\n")
                f.write(".subckt C a b\n")
                f.write("X1 a b D\n")
                f.write(".ends C\n")

            # Create a.sp (includes b and c)
            a_file = os.path.join(tmpdir, "a.sp")
            with open(a_file, "w") as f:
                f.write(".include 'b.sp'\n")
                f.write(".include 'c.sp'\n")
                f.write(".subckt A a b c d\n")
                f.write("X1 a b B\n")
                f.write("X2 c d C\n")
                f.write(".ends A\n")

            # This should parse successfully
            parser = NetlistParser(a_file)
            assert "A" in parser.subckts
            assert "B" in parser.subckts
            assert "C" in parser.subckts
            assert "D" in parser.subckts

    def test_include_quoted_and_bare_paths(self) -> None:
        """Variants `.include "path"`, `.include 'path'`, `.include path` all work."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create child.sp
            child_file = os.path.join(tmpdir, "child.sp")
            with open(child_file, "w") as f:
                f.write(".subckt CHILD a b\n")
                f.write("R1 a b res=1k\n")
                f.write(".ends CHILD\n")

            # Test double-quoted
            parent1 = os.path.join(tmpdir, "parent1.sp")
            with open(parent1, "w") as f:
                f.write('.include "child.sp"\n')
                f.write(".subckt PARENT1 a b\n")
                f.write("X1 a b CHILD\n")
                f.write(".ends PARENT1\n")

            parser1 = NetlistParser(parent1)
            assert "CHILD" in parser1.subckts

            # Test single-quoted
            parent2 = os.path.join(tmpdir, "parent2.sp")
            with open(parent2, "w") as f:
                f.write(".include 'child.sp'\n")
                f.write(".subckt PARENT2 a b\n")
                f.write("X1 a b CHILD\n")
                f.write(".ends PARENT2\n")

            parser2 = NetlistParser(parent2)
            assert "CHILD" in parser2.subckts

            # Test bare path
            parent3 = os.path.join(tmpdir, "parent3.sp")
            with open(parent3, "w") as f:
                f.write(".include child.sp\n")
                f.write(".subckt PARENT3 a b\n")
                f.write("X1 a b CHILD\n")
                f.write(".ends PARENT3\n")

            parser3 = NetlistParser(parent3)
            assert "CHILD" in parser3.subckts

    def test_include_inc_alias(self) -> None:
        """`.inc path` is an alias for `.include path`. Must resolve."""
        with tempfile.TemporaryDirectory() as tmpdir:
            child_file = os.path.join(tmpdir, "child.sp")
            with open(child_file, "w") as f:
                f.write(".subckt CHILD a b\n")
                f.write("R1 a b res=1k\n")
                f.write(".ends CHILD\n")

            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".inc 'child.sp'\n")
                f.write(".subckt PARENT a b\n")
                f.write("X1 a b CHILD\n")
                f.write(".ends PARENT\n")

            parser = NetlistParser(parent_file)
            assert "CHILD" in parser.subckts

    def test_include_self_cycle(self) -> None:
        """Self-referential file include. Must raise NetlistParseError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            self_file = os.path.join(tmpdir, "self.sp")
            with open(self_file, "w") as f:
                f.write(".include 'self.sp'\n")
                f.write(".subckt SELF a b\n")
                f.write("R1 a b res=1k\n")
                f.write(".ends SELF\n")

            with pytest.raises(NetlistParseError) as exc_info:
                NetlistParser(self_file)

            error_msg = str(exc_info.value)
            assert "cycle" in error_msg.lower()

    def test_lib_directive_named_section_resolvable_inlines(self, caplog) -> None:
        """`.lib path SECTION` resolves and emits ONLY the matched section (v0.3.1).

        v0.3.1 (J): section-aware loading. The resolver scans the inlined file
        for `.lib SECTION ... .endl SECTION` markers and emits only the lines
        between them. Other sections in the file are ignored.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Multi-section .lib: TT corner has Q_TT, FF corner has Q_FF.
            lib_file = os.path.join(tmpdir, "transistor_lib.lib")
            with open(lib_file, "w") as f:
                f.write(".lib TT_CORNER\n")
                f.write(".subckt Q_TT c b e\n")
                f.write("Q1 c b e transistor_model\n")
                f.write(".ends Q_TT\n")
                f.write(".endl TT_CORNER\n")
                f.write(".lib FF_CORNER\n")
                f.write(".subckt Q_FF c b e\n")
                f.write("Q1 c b e transistor_model\n")
                f.write(".ends Q_FF\n")
                f.write(".endl FF_CORNER\n")

            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".lib 'transistor_lib.lib' TT_CORNER\n")
                f.write(".subckt TOP a b c\n")
                f.write("X1 a b c Q_TT\n")
                f.write(".ends TOP\n")

            parser = NetlistParser(parent_file)
            assert "TOP" in parser.subckts
            assert "Q_TT" in parser.subckts, (
                "Requested section TT_CORNER's content should be emitted"
            )
            assert "Q_FF" not in parser.subckts, (
                "Non-requested section FF_CORNER's content must NOT be emitted (v0.3.1)"
            )

    def test_lib_directive_named_section_section_not_found(self, caplog) -> None:
        """.lib path SECTION resolves but SECTION absent in file -> WARN + skip (v0.3.1).

        v0.3.1 (J): when the path resolves but the requested section name is
        not found inside the file, the include is skipped with a warning so
        the parent parse can continue.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_file = os.path.join(tmpdir, "transistor_lib.lib")
            with open(lib_file, "w") as f:
                f.write(".lib TT_CORNER\n")
                f.write(".subckt Q_TT c b e\n")
                f.write("Q1 c b e transistor_model\n")
                f.write(".ends Q_TT\n")
                f.write(".endl TT_CORNER\n")

            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".lib 'transistor_lib.lib' NONEXISTENT_SECTION\n")
                f.write(".subckt TOP a b c\n")
                f.write("R1 a b 1k\n")
                f.write(".ends TOP\n")

            with caplog.at_level("WARNING"):
                parser = NetlistParser(parent_file)
            assert "TOP" in parser.subckts, (
                "Parent must remain visible after section-not-found WARN+skip"
            )
            assert "Q_TT" not in parser.subckts, (
                "Section not requested -> nothing should be emitted"
            )
            assert any("section not found" in r.message.lower() for r in caplog.records), (
                f"Expected 'section not found' warning; got: {[r.message for r in caplog.records]}"
            )

    def test_lib_directive_named_section_unresolvable(self, caplog) -> None:
        """.lib path section with unresolvable path -> WARNING + skip, no raise."""
        with tempfile.TemporaryDirectory() as tmpdir:
            parent_file = os.path.join(tmpdir, "parent.sp")
            missing_path = os.path.join(tmpdir, "definitely_missing.lib")
            with open(parent_file, "w") as f:
                f.write(f".lib '{missing_path}' SOME_SECTION\n")
                f.write(".subckt TOP a b c\n")
                f.write("R1 a b 1k\n")
                f.write(".ends TOP\n")

            with caplog.at_level("WARNING"):
                parser = NetlistParser(parent_file)
            assert "TOP" in parser.subckts
            assert any("unresolvable" in r.message.lower() for r in caplog.records), (
                f"Expected unresolvable warning; got: {[r.message for r in caplog.records]}"
            )

    def test_lib_directive_bare_include(self) -> None:
        """`.lib path` (no section) inlines the entire file like `.include`."""
        with tempfile.TemporaryDirectory() as tmpdir:
            lib_file = os.path.join(tmpdir, "transistor_lib.lib")
            with open(lib_file, "w") as f:
                f.write(".subckt Q_NPNX c b e\n")
                f.write("Q1 c b e transistor_model\n")
                f.write(".ends Q_NPNX\n")

            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(".lib 'transistor_lib.lib'\n")
                f.write(".subckt TOP a b c\n")
                f.write("X1 a b c Q_NPNX\n")
                f.write(".ends TOP\n")

            parser = NetlistParser(parent_file)
            assert "TOP" in parser.subckts
            assert "Q_NPNX" in parser.subckts

    def test_lib_directive_bare_unresolvable(self, caplog) -> None:
        """Bare `.lib path` with unresolvable path -> WARNING + skip, no raise (v0.3.1).

        HSPICE files commonly contain intra-file `.lib SECTION_NAME` markers
        that open a section block. These are syntactically identical to a
        bare-form .lib path include directive. v0.3.1 extends the
        try-and-degrade pattern (already used for `.lib path section`) to
        the bare form so the parser doesn't abort on these markers.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            parent_file = os.path.join(tmpdir, "parent.sp")
            # 'tt_allDevices_post' is a typical HSPICE intra-file section
            # marker name; with no resolvable file, it should warn+skip.
            with open(parent_file, "w") as f:
                f.write(".lib tt_allDevices_post\n")
                f.write(".subckt TOP a b c\n")
                f.write("R1 a b 1k\n")
                f.write(".ends TOP\n")

            with caplog.at_level("WARNING"):
                parser = NetlistParser(parent_file)
            assert "TOP" in parser.subckts, (
                "Parent subckt must remain visible after bare .lib WARN+skip"
            )
            assert any("unresolvable" in r.message.lower() for r in caplog.records), (
                f"Expected unresolvable warning; got: {[r.message for r in caplog.records]}"
            )

    def test_include_directive_unresolvable_still_raises(self) -> None:
        """`.include` (NOT `.lib`) with unresolvable path STILL raises (v0.3.1).

        Confirms that v0.3.1's try-and-degrade scope is intentionally limited
        to `.lib` (best-effort PDK overlay) and does NOT extend to `.include`
        / `.inc` (explicit dependencies). This is the inverse-direction
        regression for deliverable H.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            parent_file = os.path.join(tmpdir, "parent.sp")
            missing_path = os.path.join(tmpdir, "definitely_missing.sp")
            with open(parent_file, "w") as f:
                f.write(f".include '{missing_path}'\n")
                f.write(".subckt TOP a b c\n")
                f.write("R1 a b 1k\n")
                f.write(".ends TOP\n")

            with pytest.raises(NetlistParseError, match="Include path not found"):
                NetlistParser(parent_file)

    def test_spectre_include(self) -> None:
        """Spectre include directive: `include "child.scs"` expands child subckt."""
        with tempfile.TemporaryDirectory() as tmpdir:
            child_file = os.path.join(tmpdir, "child.scs")
            with open(child_file, "w") as f:
                f.write("subckt foo a b\n")
                f.write("  r1 a b resistor r=1k\n")
                f.write("ends foo\n")

            parent_file = os.path.join(tmpdir, "parent.scs")
            with open(parent_file, "w") as f:
                f.write('include "child.scs"\n')
                f.write("subckt top x y\n")
                f.write("  x1 x y foo\n")
                f.write("ends top\n")

            # Should parse Spectre file and expand includes
            parser = NetlistParser(parent_file)
            assert "foo" in parser.subckts, "Child subckt should be parsed from include"
            assert "top" in parser.subckts

    def test_spectre_simulator_lang_spice_include(self) -> None:
        """Spectre `simulator lang=spice` block with SPICE .include directive expands correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Child SPICE file with SPICE syntax
            child_file = os.path.join(tmpdir, "child.sp")
            with open(child_file, "w") as f:
                f.write(".subckt bar a b\n")
                f.write("R1 a b 1k\n")
                f.write(".ends bar\n")

            # Parent Spectre file with simulator lang=spice block containing SPICE .include
            parent_file = os.path.join(tmpdir, "parent.scs")
            with open(parent_file, "w") as f:
                f.write("simulator lang=spice\n")
                f.write(".include 'child.sp'\n")  # SPICE include syntax (correct in spice mode)
                f.write("endsimulator\n")
                f.write("subckt top x y\n")
                f.write("  x1 x y bar\n")
                f.write("ends top\n")

            # Verify include expansion recognizes .include in spice mode
            from netlist_tracer.parsers.includes import expand_includes

            expanded_lines, _, _ = expand_includes(parent_file, "spectre")
            expanded_text = "\n".join([line[0] for line in expanded_lines])

            # The .subckt bar should be expanded from child.sp
            assert ".subckt bar" in expanded_text, (
                "SPICE .include should expand child SPICE content"
            )
            assert ".ends bar" in expanded_text, (
                "Expanded content should include complete subckt definition"
            )

    def test_spectre_include_section_resolvable_inlines(self, caplog) -> None:
        """Spectre `include "path" section=NAME` emits only the matched library (v0.3.1).

        v0.3.1 (J): Spectre section-aware loading scans the inlined file for
        `library NAME ... endlibrary NAME` markers and emits only the lines
        between them.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            child_file = os.path.join(tmpdir, "child.scs")
            with open(child_file, "w") as f:
                f.write("library SSG_PRE\n")
                f.write("subckt foo a b\n")
                f.write("  r1 a b resistor r=1k\n")
                f.write("ends foo\n")
                f.write("endlibrary SSG_PRE\n")
                f.write("library FFG_PRE\n")
                f.write("subckt bar a b\n")
                f.write("  r1 a b resistor r=2k\n")
                f.write("ends bar\n")
                f.write("endlibrary FFG_PRE\n")

            parent_file = os.path.join(tmpdir, "parent.scs")
            with open(parent_file, "w") as f:
                f.write('include "child.scs" section=SSG_PRE\n')
                f.write("subckt top x y\n")
                f.write("  x1 x y foo\n")
                f.write("ends top\n")

            parser = NetlistParser(parent_file)
            assert "top" in parser.subckts
            assert "foo" in parser.subckts, (
                "Requested library SSG_PRE's content should be emitted (v0.3.1)"
            )
            assert "bar" not in parser.subckts, (
                "Non-requested library FFG_PRE's content must NOT be emitted"
            )

    def test_spectre_include_section_unresolvable(self, monkeypatch, caplog) -> None:
        """Spectre `include "path" section=NAME` with unresolvable path -> WARNING + skip, no raise."""
        # Ensure the env var is NOT set in case the host shell has it.
        monkeypatch.delenv("NETTRACE_TEST_NONEXISTENT_VAR", raising=False)
        with tempfile.TemporaryDirectory() as tmpdir:
            parent_file = os.path.join(tmpdir, "parent.scs")
            with open(parent_file, "w") as f:
                f.write('include "$NETTRACE_TEST_NONEXISTENT_VAR/missing.slib" section=foo\n')
                f.write("subckt top x y\n")
                f.write("  r1 x y resistor r=1k\n")
                f.write("ends top\n")

            with caplog.at_level("WARNING"):
                parser = NetlistParser(parent_file)
            assert "top" in parser.subckts
            assert any("unresolvable" in r.message.lower() for r in caplog.records), (
                f"Expected unresolvable warning; got: {[r.message for r in caplog.records]}"
            )

    def test_lib_directive_same_file_two_sections_no_false_cycle(self) -> None:
        """L fix: .lib path SECTION_A and .lib path SECTION_B from same file must NOT trigger false cycle.

        v0.3.1 (L): cycle detection now keys on (path, section_filter) tuple, not just path.
        Two `.lib path SECTION_A` and `.lib path SECTION_B` calls into the same file are
        distinct logical include units and do not form a cycle.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Multi-section .lib: two corners with different subckts
            lib_file = os.path.join(tmpdir, "multi_corner.lib")
            with open(lib_file, "w") as f:
                f.write(".lib CORNER_FF\n")
                f.write(".subckt Q_FF c b e\n")
                f.write("Q1 c b e nmos_ff\n")
                f.write(".ends Q_FF\n")
                f.write(".endl CORNER_FF\n")
                f.write(".lib CORNER_SS\n")
                f.write(".subckt Q_SS c b e\n")
                f.write("Q1 c b e nmos_ss\n")
                f.write(".ends Q_SS\n")
                f.write(".endl CORNER_SS\n")

            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                # Include same file with different sections
                f.write(".lib 'multi_corner.lib' CORNER_FF\n")
                f.write(".lib 'multi_corner.lib' CORNER_SS\n")
                f.write(".subckt TOP a b c\n")
                f.write("X1 a b c Q_FF\n")
                f.write("X2 a b c Q_SS\n")
                f.write(".ends TOP\n")

            # This must parse successfully (no false cycle error)
            parser = NetlistParser(parent_file)
            assert "TOP" in parser.subckts
            assert "Q_FF" in parser.subckts
            assert "Q_SS" in parser.subckts

    def test_lib_directive_cycle_inside_file_hard_failure(self) -> None:
        """M fix: real cycle inside .lib file must propagate and exit 1, not degrade to WARNING.

        v0.3.1 (M): try-and-degrade now discriminates exception types. Only
        IncludePathNotFoundError (unresolvable paths) triggers degradation.
        Cycle-detection errors raise NetlistParseError and propagate, causing
        CLI exit code 1.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a cycle: a.lib includes b.lib, b.lib includes a.lib
            a_lib = os.path.join(tmpdir, "a.lib")
            b_lib = os.path.join(tmpdir, "b.lib")

            with open(a_lib, "w") as f:
                f.write(".lib CORNER_A\n")
                f.write(".subckt QA c b e\n")
                f.write("Q1 c b e nmos\n")
                f.write(".ends QA\n")
                f.write(f".include '{b_lib}'\n")
                f.write(".endl CORNER_A\n")

            with open(b_lib, "w") as f:
                f.write(".lib CORNER_B\n")
                f.write(".subckt QB c b e\n")
                f.write("Q1 c b e nmos\n")
                f.write(".ends QB\n")
                f.write(f".include '{a_lib}'\n")
                f.write(".endl CORNER_B\n")

            parent_file = os.path.join(tmpdir, "parent.sp")
            with open(parent_file, "w") as f:
                f.write(f".include '{a_lib}'\n")
                f.write(".subckt TOP a b c\n")
                f.write("X1 a b c QA\n")
                f.write(".ends TOP\n")

            # This must raise NetlistParseError (cycle detection), not degrade to warning
            with pytest.raises(NetlistParseError) as exc_info:
                NetlistParser(parent_file)

            error_msg = str(exc_info.value)
            assert "cycle" in error_msg.lower()


class TestSpicePeek:
    """Peek tests for SPICE/CDL format."""

    def test_peek_basic(self, synthetic_spice_basic_sp):
        """Test peek on basic SPICE file returns expected pins."""
        pns = NetlistParser.peek_pins(synthetic_spice_basic_sp, "nand2")
        assert pns is not None
        assert "Y" in pns
        assert "A" in pns
        assert "B" in pns
        assert "VDD" in pns
        assert "VSS" in pns

    def test_peek_top_cell(self, synthetic_spice_basic_sp):
        """Test peek finds pins for top-level cell."""
        pns = NetlistParser.peek_pins(synthetic_spice_basic_sp, "top_spice")
        assert pns is not None
        assert "VDD" in pns
        assert "VSS" in pns
        assert "A" in pns

    def test_peek_cell_not_found(self, synthetic_spice_basic_sp):
        """Test peek returns None for non-existent cell."""
        pns = NetlistParser.peek_pins(synthetic_spice_basic_sp, "NONEXISTENT")
        assert pns is None

    def test_peek_continuation_line(self, synthetic_spice_basic_sp):
        """Test that peek handles basic SPICE files."""
        # synthetic_spice_basic_sp has single-line subckt definitions
        # This test verifies peek works on it
        pns = NetlistParser.peek_pins(synthetic_spice_basic_sp, "nand2")
        assert pns is not None
        assert len(pns) > 0

    def test_peek_case_insensitive(self, synthetic_spice_basic_sp):
        """Test peek is case-insensitive for SPICE cell names."""
        pns_lower = NetlistParser.peek_pins(synthetic_spice_basic_sp, "nand2")
        pns_upper = NetlistParser.peek_pins(synthetic_spice_basic_sp, "NAND2")
        assert pns_lower is not None
        assert pns_upper is not None
        # Should find the same pins (in same order since both hit the same line)
        assert pns_lower == pns_upper

    def test_peek_nets_internal_in_continuation(self, synthetic_cdl_internal_nets_cdl):
        """Test peek_nets extracts internal nets from continuation lines."""
        # parent_cell has internal nets in multi-line instances
        nets = NetlistParser.peek_nets(synthetic_cdl_internal_nets_cdl, "parent_cell")
        assert nets is not None
        assert "internal_sig" in nets
        # internal_sig appears as a connection in the multi-line instance declaration
        # (it's internal because it's not a pin of parent_cell)

    def test_peek_nets_not_found(self, synthetic_cdl_internal_nets_cdl):
        """Test peek_nets returns None for non-existent cell."""
        nets = NetlistParser.peek_nets(synthetic_cdl_internal_nets_cdl, "NONEXISTENT")
        assert nets is None
