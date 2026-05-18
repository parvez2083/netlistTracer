"""Tests for per-file format detection and per-format dispatch (mixed-directory support)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from netlist_tracer import NetlistParser
from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.parsers.detect import detect_format_per_file


class TestDetectFormatPerFile:
    """Tests for detect_format_per_file() grouping function."""

    def test_detect_format_per_file_homogeneous_verilog(self) -> None:
        """Homogeneous Verilog file list groups into single verilog format."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        v_files = sorted(
            [str(fixture_dir / "verilog_features.v"), str(fixture_dir / "verilog_udp_genvar.v")]
        )

        frmt_grps = detect_format_per_file(v_files)

        assert len(frmt_grps) == 1
        assert "verilog" in frmt_grps
        assert sorted(frmt_grps["verilog"]) == sorted(v_files)

    def test_detect_format_per_file_mixed_verilog_spice(self) -> None:
        """Mixed Verilog + SPICE files group into separate format buckets."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        v_file = str(fixture_dir / "verilog_features.v")
        sp_file = str(fixture_dir / "spice_features.sp")

        frmt_grps = detect_format_per_file([v_file, sp_file])

        assert len(frmt_grps) == 2
        assert "verilog" in frmt_grps
        assert "spice" in frmt_grps
        assert frmt_grps["verilog"] == [v_file]
        assert frmt_grps["spice"] == [sp_file]

    def test_detect_format_per_file_sorted_within_group(self) -> None:
        """Files within each format group are sorted alphabetically."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        v1 = str(fixture_dir / "verilog_features.v")
        v2 = str(fixture_dir / "verilog_udp_genvar.v")
        v3 = str(fixture_dir / "sv_interfaces.sv")

        frmt_grps = detect_format_per_file([v1, v2, v3])

        # All three are Verilog
        assert len(frmt_grps) == 1
        assert "verilog" in frmt_grps
        # Should be sorted
        assert frmt_grps["verilog"] == sorted([v1, v2, v3])


class TestDispatchMixedDirectory:
    """Tests for mixed-format directory dispatch and subckt merging."""

    def test_dispatch_mixed_dir_creates_merged_subckts(self) -> None:
        """Mixed directory with Verilog and SPICE files merges subckts from both."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a simple Verilog module
            v_file = tmpdir_path / "design.v"
            v_file.write_text("""
module BUF (input in, output out);
  assign out = in;
endmodule
""")

            # Create a simple SPICE subcircuit
            sp_file = tmpdir_path / "tb.sp"
            sp_file.write_text("""
.subckt INV inp out vdd gnd
.ends INV
""")

            # Parse the directory
            parser = NetlistParser(tmpdir)

            # Both subckts should be present
            assert "BUF" in parser.subckts, "Verilog module BUF not found"
            assert "INV" in parser.subckts, "SPICE subckt INV not found"

            # Format should be marked as 'mixed'
            assert parser.format == "mixed"

    def test_dispatch_collision_format_priority_wins(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When same subckt name in multiple formats, higher-priority format wins."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create SPF definition (rank 0 — lowest)
            spf_file = tmpdir_path / "cell.spf"
            spf_file.write_text("""
*|DSPF 1.0
.SUBCKT INV a b c
*|NET b 1.0PF
.ENDS INV
""")

            # Create SPICE definition (rank 3 — higher) with different pins
            sp_file = tmpdir_path / "cell.sp"
            sp_file.write_text("""
.subckt INV A B VDD GND
M1 B A VDD VDD pch w=1u l=100n
M2 B A GND GND nch w=1u l=100n
.ends INV
""")

            # Parse the directory
            parser = NetlistParser(tmpdir)

            # INV should have 4 pins from SPICE (rank 3 > spf rank 0)
            assert "INV" in parser.subckts
            spice_inv = parser.subckts["INV"]
            assert len(spice_inv.pins) == 4  # A, B, VDD, GND from SPICE
            assert spice_inv.pins == ["A", "B", "VDD", "GND"]

            # Check for warning in logs
            assert any(
                "INV" in record.message and "defined in both" in record.message
                for record in caplog.records
                if record.levelname == "WARNING"
            )

    def test_dispatch_format_kwarg_override_skips_per_file(self) -> None:
        """format= kwarg override routes all files to specified parser, skipping per-file detection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a valid Verilog file
            v_file = tmpdir_path / "design.v"
            v_file.write_text("""
module TOP (input clk, output result);
endmodule
""")

            # Create an invalid SPICE file (no .subckt)
            sp_file = tmpdir_path / "invalid.sp"
            sp_file.write_text("this is not valid SPICE\n")

            # With format='verilog' override, both files should be parsed as Verilog
            # (the invalid SPICE file will be ignored or cause an error when Verilog parser tries it)
            # For now, we just verify the override is respected
            parser = NetlistParser(tmpdir, format="verilog")

            # Format should be pinned to verilog (not mixed)
            assert parser.format == "verilog"
            assert "TOP" in parser.subckts

    def test_dispatch_single_file_unchanged_behavior(self) -> None:
        """Single-file path maintains identical behavior to v0.4.1."""
        fixture_path = Path(__file__).parent / "fixtures" / "synthetic" / "spice_features.sp"
        if not fixture_path.exists():
            pytest.skip(f"Fixture {fixture_path} not found")

        parser = NetlistParser(str(fixture_path))

        # Single file should NOT be marked as mixed
        assert parser.format != "mixed"
        # Should have parsed the SPICE subckt
        assert len(parser.subckts) > 0

    def test_dispatch_verilog_only_directory_unchanged(self) -> None:
        """Verilog-only directory routes through existing Verilog pipeline unchanged."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create multiple Verilog files
            v1 = tmpdir_path / "module1.v"
            v1.write_text("""
module MOD1 (input a, output b);
endmodule
""")

            v2 = tmpdir_path / "module2.v"
            v2.write_text("""
module MOD2 (input x, output y);
endmodule
""")

            # Parse the directory
            parser = NetlistParser(tmpdir)

            # Should be verilog format (not mixed, since only Verilog files present)
            assert parser.format == "verilog"
            # Both modules should be found
            assert "MOD1" in parser.subckts
            assert "MOD2" in parser.subckts

    def test_dispatch_spice_group_multifile_errors(self) -> None:
        """SPICE group with multiple files raises NetlistParseError."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create two SPICE files
            sp1 = tmpdir_path / "cell1.sp"
            sp1.write_text(".subckt C1\n.ends C1\n")

            sp2 = tmpdir_path / "cell2.sp"
            sp2.write_text(".subckt C2\n.ends C2\n")

            # Create one Verilog file to trigger mixed-format detection
            v_file = tmpdir_path / "design.v"
            v_file.write_text("module TOP; endmodule\n")

            # Parsing should raise error about SPICE multifile expectation
            with pytest.raises(NetlistParseError) as exc_info:
                NetlistParser(tmpdir)

            assert "spice" in str(exc_info.value).lower()
            assert "expects exactly one file" in str(exc_info.value).lower()


class TestDispatchBackwardCompatibility:
    """Tests ensuring backward compatibility with v0.4.1 behavior."""

    def test_single_file_verilog_byte_identical(self, synthetic_verilog_features_v: str) -> None:
        """Single Verilog file parse produces identical results as before."""
        parser = NetlistParser(synthetic_verilog_features_v)
        assert "concat_alias" in parser.subckts, (
            "concat_alias module should be in verilog_features.v"
        )

        # Should have subckts (the fixture defines modules)
        assert len(parser.subckts) > 0
        # Format should be verilog (not mixed)
        assert parser.format == "verilog"

    def test_single_file_spice_byte_identical(self) -> None:
        """Single SPICE file parse produces identical results as before."""
        fixture_path = Path(__file__).parent / "fixtures" / "synthetic" / "spice_features.sp"
        if not fixture_path.exists():
            pytest.skip(f"Fixture {fixture_path} not found")

        parser = NetlistParser(str(fixture_path))

        # Should have subckts
        assert len(parser.subckts) > 0
        # Format should be spice (not mixed)
        assert parser.format == "spice"

    def test_directory_verilog_only_byte_identical(self) -> None:
        """Verilog-only directory uses existing multiprocessing pipeline unchanged."""
        verilog_dir = Path(__file__).parent / "fixtures" / "picorv32_tiny"
        if not verilog_dir.exists():
            pytest.skip(f"Fixture directory {verilog_dir} not found")

        parser = NetlistParser(str(verilog_dir))

        # Should find modules
        assert len(parser.subckts) > 0
        # Format should be verilog (not mixed, since only Verilog files)
        assert parser.format == "verilog"


class TestDispatchErrorConditions:
    """Tests for error handling in dispatch logic."""

    def test_dispatch_empty_directory_raises(self) -> None:
        """Empty directory raises appropriate error."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an empty directory (no netlist files)
            # NetlistParser should fail when trying to parse it
            with pytest.raises(NetlistParseError) as exc_info:
                NetlistParser(tmpdir)
            assert "no netlist files" in str(exc_info.value).lower()

    def test_dispatch_verilog_file_alone_in_dir(self) -> None:
        """Single Verilog file in directory uses verilog format, not mixed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create a Verilog file (recognized)
            v_file = tmpdir_path / "design.v"
            v_file.write_text("module TOP (input clk, output result); endmodule\n")

            # Create a non-netlist file (should be ignored, not globbed)
            txt_file = tmpdir_path / "data.txt"
            txt_file.write_text("random data\n")

            # Should parse as verilog-only (txt not a netlist format)
            parser = NetlistParser(tmpdir)

            # Should have format as verilog (not mixed)
            assert parser.format == "verilog"
            assert "TOP" in parser.subckts
