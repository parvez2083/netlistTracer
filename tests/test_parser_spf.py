"""Tests for SPF/DSPF parser functionality (incl. env-gated real-DSPF smoke)."""

from __future__ import annotations

import os
import tempfile
import time
from collections import Counter
from pathlib import Path

import pytest

from netlist_tracer import BidirectionalTracer, NetlistParser
from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.parsers.detect import detect_format
from netlist_tracer.parsers.spf import parse_spf


class TestDetectSpfFormat:
    """Tests for SPF format detection."""

    def test_detect_spf_extension(self) -> None:
        """detect_format returns 'spf' for .spf files."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        spf_file = str(fixture_dir / "simple.spf")

        fmt = detect_format([spf_file])

        assert fmt == "spf"

    def test_detect_dspf_extension(self) -> None:
        """detect_format returns 'spf' for .dspf files with SPF content."""
        # Create a temporary .dspf file with SPF marker
        with tempfile.NamedTemporaryFile(suffix=".dspf", mode="w", delete=False) as f:
            f.write("*|DSPF 1.0\n.SUBCKT TEST_DSPF a b\n.ENDS TEST_DSPF\n")
            dspf_file = f.name

        try:
            fmt = detect_format([dspf_file])
            assert fmt == "spf"
        finally:
            Path(dspf_file).unlink()

    def test_detect_spf_content_marker(self) -> None:
        """detect_format returns 'spf' for *|DSPF marker regardless of extension."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        txt_file = str(fixture_dir / "spf_dspf_marker.txt")

        fmt = detect_format([txt_file])

        assert fmt == "spf"


class TestParseSimpleSpf:
    """Tests for basic SPF parsing."""

    def test_parse_simple_spf_subckt(self) -> None:
        """Parse simple.spf and verify subckt structure."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        spf_file = str(fixture_dir / "simple.spf")

        sbckts, insts, _ = parse_spf(spf_file)

        # Check subckt was parsed
        assert "INV" in sbckts
        inv = sbckts["INV"]
        assert inv.pins == ["in", "out", "vdd", "gnd"]

        # Check SPF metadata in params
        assert "_net_caps" in inv.params
        assert "out" in inv.params["_net_caps"]
        assert inv.params["_net_caps"]["out"] == 1.5e-12  # 1.5 PF in Farads

        assert "_ground_net" in inv.params
        assert inv.params["_ground_net"] == "gnd"

        assert "_pin_aliases" in inv.params
        assert "M1:S" in inv.params["_pin_aliases"]
        assert inv.params["_pin_aliases"]["M1:S"] == ("M1", "S")
        assert inv.params["_pin_aliases"]["M2:D"] == ("M2", "D")

    def test_parse_simple_spf_instances(self) -> None:
        """Parse simple.spf and verify instance extraction.

        Note: R1 and R2 form a series chain, so they merge into 1 instance.
        R1 (in/X -> in/Y) + R2 (in/Y -> gnd) = merged R (in/X -> gnd).
        So we have: 1 merged R + 2 C = 3 instances total.
        """
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        spf_file = str(fixture_dir / "simple.spf")

        _, insts, _ = parse_spf(spf_file)

        # Should have 3 instances (1 merged R from R1+R2, 2 C elements)
        assert len(insts) == 3

        # Check merged R: should have _to_ in name and merged_from populated
        r_insts = [i for i in insts if i.cell_type == "R"]
        assert len(r_insts) == 1
        merged_r = r_insts[0]
        assert "_to_" in merged_r.name
        assert merged_r.params.get("_merged_from") == ["R1", "R2"]

        # Check C1: capacitance element
        c1 = [i for i in insts if i.name == "C1"][0]
        assert c1.cell_type == "C"
        assert c1.parent_cell == "INV"
        assert c1.nets == ["in/X", "gnd"]

    def test_subnode_collapse_on_parse(self) -> None:
        """Verify that net names with subnodes (net:N) are PRESERVED (no collapse).

        R1 and R2 merge into a single R, but the merged instance should preserve
        the full net names as they appear.
        """
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        spf_file = str(fixture_dir / "simple.spf")

        _, insts, _ = parse_spf(spf_file)

        # Find the merged R (R1 and R2 are now merged)
        r_merged = [i for i in insts if i.cell_type == "R"][0]
        # The merged R connects in/X and gnd (from the chain in/X -> in/Y -> gnd)
        assert "in/X" in r_merged.nets
        assert "gnd" in r_merged.nets


class TestSpfPinAliases:
    """Tests for SPF *|I pin alias extraction."""

    def test_pin_alias_extraction(self) -> None:
        """Verify *|I directives populate pin alias map."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic"
        spf_file = str(fixture_dir / "simple.spf")

        sbckts, _, _ = parse_spf(spf_file)
        inv = sbckts["INV"]

        aliases = inv.params.get("_pin_aliases", {})
        assert "M1:S" in aliases
        assert aliases["M1:S"] == ("M1", "S")


class TestMixedDirDispatch:
    """Tests for mixed SPICE + SPF directory parsing."""

    def test_mixed_dir_spice_plus_spf(self) -> None:
        """Parse mixed directory with .sp and .spf files."""
        fixture_dir = Path(__file__).parent / "fixtures" / "synthetic" / "mixed_dir"

        parser = NetlistParser(str(fixture_dir))

        # Both TB and INV should be present
        assert "TB" in parser.subckts
        assert "INV" in parser.subckts

        # Format should be marked as 'mixed'
        assert parser.format == "mixed"

        # Verify SPF INV has the ground net metadata
        inv = parser.subckts["INV"]
        assert "_ground_net" in inv.params
        assert inv.params["_ground_net"] == "gnd"


class TestSpfFormatCollision:
    """Tests for SPF vs SPICE format collision in mixed dirs."""

    def test_spice_wins_over_spf_on_collision(self, caplog: pytest.LogCaptureFixture) -> None:
        """When same subckt in both SPICE and SPF, SPICE version wins (higher priority)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)

            # Create SPICE file with INV (4 pins)
            sp_file = tmpdir_path / "cell.sp"
            sp_file.write_text("""
.SUBCKT INV a b vdd gnd
.ENDS INV
""")

            # Create SPF file with INV (3 pins)
            spf_file = tmpdir_path / "cell.spf"
            spf_file.write_text("""
*|DSPF 1.0
.SUBCKT INV a b c
*|NET b 1.0PF
.ENDS INV
""")

            parser = NetlistParser(str(tmpdir_path))

            # SPICE version (4 pins) should win over SPF (3 pins)
            inv = parser.subckts["INV"]
            assert len(inv.pins) == 4
            assert inv.pins == ["a", "b", "vdd", "gnd"]

            # Check warning was logged (format collision)
            assert any(
                ("spf" in record.message.lower() and "spice" in record.message.lower())
                for record in caplog.records
            )


class TestSpfEmpty:
    """Tests for error handling on empty/invalid SPF files."""

    def test_parse_empty_spf_raises_error(self) -> None:
        """parse_spf raises NetlistParseError for empty file."""
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write("")
            spf_file = f.name

        try:
            with pytest.raises(NetlistParseError, match="empty"):
                parse_spf(spf_file)
        finally:
            Path(spf_file).unlink()

    def test_parse_spf_no_subckt_raises_error(self) -> None:
        """parse_spf raises NetlistParseError if no .SUBCKT found."""
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write("*|DSPF 1.0\n* Just a comment\n")
            spf_file = f.name

        try:
            with pytest.raises(NetlistParseError, match="No .SUBCKT"):
                parse_spf(spf_file)
        finally:
            Path(spf_file).unlink()


class TestSubnodeCollapseNumericOnly:
    """Tests for _normalize_subnode_net identity pass-through."""

    def test_numeric_suffix_preserved(self) -> None:
        """Numeric suffixes :N are now PRESERVED (identity pass-through)."""
        from netlist_tracer.parsers.spf import _normalize_subnode_net

        assert _normalize_subnode_net("ln_X/96:1", ":") == "ln_X/96:1"
        assert _normalize_subnode_net("ln_X/96:2", ":") == "ln_X/96:2"
        assert _normalize_subnode_net("name:42", ":") == "name:42"

    def test_letter_suffixes_preserved(self) -> None:
        """Letter suffixes (:D, :G, :S, :B) are preserved."""
        from netlist_tracer.parsers.spf import _normalize_subnode_net

        assert _normalize_subnode_net("inst/M1:D", ":") == "inst/M1:D"
        assert _normalize_subnode_net("inst/M1:G", ":") == "inst/M1:G"
        assert _normalize_subnode_net("inst/M1:S", ":") == "inst/M1:S"
        assert _normalize_subnode_net("inst/M1:B", ":") == "inst/M1:B"

    def test_mixed_alphanumeric_preserved(self) -> None:
        """Non-numeric suffixes like :ext are preserved."""
        from netlist_tracer.parsers.spf import _normalize_subnode_net

        assert _normalize_subnode_net("name:ext", ":") == "name:ext"
        assert _normalize_subnode_net("inst:abc", ":") == "inst:abc"

    def test_idempotent(self) -> None:
        """Normalization is idempotent."""
        from netlist_tracer.parsers.spf import _normalize_subnode_net

        normalized = _normalize_subnode_net("ln_X/96:1", ":")
        assert _normalize_subnode_net(normalized, ":") == normalized

    def test_empty_suffix_preserved(self) -> None:
        """Net ending with delimiter (empty suffix) is preserved."""
        from netlist_tracer.parsers.spf import _normalize_subnode_net

        assert _normalize_subnode_net("name:", ":") == "name:"


class TestXInstanceWithCelltypeAndParams:
    """Tests for X-instance parsing with cell_type and params."""

    def test_x_instance_basic(self) -> None:
        """Synthetic X-line parses correctly: cell_type and params extracted."""
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT TEST a b c d
XI0 a b c d nch_mac L=1u W=0.5u
.ENDS TEST
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            assert len(insts) == 1
            inst = insts[0]
            assert inst.name == "XI0"
            assert inst.cell_type == "nch_mac"
            assert inst.nets == ["a", "b", "c", "d"]
            assert inst.params.get("L") == "1u"
            assert inst.params.get("W") == "0.5u"
        finally:
            Path(spf_file).unlink()

    def test_x_instance_with_starrc_annotations(self) -> None:
        """X-line with StarRC $x, $y, $angle annotations parses correctly."""
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT TEST a b c d
XI0 a:D b:G c:S d nmos_model L=0.003u nfin=2 $x=0.864 $y=0.245 $angle=90
.ENDS TEST
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            assert len(insts) == 1
            inst = insts[0]
            assert inst.name == "XI0"
            assert inst.cell_type == "nmos_model"
            # Letter terminals should be preserved
            assert inst.nets == ["a:D", "b:G", "c:S", "d"]
            assert inst.params.get("L") == "0.003u"
            assert inst.params.get("nfin") == "2"
            assert inst.params.get("$x") == "0.864"
            assert inst.params.get("$y") == "0.245"
            assert inst.params.get("$angle") == "90"
        finally:
            Path(spf_file).unlink()


class TestMInstancePreservesLetterTerminals:
    """Tests for M-instance letter terminal preservation."""

    def test_m_instance_letter_terminals(self) -> None:
        """M-instance with letter terminal refs preserves them."""
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT TEST d g s b
M1 d:D g:G s:S b:B nch_mac
.ENDS TEST
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            assert len(insts) == 1
            inst = insts[0]
            assert inst.name == "M1"
            assert inst.cell_type == "nch_mac"
            # Letter suffixes should be preserved
            assert inst.nets == ["d:D", "g:G", "s:S", "b:B"]
        finally:
            Path(spf_file).unlink()


class TestSubnodePreservedAtEndOfChain:
    """Test: R instances with numeric subnodes are preserved and then merged."""

    def test_resistor_numeric_subnode_collapse(self) -> None:
        """R instances with numeric subnodes are PRESERVED, then merged via series-R reduction.

        A 2-R chain through intermediate subnodes should merge to 1 R with
        summed value and name reflecting endpoints.
        """
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT TEST ln_X term
R1 ln_X:1 ln_X:2 1.5
R2 ln_X:2 term 0.5
.ENDS TEST
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            # Should have 1 merged R (the 2-R chain)
            assert len(insts) == 1

            merged = insts[0]
            assert merged.cell_type == "R"
            assert "_to_" in merged.name
            assert merged.params.get("_value") == "2"  # 1.5 + 0.5
            assert merged.params.get("_merged_from") == ["R1", "R2"]
        finally:
            Path(spf_file).unlink()


class TestSeriesRMerge:
    """Tests for series-R reduction."""

    def test_series_r_merge_2_chain(self) -> None:
        """AC4: Synthetic SPF with port -> R(10) -> midnet -> R(20) -> term.

        Only those 2 R's on midnet -> result has 1 merged R named '<R1>_to_<R2>'
        with value 30.
        """
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT top in out
R1 in n1 10
R2 n1 out 20
.ENDS top
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            assert len(insts) == 1

            merged = insts[0]
            assert merged.cell_type == "R"
            assert merged.name == "R1_to_R2"
            assert merged.params.get("_value") == "30"
            assert merged.params.get("_merged_from") == ["R1", "R2"]
        finally:
            Path(spf_file).unlink()

    def test_series_r_merge_3_chain(self) -> None:
        """AC5: Synthetic with port -> R(10) -> n1 -> R(20) -> n2 -> R(30) -> term.

        Result has 1 merged R named 'R1_to_R3' (deterministic: sorted endpoints) with value 60.
        """
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT top in out
R1 in n1 10
R2 n1 n2 20
R3 n2 out 30
.ENDS top
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            assert len(insts) == 1

            merged = insts[0]
            assert merged.cell_type == "R"
            assert merged.name == "R1_to_R3"
            assert merged.params.get("_value") == "60"
            # Verify merged_from chain contains all 3 R's (order may vary)
            mrgd_from = merged.params.get("_merged_from")
            assert len(mrgd_from) == 3, f"Expected 3 R's merged, got {len(mrgd_from)}"
            assert set(mrgd_from) == {"R1", "R2", "R3"}, f"Expected R1, R2, R3, got {mrgd_from}"
        finally:
            Path(spf_file).unlink()

    def test_series_r_no_merge_with_transistor(self) -> None:
        """AC6: Synthetic where midnet has 2 R's PLUS one transistor X-instance.

        No merge happens (transistor blocks); R1, R2, X1 all remain.
        """
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT top in out n2 n3 n4
R1 in n1 10
R2 n1 out 20
X1 n1 n2 n3 n4 nch_model
.ENDS top
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            # Should have 3 instances (no merge due to transistor)
            assert len(insts) == 3

            # Find each instance by type
            r_insts = [i for i in insts if i.cell_type == "R"]
            x_insts = [i for i in insts if i.cell_type == "nch_model"]
            assert len(r_insts) == 2
            assert len(x_insts) == 1
        finally:
            Path(spf_file).unlink()

    def test_series_r_caps_dont_block(self) -> None:
        """AC7: Synthetic where midnet has 2 R's + 1 C.

        Merge happens (C doesn't block); merged R named 'R1_to_R2' value '30';
        C1 remains in instance list.
        """
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT top in out gnd
R1 in n1 10
R2 n1 out 20
C1 n1 gnd 0.001p
.ENDS top
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            # Should have 2 instances: 1 merged R + 1 C
            assert len(insts) == 2

            r_insts = [i for i in insts if i.cell_type == "R"]
            c_insts = [i for i in insts if i.cell_type == "C"]
            assert len(r_insts) == 1
            assert len(c_insts) == 1

            merged_r = r_insts[0]
            assert "_to_" in merged_r.name
            assert merged_r.params.get("_value") == "30"
        finally:
            Path(spf_file).unlink()

    def test_series_r_port_net_not_merged(self) -> None:
        """AC8: Port-preservation guard: net marked as a port is NOT merged even if it has exactly 2 R's.

        This test directly exercises the port_nets guard at line 449 of spf.py:
        `if net in port_nets: continue` prevents processing a net if it's a port.

        Directly constructs Instance objects and calls _reduce_series_resistors with
        a custom port_nets set containing 'clk_in' (a net that has 2 R's on it).
        The guard should prevent the merge of R1 and R2 even though they are
        both on clk_in and form a merge candidate.
        """
        from netlist_tracer.model import Instance, SubcktDef
        from netlist_tracer.parsers.spf import _reduce_series_resistors

        # Create synthetic instance list: 2 R's both on net 'clk_in'
        # (simulating a port net that should not be merged)
        r1 = Instance(
            name="R1",
            cell_type="R",
            nets=["clk_in", "n_mid"],
            parent_cell="top",
            params={"_value": "10"},
        )
        r2 = Instance(
            name="R2",
            cell_type="R",
            nets=["clk_in", "n_other"],
            parent_cell="top",
            params={"_value": "20"},
        )

        # Create a synthetic subckt definition with 'clk_in' as a port
        sbckts = {
            "top": SubcktDef(
                name="top",
                pins=["clk_in", "clk_out"],  # clk_in is a PORT
            )
        }

        # Call _reduce_series_resistors with clk_in marked as a port
        result = _reduce_series_resistors(sbckts, [r1, r2], "top")

        # clk_in is a port, so R1 and R2 should NOT be merged despite both being on it
        assert len(result) == 2, f"Expected 2 R instances (no merge), got {len(result)}"
        r_names = {r.name for r in result}
        assert r_names == {"R1", "R2"}, f"Expected R1, R2 unmerged; got {r_names}"

    def test_series_r_parallel_not_merged(self) -> None:
        """AC9: Parallel-R guard: a midnet with 2 R's whose other-terminals are the same net is NOT merged.

        Fixture: port 'in' -> R1 -> n_par (non-port) -> exit,
                 port 'in' -> R2 -> n_par (non-port) -> exit.
        n_par has exactly 2 R's (R1, R2), making it eligible for merge.
        Both R's have endpoints (in, n_par) and (in, n_par), so a == b.
        The parallel guard (line 477: `if a == b: continue`)
        prevents the merge because the resistors are in parallel, not series.
        Result: R1 and R2 remain unmerged.
        """
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT top in
R1 in n_par 10
R2 in n_par 20
.ENDS top
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            # Should have 2 parallel R instances (no merge due to parallel guard)
            assert len(insts) == 2

            r_insts = [i for i in insts if i.cell_type == "R"]
            assert len(r_insts) == 2
            # Verify names are unchanged
            r_names = {r.name for r in r_insts}
            assert r_names == {"R1", "R2"}, f"Expected R1 and R2 unmerged (parallel), got {r_names}"
        finally:
            Path(spf_file).unlink()

    def test_subnode_preservation(self) -> None:
        """AC10: Synthetic SPF with subnodes :1, :2 -> assert subnodes PRESERVED.

        Subnodes are distinct nets (not collapsed to parent).
        """
        with tempfile.NamedTemporaryFile(suffix=".spf", mode="w", delete=False) as f:
            f.write(
                """*|DSPF 1.0
.SUBCKT top sig
R1 sig:1 sig:2 1.5
.ENDS top
"""
            )
            spf_file = f.name

        try:
            _, insts, _ = parse_spf(spf_file)
            assert len(insts) == 1

            r1 = insts[0]
            # R1's nets should contain 'sig:1' and 'sig:2' UNCHANGED
            assert "sig:1" in r1.nets
            assert "sig:2" in r1.nets
            assert r1.nets == ["sig:1", "sig:2"]
        finally:
            Path(spf_file).unlink()


class TestSpfPeek:
    """Peek tests for SPF/DSPF format."""

    def test_peek_basic(self, fixtures_synthetic_dir):
        """Test peek on SPF file returns expected pins."""
        spf_file = os.path.join(fixtures_synthetic_dir, "simple.spf")
        if os.path.exists(spf_file):
            pns = NetlistParser.peek_pins(spf_file, "top")
            # SPF peek reuses spice-family logic
            assert pns is None or isinstance(pns, list)

    def test_peek_dspf_marker(self):
        """Test peek on DSPF format marker."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            dspf_file = os.path.join(tmpdir, "test.dspf")
            with open(dspf_file, "w") as f:
                f.write("*|DSPF\n")
                f.write(".SUBCKT top a b c\n")
                f.write(".ENDS top\n")

            pns = NetlistParser.peek_pins(dspf_file, "top")
            assert pns is not None
            assert "a" in pns
            assert "b" in pns
            assert "c" in pns


# ---------------------------------------------------------------------------
# Real-DSPF smoke tests (env-gated via NETLIST_TRACER_SPF_SAMPLE)
# Folded in from former tests/test_real_dspf_smoke.py to mirror the
# SV interface real-corpus folding pattern.
# ---------------------------------------------------------------------------


@pytest.mark.local
class TestRealDspfSmoke:
    """Tests using real DSPF file if NETLIST_TRACER_SPF_SAMPLE env var is set."""

    @pytest.fixture
    def spf_sample_path(self) -> str | None:
        """Get NETLIST_TRACER_SPF_SAMPLE env var or skip test."""
        path = os.getenv("NETLIST_TRACER_SPF_SAMPLE")
        if not path or not os.path.exists(path):
            pytest.skip("NETLIST_TRACER_SPF_SAMPLE not set or file not found")
        return path

    def test_real_dspf_parse_completes(self, spf_sample_path: str) -> None:
        """Real DSPF file parses without exception."""
        parser = NetlistParser(spf_sample_path)
        assert parser.subckts
        assert len(parser.subckts) > 0

    def test_real_dspf_has_instances(self, spf_sample_path: str) -> None:
        """Real DSPF file produces instances."""
        parser = NetlistParser(spf_sample_path)
        total_insts = sum(len(insts) for insts in parser.instances_by_parent.values())
        assert total_insts > 0

    def test_real_dspf_format_marked(self, spf_sample_path: str) -> None:
        """Real DSPF file format is detected as 'spf'."""
        parser = NetlistParser(spf_sample_path)
        assert parser.format == "spf"

    def test_real_dspf_cell_type_breakdown(self, spf_sample_path: str) -> None:
        """Real DSPF cell_type breakdown shows real model names, not $-prefixed."""
        parser = NetlistParser(spf_sample_path)
        top = list(parser.subckts.keys())[0]
        insts = parser.instances_by_parent.get(top, [])
        ctps = Counter(i.cell_type for i in insts)
        dllr_pfxd = {ct for ct in ctps.keys() if ct.startswith("$")}
        assert len(dllr_pfxd) == 0, f"Found {len(dllr_pfxd)} $-prefixed cell_types: {dllr_pfxd}"
        has_transistor_or_passive = any(
            ct.startswith(("nch_", "pch_", "r_", "c_")) or ct in ("R", "C", "L", "M")
            for ct in ctps.keys()
        )
        assert has_transistor_or_passive, (
            f"Expected at least one transistor or passive element; "
            f"got cell_types: {list(ctps.keys())}"
        )

    def test_real_dspf_tracer_ac7a_returns_paths(self, spf_sample_path: str) -> None:
        """AC7a: Real DSPF tracer returns >= 1 path (lateral walk through galvanic R/L only)."""
        parser = NetlistParser(spf_sample_path)
        top = list(parser.subckts.keys())[0]
        top_subckt = parser.subckts[top]
        tracer = BidirectionalTracer(parser)
        found_paths = False
        for pin in top_subckt.pins[:3]:
            paths = tracer.trace(top, pin, max_depth=3)
            assert isinstance(paths, list), "trace() should return a list"
            if len(paths) >= 1:
                found_paths = True
                break
        assert found_paths, (
            f"Expected at least one pin on {top} to trace to >= 1 path "
            f"(AC7a); tested pins: {top_subckt.pins[:3]}"
        )

    def test_real_dspf_tracer_ac7b_thru_steps(self, spf_sample_path: str) -> None:
        """AC7b: Traced paths should contain direction='thru' for R cell_type (not C)."""
        parser = NetlistParser(spf_sample_path)
        top = list(parser.subckts.keys())[0]
        top_subckt = parser.subckts[top]
        tracer = BidirectionalTracer(parser)
        found_thru_r = False
        for pin in top_subckt.pins[:5]:
            paths = tracer.trace(top, pin, max_depth=5)
            for path in paths:
                for step in path:
                    if step.direction == "thru" and step.cell == "R":
                        found_thru_r = True
                        break
            if found_thru_r:
                break
        assert found_thru_r, (
            "Expected at least one path with direction='thru' and cell='R'; "
            "caps should emit direction='endpoint', not be walked through"
        )

    def test_real_dspf_tracer_ac7d_caps_skipped(self, spf_sample_path: str) -> None:
        """AC7d: Caps (C, K) are parasitic noise; no path step should reference them."""
        parser = NetlistParser(spf_sample_path)
        top = list(parser.subckts.keys())[0]
        top_subckt = parser.subckts[top]
        tracer = BidirectionalTracer(parser)
        cap_steps_found: list[tuple[str, str, str]] = []
        for pin in top_subckt.pins[:5]:
            paths = tracer.trace(top, pin, max_depth=5)
            for path in paths:
                for step in path:
                    if step.cell in ("C", "K"):
                        cap_steps_found.append((pin, step.direction, str(step.instance_name)))
        assert not cap_steps_found, (
            f"Caps (C, K) should be SKIPPED during lateral walk (parasitic noise), "
            f"but found {len(cap_steps_found)} steps referencing them: {cap_steps_found[:5]}"
        )

    def test_real_dspf_r_count_reduces(self, spf_sample_path: str) -> None:
        """Real small DSPF R instance count drops by >= 30% post-reduction."""
        parser = NetlistParser(spf_sample_path)
        top = list(parser.subckts.keys())[0]
        insts = parser.instances_by_parent.get(top, [])
        r_count_reduced = sum(1 for i in insts if i.cell_type == "R")
        merged_count = sum(
            len(i.params.get("_merged_from", [])) - 1
            for i in insts
            if i.cell_type == "R" and i.params.get("_merged_from")
        )
        r_count_unreduced_est = r_count_reduced + merged_count
        reduction_pct = (
            (r_count_unreduced_est - r_count_reduced) / r_count_unreduced_est * 100
            if r_count_unreduced_est > 0
            else 0
        )
        assert reduction_pct >= 30, (
            f"Expected R reduction >= 30%; got {reduction_pct:.1f}% "
            f"(post-reduction: {r_count_reduced}, estimated unreduced: {r_count_unreduced_est})"
        )

    def test_real_dspf_merged_names_present(self, spf_sample_path: str) -> None:
        """Real small DSPF has merged R names (_to_) and _merged_from populated."""
        parser = NetlistParser(spf_sample_path)
        top = list(parser.subckts.keys())[0]
        insts = parser.instances_by_parent.get(top, [])
        merged_names = [i for i in insts if i.cell_type == "R" and "_to_" in i.name]
        assert len(merged_names) > 0, "Expected at least one R instance with '_to_' in name"
        merged_from_populated = [
            i
            for i in merged_names
            if i.params.get("_merged_from") and len(i.params.get("_merged_from", [])) > 1
        ]
        assert len(merged_from_populated) > 0, (
            "Expected at least one merged R to have _merged_from with > 1 entries"
        )

    def test_real_dspf_tracer_reaches_transistor_via_merged_chain(
        self, spf_sample_path: str
    ) -> None:
        """At least one path: thru-step with _to_ instance name AND transistor endpoint."""
        parser = NetlistParser(spf_sample_path)
        top = list(parser.subckts.keys())[0]
        top_subckt = parser.subckts[top]
        tracer = BidirectionalTracer(parser)
        found_merged_to_transistor = False
        for pin in top_subckt.pins[:10]:
            paths = tracer.trace(top, pin, max_depth=5)
            for path in paths:
                has_merged_r = False
                has_transistor = False
                for step in path:
                    if step.direction == "thru" and "_to_" in (step.instance_name or ""):
                        has_merged_r = True
                    if step.direction == "endpoint" and step.cell and step.cell[0] in ("n", "p"):
                        has_transistor = True
                if has_merged_r and has_transistor:
                    found_merged_to_transistor = True
                    break
            if found_merged_to_transistor:
                break
        assert found_merged_to_transistor, (
            "Expected at least one path with merged R (name contains '_to_') "
            "reaching a transistor endpoint"
        )

    def test_reduction_perf_under_5s(self, spf_sample_path: str) -> None:
        """Performance: small DSPF reduction completes in < 5 seconds."""
        start_time = time.time()
        NetlistParser(spf_sample_path)
        elapsed = time.time() - start_time
        assert elapsed < 5.0, (
            f"Expected parse + reduction to complete in < 5.0 seconds; took {elapsed:.2f}s"
        )

    def test_peek_perf_vs_full_parse(self, spf_sample_path: str) -> None:
        """Peek performance is dramatically faster than full parse.

        Peek on a large SPF file should complete in < 2 seconds, while
        full parse takes > 30 seconds (relative ratio test).
        """
        start_peek = time.time()
        top_cell = "top"  # SPF files typically have 'top' or similar
        pns = NetlistParser.peek_pins(spf_sample_path, top_cell)
        elapsed_peek = time.time() - start_peek
        start_parse = time.time()
        NetlistParser(spf_sample_path)
        elapsed_parse = time.time() - start_parse
        assert elapsed_peek < 2.0, (
            f"Expected peek to complete in < 2.0 seconds; took {elapsed_peek:.2f}s"
        )
        if pns is not None:
            ratio = elapsed_parse / elapsed_peek if elapsed_peek > 0.01 else 1000
            assert ratio > 5, (
                f"Expected full parse to be >5x slower than peek; "
                f"ratio={ratio:.1f} (peek={elapsed_peek:.2f}s, parse={elapsed_parse:.2f}s)"
            )
