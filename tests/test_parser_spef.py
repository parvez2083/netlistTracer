"""Unit tests for SPEF parser and overlay."""

import gzip
import os
import tempfile

import pytest

from netlist_tracer.parsers.spef import SpefData, SpefNet, SpefOverlay, parse_spef


def test_spef_data_creation():
    """Test that SpefData can be created."""
    data = SpefData()
    assert data is not None
    assert isinstance(data.nets, dict)
    assert data.design_name == ""


def test_spef_net_creation():
    """Test that SpefNet can be created."""
    net = SpefNet(name="test_net", total_cap=1e-15, total_res=10.0)
    assert net.name == "test_net"
    assert net.total_cap == 1e-15
    assert net.total_res == 10.0


def test_parse_spef_basic():
    """Test basic SPEF parsing."""
    spef_content = """*DESIGN test_circuit
*DIVIDER /
*DELIMITER :
*C_UNIT 1 FF
*R_UNIT 1 OHM

*D_NET net1 0.5
*CONN P inst1/pin1
*CAP 1 0.25
*RES 1 5.0
*END

*D_NET net2 1.0
*CONN P inst2/pin2
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        data = parse_spef(temp_file)
        assert data.design_name == "test_circuit"
        assert data.divider == "/"
        assert data.delimiter == ":"
        assert len(data.nets) == 2
        assert "net1" in data.nets
        assert "net2" in data.nets
        # Check scaling
        assert data.c_unit_scale == 1e-15  # FF
        assert data.r_unit_scale == 1.0  # OHM
    finally:
        os.unlink(temp_file)


def test_spef_unit_scaling_ff():
    """Test SPEF capacitance scaling from FF."""
    spef_content = """*C_UNIT 1 FF
*D_NET net1 100.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        data = parse_spef(temp_file)
        assert data.c_unit_scale == 1e-15
        assert "net1" in data.nets
        # 100 FF -> 100e-15 F
        assert data.nets["net1"].total_cap == 100e-15
    finally:
        os.unlink(temp_file)


def test_spef_unit_scaling_pf():
    """Test SPEF capacitance scaling from PF."""
    spef_content = """*C_UNIT 1 PF
*D_NET net1 2.5
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        data = parse_spef(temp_file)
        assert data.c_unit_scale == 1e-12
        # 2.5 PF -> 2.5e-12 F
        assert data.nets["net1"].total_cap == 2.5e-12
    finally:
        os.unlink(temp_file)


def test_spef_gz_parsing():
    """Test that gzipped SPEF files are parsed."""
    spef_content = """*DESIGN test_circuit
*D_NET net1 1.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".spef.gz", delete=False) as f:
        with gzip.open(f.name, "wt", encoding="utf-8") as gz:
            gz.write(spef_content)
        temp_file = f.name

    try:
        data = parse_spef(temp_file)
        assert data.design_name == "test_circuit"
        assert "net1" in data.nets
    finally:
        os.unlink(temp_file)


def test_spef_name_map():
    """Test SPEF *NAME_MAP indirection resolution."""
    spef_content = """*NAME_MAP *5 real_net_name
*D_NET *5 1.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        data = parse_spef(temp_file)
        # Real name should be resolved
        assert "real_net_name" in data.nets
        assert "*5" not in data.nets
    finally:
        os.unlink(temp_file)


def test_spef_overlay_lookup():
    """Test SpefOverlay lookup functionality."""
    data = SpefData()
    net = SpefNet(name="test_net", total_cap=1e-15, total_res=10.0)
    data.nets["test_net"] = net

    overlay = SpefOverlay(data)
    result = overlay.lookup("test_net")

    assert result is not None
    assert result["C"] == 1e-15
    assert result["R"] == 10.0


def test_spef_overlay_bracket_normalization():
    """Test SpefOverlay handles bracket variants."""
    data = SpefData()
    net = SpefNet(name="net[0]", total_cap=1e-15)
    data.nets["net[0]"] = net

    overlay = SpefOverlay(data)
    # Should find via bracket variant
    result = overlay.lookup("net<0>")

    assert result is not None
    assert result["C"] == 1e-15


def test_spef_overlay_suffix_stripping():
    """Test SpefOverlay strips SPF ':pin' suffix."""
    data = SpefData()
    net = SpefNet(name="inst/M1", total_cap=1e-15)
    data.nets["inst/M1"] = net

    overlay = SpefOverlay(data)
    # Should find without suffix after stripping
    result = overlay.lookup("inst/M1:G")

    assert result is not None
    assert result["C"] == 1e-15


def test_spef_overlay_miss():
    """Test SpefOverlay returns None for missing nets."""
    data = SpefData()
    overlay = SpefOverlay(data)
    result = overlay.lookup("nonexistent_net")

    assert result is None


def test_spef_resistance_aggregation():
    """Test that multiple *RES lines aggregate resistance."""
    spef_content = """*R_UNIT 1 OHM
*D_NET net1 1.0
*RES 1 5.0
*RES 2 3.0
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        data = parse_spef(temp_file)
        net = data.nets["net1"]
        # Should sum the resistance values
        assert net.total_res == 8.0  # 5.0 + 3.0
    finally:
        os.unlink(temp_file)


def test_spef_ports_parsing():
    """Test that SPEF *PORTS are parsed."""
    spef_content = """*PORTS I port1 port2 port3
*PORTS O port4
*END
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        data = parse_spef(temp_file)
        # Ports should be collected
        assert len(data.ports) >= 2  # At least port1 and port2
    finally:
        os.unlink(temp_file)


def test_spef_malformed_missing_end():
    """Test that SPEF without *END raises NetlistParseError."""
    spef_content = """*DESIGN test
*D_NET net1 1.0
"""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write(spef_content)
        f.flush()
        temp_file = f.name

    try:
        from netlist_tracer.exceptions import NetlistParseError
        with pytest.raises(NetlistParseError):
            parse_spef(temp_file)
    finally:
        os.unlink(temp_file)


def test_spef_empty_file():
    """Test that empty SPEF raises NetlistParseError."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".spef", delete=False) as f:
        f.write("")
        f.flush()
        temp_file = f.name

    try:
        from netlist_tracer.exceptions import NetlistParseError
        with pytest.raises(NetlistParseError):
            parse_spef(temp_file)
    finally:
        os.unlink(temp_file)


def test_spef_nonexistent_file():
    """Test that nonexistent file raises NetlistParseError."""
    from netlist_tracer.exceptions import NetlistParseError
    with pytest.raises(NetlistParseError):
        parse_spef("/nonexistent/path/file.spef")
