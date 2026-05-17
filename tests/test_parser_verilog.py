"""Unit tests for Verilog parser (PHASE 10)."""

from __future__ import annotations

import logging
import os
import re
import tempfile
from pathlib import Path

import pytest

from netlist_tracer import NetlistParser
from netlist_tracer.parsers.verilog.instances import _sv_parse_file


def test_verilog_concat_alias_parse(synthetic_concat_alias_v):
    """Test parsing Verilog with concatenation and aliases."""
    parser = NetlistParser(synthetic_concat_alias_v)
    assert parser.format == "verilog"
    assert len(parser.subckts) > 0, "Verilog parser should find modules"


def test_verilog_generate_loop_parse(synthetic_generate_loop_v):
    """Test parsing Verilog with generate loops."""
    parser = NetlistParser(synthetic_generate_loop_v)
    assert parser.format == "verilog"
    assert len(parser.subckts) > 0, "Generate loop fixture should parse"
    # Structural assertions for generate_loop module
    assert "generate_loop" in parser.subckts, "Should find generate_loop module"
    subckt = parser.subckts["generate_loop"]
    assert len(subckt.pins) == 8, (
        f"generate_loop should have 8 pins (4-bit in, 4-bit out), got {len(subckt.pins)}"
    )
    # Strong assertions: generate loop variable expansion produces concrete per-iteration aliases
    assert isinstance(subckt.aliases, dict), "Aliases should be extractable"
    assert len(subckt.aliases) == 4, (
        f"generate_loop should have 4 alias pairs (out[i]->in[i] for i=0..3), got {len(subckt.aliases)}"
    )
    expected_aliases = {
        "out[0]": "in[0]",
        "out[1]": "in[1]",
        "out[2]": "in[2]",
        "out[3]": "in[3]",
    }
    assert subckt.aliases == expected_aliases, (
        f"Aliases should match concrete per-iteration pairs, got {subckt.aliases}"
    )


def test_verilog_param_specialize_parse(synthetic_param_specialize_v):
    """Test parsing parameterized Verilog modules."""
    parser = NetlistParser(synthetic_param_specialize_v)
    assert parser.format == "verilog"
    # Should have both the parameterized module and the top module
    assert len(parser.subckts) >= 1, "Should find parameterized module instances"
    # Structural assertions for parameter specialization
    assert "param_specialize__WIDTH_16" in parser.subckts, "Should find WIDTH=16 specialized module"
    assert "top_param" in parser.instances_by_parent, "Should find top_param module instances"
    insts = parser.instances_by_parent["top_param"]
    assert len(insts) > 0, "top_param should have instances"
    assert insts[0].cell_type == "param_specialize__WIDTH_16", (
        f"Instance cell_type should be specialized, got {insts[0].cell_type}"
    )


def test_verilog_concat_aliases(synthetic_concat_alias_v):
    """Test that Verilog parser extracts aliases from assigns."""
    parser = NetlistParser(synthetic_concat_alias_v)
    for sub in parser.subckts.values():
        assert hasattr(sub, "aliases"), "Subckt should have aliases attribute"
        assert isinstance(sub.aliases, dict), "Aliases should be a dict"
    # Additional assertion: concat_alias module should have extracted aliases
    if "concat_alias" in parser.subckts:
        assert len(parser.subckts["concat_alias"].aliases) > 0, (
            "concat_alias should have at least one alias pair"
        )


def test_verilog_pins(synthetic_concat_alias_v):
    """Test that Verilog parser extracts pins correctly."""
    parser = NetlistParser(synthetic_concat_alias_v)
    for sub in parser.subckts.values():
        assert hasattr(sub, "pins"), "Subckt should have pins attribute"
        assert isinstance(sub.pins, list), "Pins should be a list"
        # concat_alias module should have a, b, y ports
        if sub.name == "concat_alias":
            assert len(sub.pins) == 3, f"concat_alias should have 3 pins, got {len(sub.pins)}"


def test_verilog_nested_generate_loop(synthetic_nested_generate_v):
    """Test parsing Verilog with nested 2-level generate-for loops (fixed-point unrolling).

    This fixture exercises the fixed-point unrolling capability: both outer and inner
    loop variables should be fully substituted with concrete indices in the aliases dict.
    Pins are expanded to individual bits by the Verilog parser (standard behavior).
    """
    parser = NetlistParser(synthetic_nested_generate_v)
    assert parser.format == "verilog"
    assert len(parser.subckts) > 0, "Nested generate fixture should parse"
    assert "nested_generate" in parser.subckts, "Should find nested_generate module"

    subckt = parser.subckts["nested_generate"]
    # Pins are expanded to individual bits: in0[0..3], in1[0..3], out0[0..3], out1[0..3] = 16 total
    assert len(subckt.pins) == 16, (
        f"nested_generate pins should be expanded to 16 individual bits, got {len(subckt.pins)}"
    )

    # Strong assertions: nested loop variable expansion produces concrete indices
    assert isinstance(subckt.aliases, dict), "Aliases should be extractable"

    # Expected aliases from nested loops:
    # Outer loop i in [0, 1], inner loop j in [0, 1]:
    #   out0[i] = in0[i] is assigned (2 * 2) = 4 times, but deduped to 2 unique pairs
    #   out1[j] = in1[j] is assigned (2 * 2) = 4 times, but deduped to 2 unique pairs
    # Total: exactly 4 unique pairs (out0[0]->in0[0], out0[1]->in0[1], out1[0]->in1[0], out1[1]->in1[1])
    assert len(subckt.aliases) == 4, (
        f"nested_generate should have exactly 4 alias pairs from 2x2 nested loops, got {len(subckt.aliases)}"
    )

    # Verify all alias keys/values are concrete bit indices (not loop variables)
    import re

    for key, val in subckt.aliases.items():
        assert key.count("[") == 1 and key.count("]") == 1, (
            f"Alias key {key!r} should be concrete bit form (e.g. out0[0]), not loop-variable form"
        )
        assert val.count("[") == 1 and val.count("]") == 1, (
            f"Alias value {val!r} should be concrete bit form (e.g. in0[0]), not loop-variable form"
        )
        # Verify no bare loop-variable names remain (word boundary check)
        assert not re.search(r"\b[ij]\b", key), (
            f"Alias key {key!r} should not contain loop variables (i, j)"
        )
        assert not re.search(r"\b[ij]\b", val), (
            f"Alias value {val!r} should not contain loop variables (i, j)"
        )

    # Verify specific aliases are present
    expected_pairs = {
        "out0[0]": "in0[0]",
        "out0[1]": "in0[1]",
        "out1[0]": "in1[0]",
        "out1[1]": "in1[1]",
    }
    for exp_key, exp_val in expected_pairs.items():
        assert exp_key in subckt.aliases, (
            f"Expected alias {exp_key!r} not found in {list(subckt.aliases.keys())}"
        )
        assert subckt.aliases[exp_key] == exp_val, (
            f"Expected {exp_key!r} -> {exp_val!r}, got {subckt.aliases[exp_key]!r}"
        )


class TestVerilogQuickwins:
    """Tests for Verilog quick-win features: generate-if, generate-case, primitives, defparam."""

    class TestGenerateIf:
        """Tests for generate-if conditional compilation."""

        def test_generate_if_smoke(self, synthetic_verilog_generate_if_v):
            """Smoke test: generate-if fixture parses without error."""
            parser = NetlistParser(synthetic_verilog_generate_if_v)
            assert "verilog_generate_if" in parser.subckts
            mod = parser.subckts["verilog_generate_if"]
            assert mod is not None

        def test_generate_if_branch_isolation(self, tmp_path):
            """generate-if with gate instances should select only the true branch and isolate it."""
            code = """
module gate_inst(input clk, output q);
  parameter DO_GATE = 1;
  generate
    if (DO_GATE > 0) begin : g_on
      and u_and (q, clk, clk);
    end else begin : g_off
      buf u_buf (q, clk);
    end
  endgenerate
endmodule
"""
            vfile = tmp_path / "test_gen_if_branch.v"
            vfile.write_text(code)
            parser = NetlistParser(str(vfile))
            insts = parser.instances_by_parent.get("gate_inst", [])
            names = [i.name for i in insts]

            # True branch (DO_GATE > 0) should be selected: u_and must be present
            assert "u_and" in names, f"Selected branch missing: {names}"
            # False branch should be isolated: u_buf must NOT be present
            assert "u_buf" not in names, f"Rejected branch leaked: {names}"

        def test_generate_if_with_direct_assignment(self, tmp_path):
            """Test generate-if with direct wire assignment (creates aliases)."""
            vfile = tmp_path / "test_gen_if_alias.v"
            vfile.write_text(
                """
module test_gen_if_alias #(parameter ENABLE = 1) (
  input wire [3:0] in,
  output wire [3:0] out
);
  generate
    if (ENABLE == 1) begin : enabled
      assign out = in;
    end else begin : disabled
      assign out = 4'b0000;
    end
  endgenerate
endmodule
"""
            )
            parser = NetlistParser(str(vfile))
            assert "test_gen_if_alias" in parser.subckts
            mod = parser.subckts["test_gen_if_alias"]
            # Should have parsed the true branch with direct assignment
            assert mod is not None

    class TestGenerateCase:
        """Tests for generate-case conditional compilation."""

        def test_generate_case_smoke(self, synthetic_verilog_generate_case_v):
            """Smoke test: generate-case fixture parses without error."""
            parser = NetlistParser(synthetic_verilog_generate_case_v)
            mod = parser.subckts["verilog_generate_case"]
            assert mod is not None

        def test_generate_case_branch_isolation(self, tmp_path):
            """generate-case with gate instances should select only the matching arm and isolate others."""
            code = """
module mode_select(input clk, output q);
  parameter MODE = 1;
  generate
    case (MODE)
      0: begin : arm_0
        not u_arm0_not (q, clk);
      end
      1: begin : arm_1
        buf u_arm1_buf (q, clk);
      end
      2: begin : arm_2
        and u_arm2_and (q, clk, clk);
      end
      default: begin : arm_default
        nor u_default_nor (q, clk, clk);
      end
    endcase
  endgenerate
endmodule
"""
            vfile = tmp_path / "test_gen_case_branch.v"
            vfile.write_text(code)
            parser = NetlistParser(str(vfile))
            insts = parser.instances_by_parent.get("mode_select", [])
            names = [i.name for i in insts]

            # MODE=1 is selected: u_arm1_buf must be present
            assert "u_arm1_buf" in names, f"Selected arm missing: {names}"
            # Other arms must be isolated: u_arm0_not, u_arm2_and, u_default_nor must NOT be present
            assert "u_arm0_not" not in names, f"Unselected arm 0 leaked: {names}"
            assert "u_arm2_and" not in names, f"Unselected arm 2 leaked: {names}"
            assert "u_default_nor" not in names, f"Unselected default leaked: {names}"

        def test_generate_case_default(self, tmp_path):
            """generate-case with unmatched value should pick default."""
            vfile = tmp_path / "test_gen_case_dflt.v"
            vfile.write_text(
                """
module test_gen_case_dflt #(parameter MODE = 99) (
  input wire [3:0] in,
  output wire [3:0] out
);
  generate
    case (MODE)
      1: begin : mode_1
        assign out = in;
      end
      2: begin : mode_2
        assign out = ~in;
      end
      default: begin : mode_default
        assign out = 4'b1111;
      end
    endcase
  endgenerate
endmodule
"""
            )
            parser = NetlistParser(str(vfile))
            mod = parser.subckts["test_gen_case_dflt"]
            assert mod is not None

    class TestGatePrimitives:
        """Tests for built-in gate primitive instantiation."""

        def test_primitive_instances_appear(self, synthetic_verilog_gate_primitives_v):
            """Primitive instances should be recognized with synthesized cell types."""
            parser = NetlistParser(synthetic_verilog_gate_primitives_v)
            mod = parser.subckts["verilog_gate_primitives"]
            assert mod is not None

            # Check that we have instances
            insts_in_module = parser.instances_by_parent.get("verilog_gate_primitives", [])
            assert len(insts_in_module) > 0

            # At least one instance should reference a synthesized primitive cell
            has_synthesized = any("__prim_" in inst.cell_type for inst in insts_in_module)
            assert has_synthesized, "Should have at least one synthesized primitive cell"

        def test_primitive_cell_definitions(self, synthetic_verilog_gate_primitives_v):
            """Synthesized primitive cell definitions should exist."""
            parser = NetlistParser(synthetic_verilog_gate_primitives_v)

            # Check for synthesized primitives in subckt dict
            syn_cells = {name for name in parser.subckts.keys() if "__prim_" in name}
            assert len(syn_cells) > 0, "Should have synthesized primitive cells"

            # Check that at least 'and' and 'nand' primitives are present
            expected_prims = {"__prim_and_3__", "__prim_nand_3__"}
            assert expected_prims.issubset(syn_cells), (
                f"Expected primitives {expected_prims} not found in {syn_cells}"
            )

    class TestDefparam:
        """Tests for defparam parameter override."""

        def test_defparam_override(self, synthetic_verilog_defparam_v):
            """defparam should override inline #() parameter values."""
            parser = NetlistParser(synthetic_verilog_defparam_v)
            mod_top = parser.subckts["verilog_defparam"]
            assert mod_top is not None

            # The module should have an instance of 'counter'
            insts_in_top = parser.instances_by_parent.get("verilog_defparam", [])
            assert len(insts_in_top) == 1
            inst = insts_in_top[0]
            assert inst.name == "u_counter"
            # Cell type may be specialized variant with parameters
            assert "counter" in inst.cell_type

            # The instance should have WIDTH parameter set by defparam
            # WIDTH=8 means the cell type should include __WIDTH_8
            assert "__WIDTH_8" in inst.cell_type

        def test_defparam_wins_over_inline(self, tmp_path):
            """defparam should take precedence over inline #() parameter."""
            vfile = tmp_path / "test_defparam.v"
            vfile.write_text(
                """
module sub #(parameter WIDTH = 4) (
  output wire [WIDTH-1:0] out
);
  assign out = {WIDTH{1'b0}};
endmodule

module top (
  output wire [7:0] result
);
  sub #(.WIDTH(4)) u_sub(.out(result[3:0]));
  defparam u_sub.WIDTH = 8;
endmodule
"""
            )
            parser = NetlistParser(str(vfile))
            insts = parser.instances_by_parent.get("top", [])
            assert len(insts) == 1
            # defparam should set WIDTH=8, which should result in cell_type specialized with WIDTH_8
            inst = insts[0]
            # The cell type should reflect the specialized parameter value from defparam
            assert "__WIDTH_8" in inst.cell_type

    def test_genvar_for_with_block_label(self, synthetic_genvar_for_label_v):
        """Test parsing generate-for with block label (genvar iteration).

        Regression test for bug where generate-for blocks with labels were
        pre-unrolled before the labeled extraction pass, losing the block label
        and producing duplicate instance names instead of labeled ones.

        Example: for (genvar i=0; i<3; i++) begin: gblock ... end
        Should produce instances named gblock[0].ucell, gblock[1].ucell, gblock[2].ucell
        NOT three instances all named ucell.
        """
        parser = NetlistParser(synthetic_genvar_for_label_v)
        assert parser.format == "verilog"
        assert len(parser.subckts) > 0, "genvar_for_label fixture should parse"
        assert "my_top" in parser.subckts, "Should find my_top module"
        assert "my_top" in parser.instances_by_parent, "Should find instances under my_top"

        insts = parser.instances_by_parent["my_top"]
        # Should have exactly 3 instances (one per iteration)
        assert len(insts) == 3, (
            f"my_top should have 3 instances (gblock[0].ucell, gblock[1].ucell, gblock[2].ucell), "
            f"got {len(insts)} instances: {[i.name for i in insts]}"
        )

        # Verify instance names include the block label with index
        inst_names = sorted([i.name for i in insts])
        expected_names = ["gblock[0].ucell", "gblock[1].ucell", "gblock[2].ucell"]
        assert inst_names == expected_names, (
            f"Instance names should be {expected_names}, got {inst_names}"
        )

        # Verify all instances reference the correct cell type
        for inst in insts:
            assert inst.cell_type == "my_cell", (
                f"Instance {inst.name} should reference my_cell, got {inst.cell_type}"
            )


class TestVerilogA:
    """Tests for Verilog-A parser support."""

    def test_verilog_a_leaf_detection(self, synthetic_verilog_a_leaf_va):
        """Test that Verilog-A leaf cells are correctly detected and parsed."""
        parser = NetlistParser(synthetic_verilog_a_leaf_va)
        assert parser.format == "verilog"
        assert "verilog_a_leaf" in parser.subckts, "Should detect va_leaf module"
        subckt = parser.subckts["verilog_a_leaf"]
        assert subckt.pins == ["inp", "outp"], (
            f"va_leaf should have pins ['inp', 'outp'], got {subckt.pins}"
        )

    def test_verilog_a_parent_with_leaf(self):
        """Test parsing parent module with instantiated Verilog-A leaf cell.

        Verifies that:
        1. Both parent and leaf modules are recognized
        2. Leaf module appears as a SubcktDef with correct pins
        3. Instance connection is registered
        4. Analog behavioral blocks are stripped (no parse errors)
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Verilog-A leaf
            leaf_path = os.path.join(tmpdir, "leaf.va")
            with open(leaf_path, "w") as f:
                f.write("module va_leaf (electrical inp, electrical outp);\n")
                f.write("  analog begin\n")
                f.write("    V(outp, inp) <+ 0.0;\n")
                f.write("  end\n")
                f.write("endmodule\n")

            # Create Verilog parent
            parent_path = os.path.join(tmpdir, "parent.v")
            with open(parent_path, "w") as f:
                f.write("module top (input a, output y);\n")
                f.write("  va_leaf u_leaf (.inp(a), .outp(y));\n")
                f.write("endmodule\n")

            # Parse directory
            parser = NetlistParser(tmpdir)

            # Verify both modules are recognized
            assert "va_leaf" in parser.subckts, "Should find va_leaf module"
            assert "top" in parser.subckts, "Should find top module"

            # Verify va_leaf pins (electrical ports treated as standard pins)
            va_leaf = parser.subckts["va_leaf"]
            assert va_leaf.pins == ["inp", "outp"], (
                f"va_leaf should have ['inp', 'outp'], got {va_leaf.pins}"
            )

            # Verify instance connection
            assert "top" in parser.instances_by_parent, "top should have instances"
            insts = parser.instances_by_parent["top"]
            assert len(insts) == 1, f"top should have 1 instance, got {len(insts)}"
            assert insts[0].name == "u_leaf", f"Instance name should be u_leaf, got {insts[0].name}"
            assert insts[0].cell_type == "va_leaf", (
                f"Instance cell_type should be va_leaf, got {insts[0].cell_type}"
            )

    def test_ahdl_include_resolves_relative_va(self):
        """Test ahdl_include directive resolves relative VA file paths."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create VA leaf in subdirectory
            subdir = os.path.join(tmpdir, "subdir")
            os.makedirs(subdir)
            va_leaf = os.path.join(subdir, "va_leaf.va")
            with open(va_leaf, "w") as f:
                f.write("module va_leaf (electrical inp, electrical outp);\n")
                f.write("  analog begin\n")
                f.write("    V(outp, inp) <+ 0.0;\n")
                f.write("  end\n")
                f.write("endmodule\n")

            # Create Spectre netlist with ahdl_include
            deck_path = os.path.join(tmpdir, "tb.scs")
            with open(deck_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write('ahdl_include "subdir/va_leaf.va"\n')
                f.write("subckt tb (vdd vss)\n")
                f.write("ends tb\n")

            # Parse the deck
            parser = NetlistParser(deck_path)

            # Verify va_leaf was loaded via ahdl_include
            assert "va_leaf" in parser.subckts, "va_leaf should be loaded from ahdl_include"
            assert parser.subckts["va_leaf"].pins == ["inp", "outp"]
            assert "tb" in parser.subckts, "tb subckt should also be present"

    def test_ahdl_include_resolves_env_var(self, monkeypatch):
        """Test ahdl_include directive resolves environment variables."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create VA leaf in tmpdir
            va_leaf = os.path.join(tmpdir, "va_leaf.va")
            with open(va_leaf, "w") as f:
                f.write("module va_leaf (electrical inp, electrical outp);\n")
                f.write("  analog begin\n")
                f.write("    V(outp, inp) <+ 0.0;\n")
                f.write("  end\n")
                f.write("endmodule\n")

            # Create Spectre netlist with ahdl_include using env var
            deck_path = os.path.join(tmpdir, "tb.scs")
            with open(deck_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write('ahdl_include "$VA_DIR/va_leaf.va"\n')
                f.write("subckt tb (vdd vss)\n")
                f.write("ends tb\n")

            # Set VA_DIR env var
            monkeypatch.setenv("VA_DIR", tmpdir)

            # Parse the deck
            parser = NetlistParser(deck_path)

            # Verify va_leaf was loaded
            assert "va_leaf" in parser.subckts, "va_leaf should be loaded from env var path"
            assert parser.subckts["va_leaf"].pins == ["inp", "outp"]

    def test_ahdl_include_missing_path_warns_and_continues(self, caplog):
        """Test ahdl_include with missing path emits WARNING and continues parsing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Spectre netlist with unresolvable ahdl_include
            deck_path = os.path.join(tmpdir, "tb.scs")
            with open(deck_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write('ahdl_include "definitely_not_there.va"\n')
                f.write("subckt tb (vdd vss)\n")
                f.write("  r1 (vdd vss) resistor r=1k\n")
                f.write("ends tb\n")

            # Capture warnings
            with caplog.at_level(logging.WARNING):
                parser = NetlistParser(deck_path)

            # Verify parser did NOT raise and parsed tb subckt
            assert "tb" in parser.subckts, "Parent subckt should parse despite missing ahdl_include"

            # Verify WARNING was logged
            warning_text = caplog.text.lower()
            assert "ahdl_include" in warning_text or "unresolvable" in warning_text, (
                f"Expected WARNING about ahdl_include, got: {caplog.text}"
            )

    def test_ahdl_include_va_module_can_be_instantiated(self):
        """REGRESSION TEST for v1.2 load-order fix.

        Tests that instances referencing ahdl_include'd Verilog-A modules are
        correctly registered. This test would FAIL before the load-order fix
        (when _load_ahdl_include_modules was called AFTER the second pass)
        because the instance would be dropped when cell_type lookup fails.

        Verifies that:
        1. VA module from ahdl_include is recognized and loaded
        2. Instance referencing the VA module is correctly registered
        3. Instance appears in instances_by_celltype
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Verilog-A leaf
            leaf_path = os.path.join(tmpdir, "leaf.va")
            with open(leaf_path, "w") as f:
                f.write("module va_leaf (electrical inp, electrical outp);\n")
                f.write("  analog begin\n")
                f.write("    V(outp, inp) <+ 0.0;\n")
                f.write("  end\n")
                f.write("endmodule\n")

            # Create Spectre netlist that ahdl_include's the VA file AND instantiates it
            deck_path = os.path.join(tmpdir, "tb.scs")
            with open(deck_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write('ahdl_include "leaf.va"\n')
                f.write("subckt parent_cell (a b)\n")
                f.write("  inst_1 (a b) va_leaf\n")
                f.write("ends parent_cell\n")

            # Parse the deck
            parser = NetlistParser(deck_path)

            # Verify va_leaf was loaded via ahdl_include
            assert "va_leaf" in parser.subckts, (
                "va_leaf should be loaded from ahdl_include and present in subckts"
            )
            assert parser.subckts["va_leaf"].pins == ["inp", "outp"], (
                f"va_leaf pins should be ['inp', 'outp'], got {parser.subckts['va_leaf'].pins}"
            )

            # Verify parent_cell was parsed
            assert "parent_cell" in parser.subckts, "parent_cell should be defined"

            # Verify the instance referencing va_leaf was registered
            assert "va_leaf" in parser.instances_by_celltype, (
                "va_leaf instances should be registered in instances_by_celltype"
            )
            insts = parser.instances_by_celltype["va_leaf"]
            assert len(insts) >= 1, (
                f"va_leaf should have at least 1 instance, but got {len(insts)}. "
                f"This indicates the load-order bug is present: instances of VA cells are dropped."
            )

            # Verify instance details
            inst = insts[0]
            assert inst.name == "inst_1", f"Instance name should be inst_1, got {inst.name}"
            assert inst.parent_cell == "parent_cell", (
                f"Instance parent should be parent_cell, got {inst.parent_cell}"
            )
            assert inst.nets == ["a", "b"], f"Instance nets should be ['a', 'b'], got {inst.nets}"


class TestSvInterface:
    """Tests for SystemVerilog interface parsing."""

    def test_simple_interface_creates_subckt(self) -> None:
        """Test that simple interface without modports creates a SubcktDef."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "simple_if.sv"
            content = """interface bus_if;
    logic [7:0] data;
    logic valid;
endinterface
"""
            fpath.write_text(content, encoding="utf-8")

            mdls = _sv_parse_file((str(fpath), {}, set(), {}))

            # Should have one interface entry
            assert len(mdls) >= 1
            intrfc = mdls[0]
            assert intrfc["name"] == "bus_if"
            assert intrfc["params"]["_kind"] == "interface"
            # Check pins (at least data and valid)
            pin_names = [p["name"] for p in intrfc["ports"]]
            assert "valid" in pin_names

    def test_interface_with_modports(self) -> None:
        """Test that interface with modports creates interface + modport SubcktDefs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "if_mport.sv"
            content = """interface bus_if (input logic clk);
    logic [7:0] data;
    logic valid;
    logic ready;

    modport master (
        output data, valid,
        input ready
    );

    modport slave (
        input data, valid,
        output ready
    );

endinterface
"""
            fpath.write_text(content, encoding="utf-8")

            mdls = _sv_parse_file((str(fpath), {}, set(), {}))

            # Should have interface + 2 modports = 3 entries
            assert len(mdls) >= 3

            intrfc = mdls[0]
            assert intrfc["name"] == "bus_if"
            assert intrfc["params"]["_kind"] == "interface"
            assert set(intrfc["params"]["_modports"]) == {"master", "slave"}

            # Check modports exist
            modport_names = [m["name"] for m in mdls[1:3]]
            assert "bus_if__mp_master" in modport_names
            assert "bus_if__mp_slave" in modport_names

    def test_modport_pin_directions_correct(self) -> None:
        """Test that modport pin directions are correctly extracted via full pipeline."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "if_mport_dir.sv"
            content = """interface bus_if;
    logic [7:0] data;
    logic valid;
    logic ready;

    modport master (
        output data,
        output valid,
        input ready
    );

endinterface
"""
            fpath.write_text(content, encoding="utf-8")

            # Use full pipeline via NetlistParser, not just _sv_parse_file
            parser = NetlistParser(str(fpath))

            # Find master modport SubcktDef
            master_subckt = parser.subckts.get("bus_if__mp_master")
            assert master_subckt is not None
            assert master_subckt.params["_kind"] == "modport"

            # Check directions: data should be expanded to bit-level keys (data[7]...data[0])
            drctn = master_subckt.params["_pin_directions"]
            # Verify bit-expanded keys exist for bus signal
            assert "data[7]" in drctn, (
                f"Expected data[7] in directions, got keys: {sorted(drctn.keys())}"
            )
            assert "data[0]" in drctn, (
                f"Expected data[0] in directions, got keys: {sorted(drctn.keys())}"
            )
            assert drctn["data[7]"] == "output"
            assert drctn["data[0]"] == "output"
            # Verify scalar signals
            assert drctn["valid"] == "output"
            assert drctn["ready"] == "input"

    def test_interface_no_ports_no_modports(self) -> None:
        """Test interface with no ports and no modports."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "trivial_if.sv"
            content = """interface trivial_if;
    logic a;
    logic b;
endinterface
"""
            fpath.write_text(content, encoding="utf-8")

            mdls = _sv_parse_file((str(fpath), {}, set(), {}))

            assert len(mdls) >= 1
            intrfc = mdls[0]
            assert intrfc["name"] == "trivial_if"
            assert intrfc["params"]["_kind"] == "interface"

            pin_names = [p["name"] for p in intrfc["ports"]]
            assert "a" in pin_names
            assert "b" in pin_names

    def test_interface_with_parameter(self) -> None:
        """Test interface with parameter."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "param_if.sv"
            content = """interface bus_if #(parameter WIDTH = 4) (input clk);
    logic [WIDTH-1:0] data;
endinterface
"""
            fpath.write_text(content, encoding="utf-8")

            mdls = _sv_parse_file((str(fpath), {}, set(), {}))

            assert len(mdls) >= 1
            intrfc = mdls[0]
            assert intrfc["name"] == "bus_if"
            # Should have clk and data pins
            pin_names = [p["name"] for p in intrfc["ports"]]
            assert "clk" in pin_names

    def test_existing_module_unchanged(self) -> None:
        """Test that existing module parsing is unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "module_test.v"
            content = """module test_mod (
    input clk,
    output reg ready
);
    reg valid;

    always @(posedge clk) begin
        ready <= valid;
    end
endmodule
"""
            fpath.write_text(content, encoding="utf-8")

            mdls = _sv_parse_file((str(fpath), {}, set(), {}))

            assert len(mdls) >= 1
            mod = mdls[0]
            assert mod["name"] == "test_mod"
            # Should NOT have interface params
            assert "params" not in mod or mod.get("params", {}).get("_kind") != "interface"

    def test_no_subckt_key_collisions(self) -> None:
        """Test that interface and modport keys don't collide."""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "no_collide.sv"
            content = """interface bus_if;
    logic [7:0] data;

    modport master (output data);
    modport slave (input data);
endinterface
"""
            fpath.write_text(content, encoding="utf-8")

            mdls = _sv_parse_file((str(fpath), {}, set(), {}))

            names = [m["name"] for m in mdls]
            assert "bus_if" in names
            assert "bus_if__mp_master" in names
            assert "bus_if__mp_slave" in names

            # Check uniqueness
            assert len(names) == len(set(names))

    @pytest.mark.local
    @pytest.mark.skipif(
        not os.environ.get("NETLIST_TRACER_SV_INTERFACE_CORPUS"),
        reason="NETLIST_TRACER_SV_INTERFACE_CORPUS not set",
    )
    def test_ac9_real_interface_corpus(self) -> None:
        """Regression: NT parses real-world .sv files containing SystemVerilog interface
        blocks. Auto-discovers a candidate file in the corpus directory to avoid
        hardcoding any private filename.
        """
        corpus_dir = Path(os.environ["NETLIST_TRACER_SV_INTERFACE_CORPUS"])
        assert corpus_dir.is_dir(), f"Corpus directory not found: {corpus_dir}"

        target = None
        for fp in sorted(corpus_dir.glob("*.sv")):
            try:
                head = fp.read_text(encoding="utf-8", errors="replace")[:8192]
            except OSError:
                continue
            if re.search(r"\binterface\s+\w+", head):
                target = fp
                break

        assert target is not None, (
            f"No .sv file containing an `interface` declaration found under {corpus_dir}"
        )

        parser = NetlistParser(str(target))
        assert len(parser.subckts) > 0, f"NT produced no SubcktDefs for {target.name}"

        interfaces = [
            (name, sub)
            for name, sub in parser.subckts.items()
            if sub.params.get("_kind") == "interface"
        ]
        assert interfaces, (
            f"Expected at least one interface SubcktDef in {target.name}; "
            f"found kinds: {sorted({sub.params.get('_kind', 'module') for sub in parser.subckts.values()})}"
        )


class TestVerilogPeek:
    """Peek tests for Verilog format."""

    def test_peek_single_file(self, synthetic_concat_alias_v):
        """Test peek on single Verilog file returns expected ports."""
        pns = NetlistParser.peek_pins(synthetic_concat_alias_v, "concat_alias")
        assert pns is not None
        assert len(pns) > 0

    def test_peek_not_found(self, synthetic_concat_alias_v):
        """Test peek returns None for non-existent module."""
        pns = NetlistParser.peek_pins(synthetic_concat_alias_v, "NONEXISTENT_MODULE")
        assert pns is None

    def test_peek_basic_module(self, synthetic_verilog_gate_primitives_v):
        """Test peek on Verilog gate primitives module."""
        pns = NetlistParser.peek_pins(
            synthetic_verilog_gate_primitives_v, "verilog_gate_primitives"
        )
        assert pns is not None
        assert len(pns) > 0
        # Should have the input/output ports
        assert "a" in pns or "b" in pns

    def test_peek_case_sensitive(self, synthetic_concat_alias_v):
        """Test peek is case-sensitive for Verilog module names."""
        pns_correct = NetlistParser.peek_pins(synthetic_concat_alias_v, "concat_alias")
        pns_wrong = NetlistParser.peek_pins(synthetic_concat_alias_v, "CONCAT_ALIAS")
        # Verilog module names are case-sensitive
        assert pns_correct is not None
        assert pns_wrong is None

    def test_peek_backtick_preprocessor_directives_filtered(self):
        """Peek must filter backtick preprocessor noise from the port list.

        Regression for a real-world case: a module wrapping ports in
        `ifdef ... `else ... `endif previously caused the peek to return
        'ifdef', the macro name, 'else', 'endif' as if they were pin
        names. They are not pins; they must be filtered out.
        """
        src = (
            "module wrapped_ports (\n"
            "    `ifdef SOME_MACRO\n"
            "        port_a,\n"
            "    `else\n"
            "        port_a,\n"
            "    `endif\n"
            "    port_b,\n"
            "    port_c\n"
            ");\n"
            "endmodule\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "wrapped.sv"
            fpath.write_text(src)
            pns = NetlistParser.peek_pins(str(fpath), "wrapped_ports")
        assert pns == ["port_a", "port_b", "port_c"], (
            f"Backtick directives leaked into peek result: {pns}"
        )


class TestVerilogUDP:
    """Tests for Verilog UDP (user-defined primitive) parsing."""

    def test_udp_basic_parse(self, synthetic_udp_simple_v):
        """Test parsing of basic Verilog UDP definitions."""
        parser = NetlistParser(synthetic_udp_simple_v)
        assert parser.format == "verilog"
        # Should find UDP definitions alongside modules
        assert "udp_xor" in parser.subckts, "Should parse udp_xor UDP definition"
        assert "udp_buf" in parser.subckts, "Should parse udp_buf UDP definition"
        assert "udp_user" in parser.subckts, "Should parse udp_user module"
        assert "top" in parser.subckts, "Should parse top module"

    def test_udp_port_extraction(self, synthetic_udp_simple_v):
        """Test that UDP port lists are correctly extracted."""
        parser = NetlistParser(synthetic_udp_simple_v)
        # udp_xor has ports: output y, input a, input b
        udp_xor = parser.subckts["udp_xor"]
        assert len(udp_xor.pins) == 3, (
            f"udp_xor should have 3 pins, got {len(udp_xor.pins)}"
        )
        assert set(udp_xor.pins) == {"y", "a", "b"}, (
            f"udp_xor pins should be {{y, a, b}}, got {set(udp_xor.pins)}"
        )

    def test_udp_no_instances(self, synthetic_udp_simple_v):
        """Test that UDP has no instances (truth table contents skipped)."""
        parser = NetlistParser(synthetic_udp_simple_v)
        # UDP should have no instances (truth table is irrelevant for tracing)
        insts_in_udp = parser.instances_by_parent.get("udp_xor", [])
        assert len(insts_in_udp) == 0, "UDP should have no instances"

    def test_udp_instance_in_module(self, synthetic_udp_simple_v):
        """Test that instances of UDPs are recognized in modules."""
        parser = NetlistParser(synthetic_udp_simple_v)
        # udp_user module instantiates udp_xor and udp_buf
        insts_in_user = parser.instances_by_parent.get("udp_user", [])
        assert len(insts_in_user) == 2, (
            f"udp_user should have 2 instances, got {len(insts_in_user)}"
        )
        # Verify instance cell types
        cell_types = {inst.cell_type for inst in insts_in_user}
        assert "udp_xor" in cell_types, "Should have udp_xor instance"
        assert "udp_buf" in cell_types, "Should have udp_buf instance"

    def test_udp_tracer_leaf(self, synthetic_udp_simple_v):
        """Test that UDP is treated as a leaf cell and trace terminates at UDP."""
        from netlist_tracer.tracer import BidirectionalTracer

        parser = NetlistParser(synthetic_udp_simple_v)
        # Verify that parser loaded the UDP and it's available for tracing
        assert "udp_xor" in parser.subckts, "UDP should be registered in parser for tracing"
        assert "top" in parser.subckts, "Top module should be registered"

        # Invoke the tracer on 'clk' pin from top
        tracer = BidirectionalTracer(parser)
        results = tracer.trace_pins("top", pins=["clk"])
        paths = results.get("clk", [])

        # Verify at least one path was found
        assert len(paths) >= 1, "Expected at least one path from top.clk"

        # Verify the final step terminates at UDP (leaf cell)
        last_step = paths[0][-1]
        assert last_step.cell == "udp_xor", (
            f"Expected path to terminate at udp_xor (leaf), got {last_step.cell}"
        )

    def test_udp_multiple_definitions(self, synthetic_udp_simple_v):
        """Test file with multiple UDP definitions."""
        parser = NetlistParser(synthetic_udp_simple_v)
        # Count UDPs vs regular modules
        udp_count = sum(
            1 for name in parser.subckts.keys()
            if name in {"udp_xor", "udp_buf"}
        )
        assert udp_count == 2, (
            f"Should have 2 UDP definitions, got {udp_count}"
        )

    def test_udp_peek_pins(self, synthetic_udp_simple_v):
        """Test that peek_pins works on UDP definitions."""
        pins = NetlistParser.peek_pins(synthetic_udp_simple_v, "udp_xor")
        assert pins is not None, "peek_pins should find udp_xor"
        assert set(pins) == {"y", "a", "b"}, (
            f"udp_xor pins should be {{y, a, b}}, got {set(pins)}"
        )
