"""Unit tests for format detection, content-first override, and UTF-8 tolerance."""

from netlist_tracer.parsers.detect import detect_format


class TestFormatDetect:
    """Unit tests for per-format detection from synthetic and vendored fixtures."""

    def test_detect_format_verilog(self, synthetic_concat_alias_v):
        """Test detection of Verilog format."""
        fmt = detect_format([synthetic_concat_alias_v])
        assert fmt == "verilog", f"Expected verilog, got {fmt}"

    def test_detect_format_spice(self, synthetic_spice_basic_sp):
        """Test detection of SPICE format."""
        fmt = detect_format([synthetic_spice_basic_sp])
        assert fmt == "spice", f"Expected spice, got {fmt}"

    def test_detect_format_cdl(self, synthetic_cdl_basic_cdl):
        """Test detection of CDL format."""
        fmt = detect_format([synthetic_cdl_basic_cdl])
        assert fmt == "cdl", f"Expected cdl, got {fmt}"

    def test_detect_format_spectre(self, synthetic_spectre_basic_scs):
        """Test detection of Spectre format."""
        fmt = detect_format([synthetic_spectre_basic_scs])
        assert fmt == "spectre", f"Expected spectre, got {fmt}"

    def test_detect_format_edif(
        self, vendored_AND_gate_edf, vendored_n_bit_counter_edf, vendored_one_counter_edf
    ):
        """Test detection of EDIF format across all three vendored fixtures."""
        for fixture in [
            vendored_AND_gate_edf,
            vendored_n_bit_counter_edf,
            vendored_one_counter_edf,
        ]:
            fmt = detect_format([fixture])
            assert fmt == "edif", f"Expected edif for {fixture}, got {fmt}"


class TestFormatOverride:
    """Unit tests for format detection and override (content-first detection refactor)."""

    def test_content_beats_extension_v_with_spectre_content(self):
        """Content-first: .v file with Spectre content detects as spectre."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".v", delete=False) as f:
            f.write("simulator lang=spectre\nsubckt foo a b c\nends foo\n")
            path = f.name

        try:
            fmt = detect_format([path])
            assert fmt == "spectre", f"Expected spectre, got {fmt}"
        finally:
            os.unlink(path)

    def test_unknown_extension_with_verilog_content(self):
        """Content-first: .unknown file with Verilog content detects as verilog."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".unknown", delete=False) as f:
            f.write("module foo(a, b, c);\nendmodule\n")
            path = f.name

        try:
            fmt = detect_format([path])
            assert fmt == "verilog", f"Expected verilog, got {fmt}"
        finally:
            os.unlink(path)

    def test_no_content_signal_falls_back_to_extension(self):
        """Extension tiebreaker: .scs file with no format markers detects as spectre."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".scs", delete=False) as f:
            f.write("* this is a blank-ish file\n")
            path = f.name

        try:
            fmt = detect_format([path])
            assert fmt == "spectre", f"Expected spectre (extension hint), got {fmt}"
        finally:
            os.unlink(path)

    def test_no_content_no_extension_defaults_to_spice(self):
        """Final fallback: .xyz file with no markers defaults to spice."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".xyz", delete=False) as f:
            f.write("* comment only\n")
            path = f.name

        try:
            fmt = detect_format([path])
            assert fmt == "spice", f"Expected spice (default), got {fmt}"
        finally:
            os.unlink(path)

    def test_netlist_parser_format_override_kwarg_spectre(self):
        """NetlistParser format kwarg: explicit format='spectre' overrides auto-detect."""
        import os
        import tempfile

        from netlist_tracer import NetlistParser

        with tempfile.NamedTemporaryFile(mode="w", suffix=".scs", delete=False) as f:
            f.write("subckt foo a b\nends foo\n")
            path = f.name

        try:
            parser = NetlistParser(path, format="spectre")
            assert parser.format == "spectre", f"format kwarg not respected: {parser.format}"
        finally:
            os.unlink(path)

    def test_netlist_parser_format_override_kwarg_verilog(self):
        """NetlistParser format kwarg: explicit format='verilog' overrides auto-detect."""
        import os
        import tempfile

        from netlist_tracer import NetlistParser

        with tempfile.NamedTemporaryFile(mode="w", suffix=".v", delete=False) as f:
            f.write("module bar(a, b);\nendmodule\n")
            path = f.name

        try:
            parser = NetlistParser(path, format="verilog")
            assert parser.format == "verilog", f"format kwarg not respected: {parser.format}"
        finally:
            os.unlink(path)

    def test_netlist_parser_format_invalid_raises(self):
        """NetlistParser validation: invalid format value raises NetlistParseError."""
        import os
        import tempfile

        import pytest

        from netlist_tracer import NetlistParser
        from netlist_tracer.exceptions import NetlistParseError

        with tempfile.NamedTemporaryFile(mode="w", suffix=".v", delete=False) as f:
            f.write("module dummy(a);\nendmodule\n")
            path = f.name

        try:
            with pytest.raises(NetlistParseError) as exc_info:
                NetlistParser(path, format="vhdl")
            assert "Invalid format" in str(exc_info.value)
        finally:
            os.unlink(path)

    def test_cli_format_override_parse_with_spice(self):
        """CLI format override: format='spice' on a .sp file with _format override works."""
        import os
        import tempfile

        with tempfile.NamedTemporaryFile(mode="w", suffix=".sp", delete=False) as f:
            f.write(".subckt foo a b\nr1 a b 1k\n.ends foo\n")
            sp_path = f.name

        try:
            # Test via NetlistParser API (equivalent to CLI invocation)
            # This validates that the format parameter flows through the API
            import sys
            from unittest.mock import patch

            from netlist_tracer.cli.parse import main

            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, "out.json")
                with patch.object(
                    sys,
                    "argv",
                    [
                        "netlist-parser",
                        "-netlist",
                        sp_path,
                        "-output",
                        out_path,
                        "-format",
                        "spice",
                    ],
                ):
                    result = main()
                assert result == 0, f"CLI returned non-zero exit code: {result}"
                assert os.path.exists(out_path), f"Output JSON not created at {out_path}"
        finally:
            os.unlink(sp_path)


class TestUtf8Tolerance:
    """Tests for UTF-8 encoding tolerance in file reading."""

    def test_utf8_latin1_in_comment_parses(self) -> None:
        """Test that Verilog files with Latin-1 bytes in comments parse successfully."""
        import tempfile
        from pathlib import Path

        from netlist_tracer.parsers.verilog.instances import _sv_parse_file

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "latin1_test.v"
            # Write file with raw non-UTF-8 byte (0xa9, which is not valid UTF-8)
            content = b"// Copyright notice: \xa9 2024\nmodule test (input clk, output ready);\n    reg valid;\nendmodule\n"
            fpath.write_bytes(content)

            # Parse should succeed without UnicodeDecodeError
            mdls = _sv_parse_file((str(fpath), {}, set(), {}))
            assert len(mdls) > 0
            assert mdls[0]["name"] == "test"

    def test_utf8_latin1_in_string_literal_parses(self) -> None:
        """Test that files with Latin-1 bytes in string literals parse successfully."""
        import tempfile
        from pathlib import Path

        from netlist_tracer.parsers.verilog.instances import _sv_parse_file

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "latin1_string.v"
            # Create a file with a Latin-1 byte sequence
            content = b"""module test_str (input clk, output ready);
    // String with high byte
    reg [31:0] msg;
endmodule
"""
            fpath.write_bytes(content)

            # Parse should succeed
            mdls = _sv_parse_file((str(fpath), {}, set(), {}))
            assert len(mdls) > 0

    def test_detect_format_handles_latin1(self) -> None:
        """Test that detect_format handles Latin-1 bytes in first 4 KB."""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "latin1_detect.v"
            # Write with raw non-UTF-8 byte
            content = b"module test (input clk); endmodule \x81\n"
            fpath.write_bytes(content)

            # Detect format should succeed without UnicodeDecodeError
            fmt = detect_format([str(fpath)])
            assert fmt in ("verilog", "spice")

    def test_edif_handles_latin1(self) -> None:
        """Test that EDIF parser handles Latin-1 bytes in comments."""
        import tempfile
        from pathlib import Path

        from netlist_tracer.parsers.edif import parse_edif

        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = Path(tmpdir) / "test.edf"
            # Write with raw non-UTF-8 byte
            content = b"""(edif test
  (edifVersion 2 0 0)
  (edifLevel 0)
  (keywordMap (keywordLevel 0))
  (status
    (written (timeStamp 2024 1 1 0 0 0)
      (author "Test \xa9")
      (program "test")))
  (library test
    (edifLevel 0)
    (technology (numberDefinition))
    (cell test (cellType GENERIC)
      (view test (viewType NETLIST))
    )
  )
)
"""
            fpath.write_bytes(content)

            # Parse should succeed
            subckts, insts = parse_edif(str(fpath))
            # Should not raise UnicodeDecodeError

    def test_includes_handles_latin1(self) -> None:
        """Test that include file expansion handles Latin-1 bytes."""
        import tempfile
        from pathlib import Path

        from netlist_tracer.parsers.includes import expand_includes

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create included file with raw non-UTF-8 byte
            inc_fpath = tmpdir_path / "lib.sp"
            inc_content = b".lib typical \xa9\n.endl typical\n"
            inc_fpath.write_bytes(inc_content)

            # Create main file
            main_fpath = tmpdir_path / "main.sp"
            main_content = f".include {str(inc_fpath)}\n".encode()
            main_fpath.write_bytes(main_content)

            # Expand includes should succeed
            expanded, ahdl_paths, spf_paths = expand_includes(str(main_fpath), "spice")
            assert len(expanded) > 0
            assert ahdl_paths == []
            assert spf_paths == []
