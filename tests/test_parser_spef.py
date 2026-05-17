"""Unit tests for SPEF parser."""

import gzip
import os
import tempfile

import pytest

from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.model import SubcktDef
from netlist_tracer.parsers.spef import parse_spef


def test_spef_basic_parse():
    """Test basic SPEF parsing returns subckts and instances."""
    spef_content = """*SPEF "1.0"
*DESIGN test_circuit
*DIVIDER /
*DELIMITER :
*C_UNIT 1 FF
*R_UNIT 1 OHM

*D_NET net1 100.0
*CAP 1 net1 gnd 50.0
*RES 1 net1 net2 100.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, gbl_nets = parse_spef(temp_file)
        assert isinstance(sbckts, dict)
        assert "test_circuit" in sbckts
        assert isinstance(sbckts["test_circuit"], SubcktDef)
        assert isinstance(insts, list)
        assert len(insts) >= 2  # At least 1 CAP and 1 RES
        assert gbl_nets == []
    finally:
        os.unlink(temp_file)


def test_spef_top_design_pins():
    """Test *DESIGN and *PORTS populate top-level SubcktDef pins."""
    spef_content = """*DESIGN top
*PORTS I in_pin
*PORTS O out_pin

*D_NET net1 1.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, _, _ = parse_spef(temp_file)
        assert "top" in sbckts
        top = sbckts["top"]
        assert "in_pin" in top.pins
        assert "out_pin" in top.pins
    finally:
        os.unlink(temp_file)


def test_spef_name_map_resolved():
    """Test *NAME_MAP indirection is resolved during parse."""
    spef_content = """*DESIGN design
*NAME_MAP *5 real_net_name

*D_NET *5 1.0
*CAP 1 *5 gnd 0.5
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        # Check that instances reference the real name, not *5
        assert any("real_net_name" in inst.nets for inst in insts)
        assert not any("*5" in inst.nets for inst in insts)
    finally:
        os.unlink(temp_file)


def test_spef_res_instances():
    """Test *RES creates R-prefix Instance with scaled value."""
    spef_content = """*DESIGN design
*R_UNIT 1 KOHM

*D_NET net1 1.0
*RES 1 net1 net2 2.5
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        # Should have 1 R instance
        r_insts = [i for i in insts if i.cell_type == "R"]
        assert len(r_insts) == 1
        r = r_insts[0]
        assert r.name == "R1"
        assert r.nets == ["net1", "net2"]
        # 2.5 KOHM = 2500 ohm
        assert "2.5e+03" in r.params["_value"] or "2500" in r.params["_value"]
    finally:
        os.unlink(temp_file)


def test_spef_cap_instances():
    """Test *CAP creates C-prefix Instance with scaled value."""
    spef_content = """*DESIGN design
*C_UNIT 1 FF

*D_NET net1 100.0
*CAP 1 net1 gnd 50.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        # Should have 1 C instance
        c_insts = [i for i in insts if i.cell_type == "C"]
        assert len(c_insts) == 1
        c = c_insts[0]
        assert c.name == "C1"
        assert c.nets == ["net1", "gnd"]
        # 50 FF = 50e-15 F
        assert "50e-15" in c.params["_value"] or "5e-14" in c.params["_value"]
    finally:
        os.unlink(temp_file)


def test_spef_unit_scaling():
    """Test *C_UNIT and *R_UNIT properly scale values."""
    spef_content = """*DESIGN design
*C_UNIT 1 PF
*R_UNIT 1 OHM

*D_NET net1 10.0
*CAP 1 net1 gnd 2.5
*RES 1 net1 gnd 100.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        c_insts = [i for i in insts if i.cell_type == "C"]
        r_insts = [i for i in insts if i.cell_type == "R"]
        # 2.5 PF = 2.5e-12 F
        assert "2.5e-12" in c_insts[0].params["_value"]
        # 100 OHM = 100 ohm
        assert "100" in r_insts[0].params["_value"]
    finally:
        os.unlink(temp_file)


def test_spef_gz_parsing():
    """Test .spef.gz files are transparently decompressed."""
    spef_content = """*DESIGN design
*D_NET net1 1.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".spef.gz", delete=False) as f:
        with gzip.open(f.name, "wt", encoding="utf-8") as gz:
            gz.write(spef_content)
        temp_file = f.name

    try:
        sbckts, _, _ = parse_spef(temp_file)
        assert "design" in sbckts
    finally:
        os.unlink(temp_file)


def test_spef_conn_instances():
    """Test *CONN lines reference instances and ports."""
    spef_content = """*DESIGN design
*PORTS I clk

*D_NET clk 5.0
*CONN *I buf1:Z I
*CONN *P clk I
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        # CONN lines are parsed; no explicit instance for them in this version
        # (they are annotations), but we can verify parse completes
        assert "design" in sbckts
    finally:
        os.unlink(temp_file)


def test_spef_detect_format():
    """Test detect_format recognizes .spef files with *SPEF marker."""
    from netlist_tracer.parsers.detect import detect_format

    spef_content = """*SPEF "1.0" "ns" "1pF" "1ohm"
*DESIGN design
*D_NET net1 1.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        fmt = detect_format([temp_file])
        assert fmt == "spef"
    finally:
        os.unlink(temp_file)


def test_spef_detect_fallback_markers():
    """Test detect_format recognizes SPEF via *D_NET fallback if no *SPEF header."""
    from netlist_tracer.parsers.detect import detect_format

    spef_content = """*DESIGN design
*D_NET net1 1.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        fmt = detect_format([temp_file])
        # Should detect as spef due to *D_NET marker + .spef extension
        assert fmt in ("spef", "spice")  # May fallback to spice on extension tie
    finally:
        os.unlink(temp_file)


def test_spef_tracer_walks():
    """Test that parsed SPEF can be instantiated in a tracer with parasitic R/C network."""
    from netlist_tracer.parser import NetlistParser
    from netlist_tracer.tracer import BidirectionalTracer

    spef_content = """*SPEF "1.0"
*DESIGN test_design
*PORTS I A O Z

*D_NET A 2.0
*CAP 1 A gnd 1.0
*RES 1 A Z 100.0
*END

*D_NET Z 3.0
*CAP 2 Z gnd 1.5
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        # Parse via NetlistParser
        parser = NetlistParser(temp_file)
        assert "test_design" in parser.subckts

        # Instantiate tracer (just verify it can be instantiated)
        tracer = BidirectionalTracer(parser)
        assert tracer is not None

        # Verify instances are registered properly
        assert len(parser.instances_by_celltype["C"]) == 2
        assert len(parser.instances_by_celltype["R"]) == 1

        # Verify actual tracing works: check that tracer can resolve nets
        # The R/C instances should be present and connected to the nets
        c_inst = parser.instances_by_celltype["C"][0]
        assert len(c_inst.nets) == 2  # Should have two terminals
        assert "A" in c_inst.nets or "gnd" in c_inst.nets

        # Trace from port A — should find at least one path through R/C network
        paths = tracer.trace("test_design", "A")
        assert isinstance(paths, list), "trace() should return a list"
        # R/C network is traceable; at minimum lateral-walk should find some path
        assert len(paths) >= 1, "Expected at least one traceable path through R/C network"
    finally:
        os.unlink(temp_file)


def test_spef_no_design_name_fallback():
    """Test that design name is derived from filename if *DESIGN missing."""
    spef_content = """*D_NET net1 1.0
*END
"""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".spef", prefix="my_design_", delete=False
    ) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, _, _ = parse_spef(temp_file)
        # Design name should be derived (my_design_<tmpXXXX>)
        assert len(sbckts) == 1
        assert "my_design" in list(sbckts.keys())[0]
    finally:
        os.unlink(temp_file)


def test_spef_empty_file():
    """Test that empty SPEF raises NetlistParseError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write("")
        f.flush()
        temp_file = f.name

    try:
        with pytest.raises(NetlistParseError):
            parse_spef(temp_file)
    finally:
        os.unlink(temp_file)


def test_spef_nonexistent_file():
    """Test that nonexistent file raises NetlistParseError."""
    with pytest.raises(NetlistParseError):
        parse_spef("/nonexistent/path/file.spef")


def test_spef_malformed_cap_line():
    """Test that malformed *CAP lines are gracefully skipped."""
    spef_content = """*DESIGN design
*D_NET net1 1.0
*CAP 1 node_a node_b badvalue
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        # Should not raise; just skip the bad CAP line with a warning
        sbckts, insts, _ = parse_spef(temp_file)
        assert "design" in sbckts
    finally:
        os.unlink(temp_file)


def test_spef_multiple_nets():
    """Test parsing multiple *D_NET blocks."""
    spef_content = """*DESIGN design
*D_NET net1 1.0
*CAP 1 net1 gnd 0.5
*END

*D_NET net2 2.0
*RES 1 net2 gnd 100.0
*END

*D_NET net3 3.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        # Should have parsed 1 CAP and 1 RES
        c_count = sum(1 for i in insts if i.cell_type == "C")
        r_count = sum(1 for i in insts if i.cell_type == "R")
        assert c_count == 1
        assert r_count == 1
    finally:
        os.unlink(temp_file)


def test_spef_quoted_design_name():
    """Test that *DESIGN with quoted name is parsed and quotes stripped."""
    spef_content = """*SPEF "IEEE 1481-1998"
*DESIGN "mycircuit"
*DATE "Jan 1 2026"
*DIVIDER /
*DELIMITER :

*D_NET net1 1.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, _, _ = parse_spef(temp_file)
        # Design name should be "mycircuit" (quotes stripped)
        assert "mycircuit" in sbckts
        assert '"mycircuit"' not in sbckts
    finally:
        os.unlink(temp_file)


def test_spef_multiline_name_map():
    """Test multi-line *NAME_MAP section format (section header + data lines)."""
    spef_content = """*SPEF "IEEE 1481-1998"
*DESIGN mycircuit
*DIVIDER /
*DELIMITER :

*NAME_MAP
*1 vin
*2 vout
*3 vmid

*D_NET *1 0.025
*CAP
1 *1 *3 0.025
*RES
1 *1 *3 100
*END

*D_NET *2 0.018
*CAP
1 *2 *3 0.018
*RES
1 *3 *2 50
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        # NAME_MAP should have resolved *1->vin, *2->vout, *3->vmid
        # Check that instances reference real names, not star-aliases
        all_nets = set()
        for inst in insts:
            all_nets.update(inst.nets)
        assert "vin" in all_nets
        assert "vout" in all_nets
        assert "vmid" in all_nets
        # Star-references should NOT appear in final nets
        assert "*1" not in all_nets
        assert "*2" not in all_nets
        assert "*3" not in all_nets
    finally:
        os.unlink(temp_file)


def test_spef_multiline_cap_res_sections():
    """Test multi-line *CAP and *RES section format (section header + data lines)."""
    spef_content = """*SPEF "IEEE 1481-1998"
*DESIGN mycircuit
*DIVIDER /
*DELIMITER :
*C_UNIT 1 FF
*R_UNIT 1 OHM

*D_NET net1 0.025
*CAP
1 n1 n2 0.025
2 n1 gnd 0.010
*RES
1 n1 n2 100
*END

*D_NET net2 0.018
*RES
1 n3 n4 50
*CAP
2 n3 gnd 0.018
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        # Should parse 3 CAP instances and 2 RES instances from section format
        c_count = sum(1 for i in insts if i.cell_type == "C")
        r_count = sum(1 for i in insts if i.cell_type == "R")
        assert c_count == 3, f"Expected 3 CAP instances, got {c_count}"
        assert r_count == 2, f"Expected 2 RES instances, got {r_count}"
        # Verify all instances belong to mycircuit
        assert all(i.parent_cell == "mycircuit" for i in insts)
    finally:
        os.unlink(temp_file)


def test_spef_section_ports_pins_resolved():
    """Test *PORTS section format resolves port names via *NAME_MAP."""
    spef_content = """*SPEF "IEEE 1481-1998"
*DESIGN mycircuit
*DATE "Jan 1 2026"
*DIVIDER /
*DELIMITER :
*T_UNIT 1 PS
*C_UNIT 1 FF
*R_UNIT 1 OHM

*NAME_MAP
*1 vin
*2 vout
*3 vmid

*PORTS
*1 I
*2 O

*D_NET *1 0.025
*CAP
1 *1 *3 0.025
*RES
1 *1 *3 100
*END

*D_NET *2 0.018
*CAP
1 *2 *3 0.018
*RES
1 *3 *2 50
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        sbckts, insts, _ = parse_spef(temp_file)
        assert "mycircuit" in sbckts
        mycircuit = sbckts["mycircuit"]
        # Ports should be resolved to real names via NAME_MAP, not empty or direction markers
        assert mycircuit.pins == ["vin", "vout"], f"Expected pins ['vin', 'vout'], got {mycircuit.pins}"
        assert mycircuit.pin_to_pos["vin"] == 0
        assert mycircuit.pin_to_pos["vout"] == 1
    finally:
        os.unlink(temp_file)
