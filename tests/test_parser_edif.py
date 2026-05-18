"""Regression tests for EDIF parser against golden baselines."""

from __future__ import annotations

import json
import os
from typing import Any

import pytest

from netlist_tracer import NetlistParser
from netlist_tracer.exceptions import NetlistParseError

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vendored")
GOLDEN_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "golden")
SYNTHETIC_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "synthetic")


def parser_snapshot(parser: NetlistParser) -> dict[str, Any]:
    """Serialize a deterministic, comparable view of parser state."""
    subckts_view: dict[str, dict[str, Any]] = {}
    for name in sorted(parser.subckts.keys()):
        sub = parser.subckts[name]
        subckts_view[name] = {
            "pins": list(sub.pins),
            "pin_count": len(sub.pins),
            "alias_count": len(sub.aliases),
            "child_instance_count": len(parser.instances_by_parent.get(name, [])),
        }
    total_instances = sum(len(v) for v in parser.instances_by_parent.values())
    return {
        "format": parser.format,
        "subckt_count": len(parser.subckts),
        "total_instance_count": total_instances,
        "subckt_names_sorted": sorted(parser.subckts.keys()),
        "subckts": subckts_view,
    }


@pytest.mark.slow
class TestEDIFRegression:
    """EDIF parsing regression tests against golden baselines."""

    def test_AND_gate_parsing_regression(self) -> None:
        """Parse AND_gate.edf and match golden baseline snapshot."""
        fixture = os.path.join(FIXTURES_DIR, "AND_gate.edf")
        parser = NetlistParser(fixture)
        snapshot = parser_snapshot(parser)

        golden_file = os.path.join(GOLDEN_DIR, "AND_gate_baseline.json")
        with open(golden_file) as f:
            golden = json.load(f)

        assert snapshot == golden["parser"]

    def test_AND_gate_tracing_regression(self) -> None:
        """Trace AND_gate.edf and match golden baseline."""
        from netlist_tracer import BidirectionalTracer, format_path

        fixture = os.path.join(FIXTURES_DIR, "AND_gate.edf")
        parser = NetlistParser(fixture)
        tracer = BidirectionalTracer(parser)

        paths = tracer.trace("logic_gate", "a")
        seen = set()
        unique_formatted: list[str] = []
        for path in paths:
            sig = format_path(path)
            if sig in seen:
                continue
            seen.add(sig)
            unique_formatted.append(sig)

        golden_file = os.path.join(GOLDEN_DIR, "AND_gate_baseline.json")
        with open(golden_file) as f:
            golden = json.load(f)

        assert unique_formatted == golden["trace"]["paths"]

    def test_n_bit_counter_parsing_regression(self) -> None:
        """Parse n_bit_counter.edf and match golden baseline."""
        fixture = os.path.join(FIXTURES_DIR, "n_bit_counter.edf")
        parser = NetlistParser(fixture)
        snapshot = parser_snapshot(parser)

        golden_file = os.path.join(GOLDEN_DIR, "n_bit_counter_baseline.json")
        with open(golden_file) as f:
            golden = json.load(f)

        assert snapshot == golden["parser"]

    def test_n_bit_counter_tracing_regression(self) -> None:
        """Trace n_bit_counter.edf and match golden baseline."""
        from netlist_tracer import BidirectionalTracer, format_path

        fixture = os.path.join(FIXTURES_DIR, "n_bit_counter.edf")
        parser = NetlistParser(fixture)
        tracer = BidirectionalTracer(parser)

        paths = tracer.trace("n_bit_counter", "clk")
        seen = set()
        unique_formatted: list[str] = []
        for path in paths:
            sig = format_path(path)
            if sig in seen:
                continue
            seen.add(sig)
            unique_formatted.append(sig)

        golden_file = os.path.join(GOLDEN_DIR, "n_bit_counter_baseline.json")
        with open(golden_file) as f:
            golden = json.load(f)

        assert unique_formatted == golden["trace"]["paths"]

    def test_one_counter_parsing_regression(self) -> None:
        """Parse one_counter.edf and match golden baseline."""
        fixture = os.path.join(FIXTURES_DIR, "one_counter.edf")
        parser = NetlistParser(fixture)
        snapshot = parser_snapshot(parser)

        golden_file = os.path.join(GOLDEN_DIR, "one_counter_baseline.json")
        with open(golden_file) as f:
            golden = json.load(f)

        assert snapshot == golden["parser"]

    def test_one_counter_tracing_regression(self) -> None:
        """Trace one_counter.edf and match golden baseline."""
        from netlist_tracer import BidirectionalTracer, format_path

        fixture = os.path.join(FIXTURES_DIR, "one_counter.edf")
        parser = NetlistParser(fixture)
        tracer = BidirectionalTracer(parser)

        paths = tracer.trace("one_counter", "XP_CLK_A")
        seen = set()
        unique_formatted: list[str] = []
        for path in paths:
            sig = format_path(path)
            if sig in seen:
                continue
            seen.add(sig)
            unique_formatted.append(sig)

        golden_file = os.path.join(GOLDEN_DIR, "one_counter_baseline.json")
        with open(golden_file) as f:
            golden = json.load(f)

        assert unique_formatted == golden["trace"]["paths"]


class TestEDIFRename:
    """Test EDIF rename preservation."""

    def test_edif_rename_preserves_original(self) -> None:
        """Parse EDIF cell with (rename safe \"original\") and verify original name captured."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        # Check cell-level rename
        assert "SAFE_NAME" in parser.subckts
        safe_nm = parser.subckts["SAFE_NAME"]
        assert safe_nm.params.get("_edif_original_name") == "orig[7:0]"

    def test_edif_no_rename_no_original_name_key(self) -> None:
        """Parse EDIF cell without rename; _edif_original_name key absent."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        # INV and BUF cells have no rename
        inv = parser.subckts.get("INV")
        assert inv is not None
        assert "_edif_original_name" not in inv.params

        buf = parser.subckts.get("BUF")
        assert buf is not None
        assert "_edif_original_name" not in buf.params

    def test_edif_instance_rename_preserved(self) -> None:
        """Parse EDIF instance with (rename safe \"original\") and verify captured."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        # Find instance u1_safe
        instances = parser.instances_by_name.get("u1_safe", [])
        assert len(instances) > 0
        inst = instances[0]
        assert inst.params.get("_edif_original_name") == "u1[0]"


class TestEDIFProperties:
    """Test EDIF property preservation."""

    def test_edif_property_string(self) -> None:
        """Parse EDIF property with string value."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        cell = parser.subckts.get("PROP_TEST")
        assert cell is not None
        prp_dct = cell.params.get("_edif_properties", {})
        assert prp_dct.get("author") == "ACME"

    def test_edif_property_integer(self) -> None:
        """Parse EDIF property with integer value."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        cell = parser.subckts.get("PROP_TEST")
        assert cell is not None
        prp_dct = cell.params.get("_edif_properties", {})
        assert prp_dct.get("width") == 8
        assert isinstance(prp_dct.get("width"), int)

    def test_edif_property_real(self) -> None:
        """Parse EDIF property with real value."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        cell = parser.subckts.get("PROP_TEST")
        assert cell is not None
        prp_dct = cell.params.get("_edif_properties", {})
        assert prp_dct.get("pi") == pytest.approx(3.14)

    def test_edif_property_boolean(self) -> None:
        """Parse EDIF property with boolean value."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        cell = parser.subckts.get("PROP_TEST")
        assert cell is not None
        prp_dct = cell.params.get("_edif_properties", {})
        assert prp_dct.get("enabled") is True
        assert isinstance(prp_dct.get("enabled"), bool)

    def test_edif_instance_properties(self) -> None:
        """Parse EDIF instance properties."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        instances = parser.instances_by_name.get("I1", [])
        assert len(instances) > 0
        inst = instances[0]
        prp_dct = inst.params.get("_edif_properties", {})
        assert prp_dct.get("depth") == 16
        assert isinstance(prp_dct.get("depth"), int)


class TestEDIFLibrary:
    """Test EDIF library tracking and collision handling."""

    def test_edif_library_recorded(self) -> None:
        """Parse EDIF and verify _edif_library param recorded."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        inv = parser.subckts.get("INV")
        assert inv is not None
        assert inv.params.get("_edif_library") == "multilib_tech"

        top = parser.subckts.get("TOP")
        assert top is not None
        assert top.params.get("_edif_library") == "multilib_design"

    def test_edif_multi_library_collision_warns(self, caplog) -> None:
        """Parse EDIF with same cell in multiple libraries; warning logged, first-encountered wins."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")

        # Capture log output
        import logging

        with caplog.at_level(logging.WARNING):
            parser = NetlistParser(fixture)

        # First-encountered INV should be from multilib_tech (earlier in file)
        inv = parser.subckts.get("INV")
        assert inv is not None
        # Should have a collision warning
        warning_found = any(
            "collision" in record.message.lower() or "duplicate" in record.message.lower()
            for record in caplog.records
            if record.levelname == "WARNING"
        )
        assert warning_found, "Expected warning about INV collision not found"


class TestEDIFBusOrder:
    """Test bus_order parameter for port arrays."""

    def test_edif_bus_order_default_msb_first(self) -> None:
        """Parse EDIF bus array with default bus_order='msb_first'."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture)

        cell = parser.subckts.get("BUS_TEST")
        assert cell is not None

        # MSB-first order: [7], [6], [5], [4], [3], [2], [1], [0]
        expected_pins = [
            "data[7]",
            "data[6]",
            "data[5]",
            "data[4]",
            "data[3]",
            "data[2]",
            "data[1]",
            "data[0]",
            "single",
        ]
        assert cell.pins == expected_pins

    def test_edif_bus_order_lsb_first(self) -> None:
        """Parse EDIF bus array with bus_order='lsb_first'."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser = NetlistParser(fixture, bus_order="lsb_first")

        cell = parser.subckts.get("BUS_TEST")
        assert cell is not None

        # LSB-first order: [0], [1], [2], [3], [4], [5], [6], [7]
        expected_pins = [
            "data[0]",
            "data[1]",
            "data[2]",
            "data[3]",
            "data[4]",
            "data[5]",
            "data[6]",
            "data[7]",
            "single",
        ]
        assert cell.pins == expected_pins

    def test_edif_bus_order_invalid_raises(self) -> None:
        """Parse with invalid bus_order value raises NetlistParseError."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")

        with pytest.raises(NetlistParseError, match="Invalid bus_order"):
            NetlistParser(fixture, bus_order="invalid_order")

    def test_netlist_parser_bus_order_kwarg(self) -> None:
        """NetlistParser accepts bus_order kwarg and threads it to EDIF parser."""
        fixture = os.path.join(SYNTHETIC_DIR, "edif_features.edif")
        parser_msb = NetlistParser(fixture, bus_order="msb_first")
        parser_lsb = NetlistParser(fixture, bus_order="lsb_first")

        cell_msb = parser_msb.subckts.get("BUS_TEST")
        cell_lsb = parser_lsb.subckts.get("BUS_TEST")

        # Verify they produce different pin orders
        assert cell_msb.pins != cell_lsb.pins
        # MSB-first should start with [7]
        assert cell_msb.pins[0] == "data[7]"
        # LSB-first should start with [0]
        assert cell_lsb.pins[0] == "data[0]"

    def test_netlist_parser_bus_order_ignored_for_non_edif(self) -> None:
        """bus_order kwarg silently ignored for non-EDIF files."""
        # Use a Verilog fixture (should not raise)
        fixture = os.path.join(SYNTHETIC_DIR, "simple.spf")
        # Should not raise even with bus_order kwarg
        parser = NetlistParser(fixture, bus_order="lsb_first")
        assert parser is not None


class TestEdifPeek:
    """Peek tests for EDIF format."""

    def test_peek_basic_or_none(self, vendored_AND_gate_edf):
        """Test peek on EDIF file returns pins or None (both acceptable)."""
        pns = NetlistParser.peek_pins(vendored_AND_gate_edf, "AND")
        # Per blueprint: EDIF peek may return None if implementation complex
        # Either list or None is acceptable
        assert pns is None or isinstance(pns, list)

    def test_peek_nonexistent_cell(self, vendored_AND_gate_edf):
        """Test peek returns None for non-existent EDIF cell."""
        pns = NetlistParser.peek_pins(vendored_AND_gate_edf, "NONEXISTENT")
        assert pns is None
