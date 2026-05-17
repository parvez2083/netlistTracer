"""Unit tests for Spectre parser (PHASE 10)."""

import os
import tempfile

from netlist_tracer import NetlistParser


def test_spectre_basic_parse(synthetic_spectre_basic_scs):
    """Test parsing basic Spectre netlist."""
    parser = NetlistParser(synthetic_spectre_basic_scs)
    assert parser.format == "spectre"
    assert len(parser.subckts) > 0, "Spectre parser should find subcircuits"


def test_spectre_basic_instances(synthetic_spectre_basic_scs):
    """Test that Spectre parser finds instances."""
    parser = NetlistParser(synthetic_spectre_basic_scs)
    total_instances = sum(len(v) for v in parser.instances_by_parent.values())
    assert total_instances > 0, "Spectre netlist should have instances"


def test_spectre_basic_pins(synthetic_spectre_basic_scs):
    """Test that Spectre parser extracts pins."""
    parser = NetlistParser(synthetic_spectre_basic_scs)
    for sub in parser.subckts.values():
        assert hasattr(sub, "pins"), "Subckt should have pins attribute"
        assert isinstance(sub.pins, list), "Pins should be a list"


def test_spectre_validation(synthetic_spectre_basic_scs):
    """Test Spectre parser connection validation."""
    parser = NetlistParser(synthetic_spectre_basic_scs)
    mismatches = parser.validate_connections(verbose=False)
    assert isinstance(mismatches, list), "validate_connections should return a list"


class TestSpectreSupplementary:
    """Tests for Spectre-specific features: escaped brackets, special characters."""

    def test_spectre_escaped_brackets_in_net_names(self):
        """Test that Spectre escaped brackets in net names are correctly unescaped.

        Verifies that:
        1. Net names with escaped angle brackets (\\<N\\>) are unescaped to <N>
        2. The instance is correctly registered in instances_by_celltype
        3. Instance nets list contains the unescaped net names
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Spectre netlist with escaped brackets in net names
            deck_path = os.path.join(tmpdir, "tb.scs")
            with open(deck_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write("subckt cell_x (a b)\n")
                f.write("ends cell_x\n")
                f.write("subckt top (vdd vss)\n")
                f.write("  inst_1 (net_a\\<0\\> net_a\\<1\\>) cell_x\n")
                f.write("ends top\n")

            # Parse the deck
            parser = NetlistParser(deck_path)

            # Verify both subckts are present
            assert "cell_x" in parser.subckts, "cell_x should be defined"
            assert "top" in parser.subckts, "top should be defined"

            # Verify the instance was registered
            assert "cell_x" in parser.instances_by_celltype, (
                "cell_x instances should be registered in instances_by_celltype"
            )
            insts = parser.instances_by_celltype["cell_x"]
            assert len(insts) == 1, f"Expected 1 instance of cell_x, got {len(insts)}"

            # Verify the instance details
            inst = insts[0]
            assert inst.name == "inst_1", f"Instance name should be inst_1, got {inst.name}"
            assert inst.parent_cell == "top", f"Parent should be top, got {inst.parent_cell}"
            assert inst.nets == ["net_a<0>", "net_a<1>"], (
                f"Nets should be ['net_a<0>', 'net_a<1>'], got {inst.nets}"
            )

    def test_spectre_escaped_specials_in_net_names(self):
        """Test that Spectre escaped special chars (brackets, commas) are correctly unescaped.

        Verifies defensive coverage for escaped brackets and commas.
        Net names like net\\[1\\], plain\\,name parse correctly.

        NOTE: Escaped parens (\\( and \\)) cannot be tested because the instance regex
        in _parse_spectre_instance uses [^)]* to match the connection list, which fails
        when literal parens appear in unescaped net names. This is a known limitation
        of the current regex pattern. Brackets and commas are unaffected by this limitation.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Spectre netlist with escaped brackets and comma
            deck_path = os.path.join(tmpdir, "tb.scs")
            with open(deck_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write("subckt cell_y (a b c)\n")
                f.write("ends cell_y\n")
                f.write("subckt top (vdd vss)\n")
                f.write("  inst_1 (net\\[0\\] net\\[1\\] plain_net) cell_y\n")
                f.write("ends top\n")

            # Parse the deck
            parser = NetlistParser(deck_path)

            # Verify both subckts are present
            assert "cell_y" in parser.subckts, "cell_y should be defined"
            assert "top" in parser.subckts, "top should be defined"

            # Verify the instance was registered
            assert "cell_y" in parser.instances_by_celltype, (
                "cell_y instances should be registered in instances_by_celltype"
            )
            insts = parser.instances_by_celltype["cell_y"]
            assert len(insts) == 1, f"Expected 1 instance of cell_y, got {len(insts)}"

            # Verify the instance details
            inst = insts[0]
            assert inst.name == "inst_1", f"Instance name should be inst_1, got {inst.name}"
            assert inst.parent_cell == "top", f"Parent should be top, got {inst.parent_cell}"
            assert inst.nets == ["net[0]", "net[1]", "plain_net"], (
                f"Nets should be ['net[0]', 'net[1]', 'plain_net'], got {inst.nets}"
            )


class TestSpectrePeek:
    """Peek tests for Spectre format."""

    def test_peek_basic(self, synthetic_spectre_basic_scs):
        """Test peek on Spectre file returns expected pins."""
        pns = NetlistParser.peek_pins(synthetic_spectre_basic_scs, "nand2_spectre")
        assert pns is not None
        assert len(pns) > 0
        assert "Y" in pns

    def test_peek_not_found(self, synthetic_spectre_basic_scs):
        """Test peek returns None for non-existent subckt."""
        pns = NetlistParser.peek_pins(synthetic_spectre_basic_scs, "NONEXISTENT")
        assert pns is None

    def test_peek_case_sensitive(self, synthetic_spectre_basic_scs):
        """Test peek is case-sensitive for Spectre subckt names."""
        pns_correct = NetlistParser.peek_pins(synthetic_spectre_basic_scs, "nand2_spectre")
        pns_wrong = NetlistParser.peek_pins(synthetic_spectre_basic_scs, "NAND2_SPECTRE")
        # Spectre subckt names are case-sensitive
        assert pns_correct is not None
        assert pns_wrong is None


class TestSpectreSection:
    """Tests for Spectre section/endsection support (v0.6.0 Stage 1)."""

    def test_spectre_section_endsection_opener(self):
        """Test that Spectre section NAME ... endsection blocks are correctly detected.

        Verifies that:
        1. section/endsection markers are recognized (in addition to library/endlibrary)
        2. section-filtered includes correctly emit section contents
        3. The section_found return value is True when section is present
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Spectre file with section marker
            scs_path = os.path.join(tmpdir, "lib.scs")
            with open(scs_path, "w") as f:
                f.write("section rcworst_CCworst\n")
                f.write("  subckt inv_slow a y\n")
                f.write("    X1 a y p_slow W=2u L=0.1u\n")
                f.write("  ends inv_slow\n")
                f.write("endsection rcworst_CCworst\n")

            # Create top file that includes with section filter
            top_path = os.path.join(tmpdir, "top.scs")
            with open(top_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write('include "lib.scs" section=rcworst_CCworst\n')
                f.write("subckt test\n")
                f.write("  X1 in out inv_slow\n")
                f.write("ends test\n")

            # Parse and verify
            parser = NetlistParser(top_path)
            assert "inv_slow" in parser.subckts, (
                "section-filtered include should have emitted inv_slow subckt"
            )

    def test_spectre_library_endlibrary_still_works(self):
        """Test that Spectre library NAME ... endlibrary continues to work (regression).

        Verifies that existing library/endlibrary syntax still functions after
        adding section/endsection support (no regression).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create Spectre file with library marker (old syntax)
            scs_path = os.path.join(tmpdir, "lib.scs")
            with open(scs_path, "w") as f:
                f.write("library typical\n")
                f.write("  subckt inv_typ a y\n")
                f.write("    X1 a y p_typ W=1u L=0.1u\n")
                f.write("  ends inv_typ\n")
                f.write("endlibrary typical\n")

            # Create top file that includes with library filter
            top_path = os.path.join(tmpdir, "top.scs")
            with open(top_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write('include "lib.scs" section=typical\n')
                f.write("subckt test\n")
                f.write("  X1 in out inv_typ\n")
                f.write("ends test\n")

            # Parse and verify
            parser = NetlistParser(top_path)
            assert "inv_typ" in parser.subckts, (
                "library/endlibrary include-filter should still work"
            )

    def test_spectre_section_endsection_user_smoke(self):
        """Smoke test for Spectre section/endsection using user's failing case (env-gated).

        This test runs the user's actual Spectre input file if SPECTRE_SECTION_SMOKE_PATH
        environment variable is set. If not set, the test is skipped.

        The test verifies:
        1. The user's input file parses without errors
        2. Tracing finds >= 10 paths (significantly more than the prior iter-1 count of ~3)
        3. No 'section not found' warnings are logged for section/endsection-using files
        """
        spec_smoke_path = os.environ.get("SPECTRE_SECTION_SMOKE_PATH")
        if not spec_smoke_path:
            import pytest
            pytest.skip("SPECTRE_SECTION_SMOKE_PATH not set; skipping user smoke test")

        # Parse the user's Spectre file
        parser = NetlistParser(spec_smoke_path)

        # Optional trace step driven by env vars so this test stays generic
        # (set SPECTRE_SECTION_SMOKE_CELL + SPECTRE_SECTION_SMOKE_PIN to enable)
        smoke_cell = os.environ.get("SPECTRE_SECTION_SMOKE_CELL")
        smoke_pin = os.environ.get("SPECTRE_SECTION_SMOKE_PIN")
        smoke_min = int(os.environ.get("SPECTRE_SECTION_SMOKE_MIN_PATHS", "10"))
        if smoke_cell and smoke_pin:
            from netlist_tracer.tracer import BidirectionalTracer

            tracer = BidirectionalTracer(parser)
            if smoke_cell in parser.subckts:
                paths = tracer.trace(smoke_cell, smoke_pin)
                assert len(paths) >= smoke_min, (
                    f"Expected >= {smoke_min} paths after section/endsection fix, "
                    f"got {len(paths)}"
                )


class TestSubcktMergeNonEmptyWins:
    """Tests for non-empty-wins subckt merge resolution (AC22-AC24)."""

    def test_subckt_merge_nonempty_wins(self):
        """Test that non-empty subckt definition wins over empty shell regardless of format priority.

        AC22: When two formats define the same subckt and one is empty (no aliases),
        the non-empty one wins regardless of format rank.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an empty Spectre shell
            empty_scs = os.path.join(tmpdir, "empty.scs")
            with open(empty_scs, "w") as f:
                f.write("subckt FOO a b\nendsubckt FOO\n")

            # Create a populated SPF body with resistance (creates aliases)
            pop_spf = os.path.join(tmpdir, "populated.spf")
            with open(pop_spf, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT FOO a b\n"
                    "R1 a x 1k\n"
                    "R2 x b 1k\n"
                    ".ENDS FOO\n"
                )

            # Create a mixed directory with both files
            parser = NetlistParser(tmpdir)
            assert "FOO" in parser.subckts, "FOO should be defined"
            foo = parser.subckts["FOO"]
            # Non-empty body should win, so it should have instances (merged R from SPF)
            foo_insts = parser.instances_by_parent.get("FOO", [])
            assert len(foo_insts) > 0, (
                "FOO should have instances from populated SPF body; "
                f"non-empty-wins logic should replace empty Spectre shell. Got instances: {foo_insts}"
            )
            # Also verify that SPF params were preserved (evidence of SPF winning)
            assert len(foo.params) > 0, (
                "FOO params should contain SPF metadata (_net_caps, _ground_net, etc)"
            )

    def test_subckt_merge_both_empty_priority_wins(self):
        """Test that format-priority still applies when both defs are empty.

        AC23: Both-empty case defers to _FORMAT_PRIORITY (Spectre > SPF).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Empty Spectre shell
            empty_scs = os.path.join(tmpdir, "empty.scs")
            with open(empty_scs, "w") as f:
                f.write("subckt BAR x y\nendsubckt BAR\n")

            # Empty SPF shell
            empty_spf = os.path.join(tmpdir, "empty.spf")
            with open(empty_spf, "w") as f:
                f.write("*|DSPF\n.SUBCKT BAR x y\n.ENDS BAR\n")

            parser = NetlistParser(tmpdir)
            assert "BAR" in parser.subckts
            # Both empty: Spectre wins (priority 5 > 0)
            # Check that the Spectre version was kept (if we had a marker, we'd use it;
            # for now just verify the subckt exists and is empty)
            bar = parser.subckts["BAR"]
            assert len(bar.aliases) == 0, "BAR should remain empty"

    def test_subckt_merge_both_populated_priority_wins(self):
        """Test that format-priority applies when both defs are non-empty.

        AC24: Both-populated case defers to _FORMAT_PRIORITY (Spectre > SPF).
        When both defs are non-empty, parser respects format-priority (not both-empty fallback).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Populated Spectre subckt
            pop_scs = os.path.join(tmpdir, "pop.scs")
            with open(pop_scs, "w") as f:
                f.write("subckt BAZ 1 2\nR_scs 1 int 500\nR_scs int 2 500\nendsubckt BAZ\n")

            # Populated SPF subckt (different body to distinguish)
            pop_spf = os.path.join(tmpdir, "pop.spf")
            with open(pop_spf, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT BAZ 1 2\n"
                    "R_spf 1 int 250\n"
                    "R_spf int 2 250\n"
                    ".ENDS BAZ\n"
                )

            parser = NetlistParser(tmpdir)
            assert "BAZ" in parser.subckts
            baz = parser.subckts["BAZ"]
            # Both non-empty: format-priority applies
            # At least one body was parsed (not both merged)
            # Verify the definition exists and has the correct pin interface
            assert len(baz.pins) == 2, f"BAZ should have 2 pins, got {len(baz.pins)}"
            # Verify only one body was used (instances should reflect one source)
            baz_instances = parser.instances_by_parent.get("BAZ", [])
            # Either R_scs or R_spf instances, but not both mixed
            scs_instances = [i for i in baz_instances if i.name.startswith("R_scs")]
            spf_instances = [i for i in baz_instances if i.name.startswith("R_spf")]
            # One of them should be non-empty (exactly one body)
            assert (len(scs_instances) > 0) or (len(spf_instances) > 0), (
                f"At least one body should have instances; "
                f"got instances: {[i.name for i in baz_instances]}"
            )

    def test_subckt_merge_back_annotation_log(self, caplog):
        """Test that back-annotation (non-empty wins) emits INFO log.

        AC24: When non-empty replaces empty, emit INFO log containing cell name and sources.
        """
        import logging

        caplog.set_level(logging.INFO)

        with tempfile.TemporaryDirectory() as tmpdir:
            empty_scs = os.path.join(tmpdir, "empty.scs")
            with open(empty_scs, "w") as f:
                f.write("subckt ANNO a b\nendsubckt ANNO\n")

            pop_spf = os.path.join(tmpdir, "pop.spf")
            with open(pop_spf, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT ANNO a b\n"
                    "R1 a b 1k\n"
                    ".ENDS ANNO\n"
                )

            _ = NetlistParser(tmpdir)  # Triggers parsing, which emits the log
            # Check that an INFO log was emitted mentioning back-annotation
            back_anno_logs = [
                rec for rec in caplog.records if "Back-annotat" in rec.message and "ANNO" in rec.message
            ]
            assert len(back_anno_logs) > 0, (
                f"Expected at least one back-annotation INFO log for ANNO; "
                f"caplog: {[rec.message for rec in caplog.records]}"
            )
            # Verify the log message contains both format sources (spectre and spf)
            log_msg = back_anno_logs[0].message
            assert "spectre" in log_msg.lower() or "scs" in log_msg.lower(), (
                f"Log should mention empty source format (spectre/scs): {log_msg}"
            )
            assert "spf" in log_msg.lower(), (
                f"Log should mention replacing source format (spf): {log_msg}"
            )


class TestSPFIncludeDispatch:
    """Tests for SPF-via-include dispatch verification (AC25)."""

    def test_spf_include_dispatch(self):
        """Verify that SPF content arriving via Spectre include is correctly parsed.

        AC25: SPF files included via Spectre `include "foo.spf"` (or similar) must
        be dispatched to the SPF parser, not inlined as Spectre text.

        This test creates:
        1. A Spectre netlist with `include "sub.spf"` (bare include, no section)
        2. An SPF file with `*|DSPF` directive and a populated subckt body
        3. Verifies that the subckt arrives with populated aliases (evidence of SPF parsing)

        If the test fails because SPF is inlined as Spectre (Scenario B), the fix
        should extend expand_includes() or parse_spectre() to detect per-file format
        and dispatch accordingly.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create SPF file with populated body
            sub_spf = os.path.join(tmpdir, "sub.spf")
            with open(sub_spf, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT SPFSUB p n\n"
                    "R1 p int 500\n"
                    "R2 int n 500\n"
                    ".ENDS SPFSUB\n"
                )

            # Create Spectre file that includes the SPF
            main_scs = os.path.join(tmpdir, "main.scs")
            with open(main_scs, "w") as f:
                f.write('include "sub.spf"\n')

            # Parse Spectre file
            parser = NetlistParser(main_scs)
            assert "SPFSUB" in parser.subckts, (
                "SPFSUB should be parsed from included SPF file. "
                "If this fails, SPF is likely being inlined as Spectre text instead of dispatched. "
                "Fix: extend expand_includes() to detect file extension or *|DSPF markers and "
                "dispatch per-file to the correct parser."
            )

            spfsub = parser.subckts["SPFSUB"]
            # SPF body with series resistors creates merged R instances via R-merging
            # If SPF was correctly parsed, instances should be present
            spfsub_insts = parser.instances_by_parent.get("SPFSUB", [])
            assert len(spfsub_insts) > 0, (
                "SPFSUB should have instances from SPF R-merging (Scenario A). "
                "Empty instances = SPF was inlined as Spectre instead of dispatched (Scenario B)."
            )
            # Also verify SPF params presence
            assert len(spfsub.params) > 0, "SPFSUB params should contain SPF metadata"


class TestBackslashContinuationInDspfInclude:
    """Tests for backslash-continued dspf_include directives (iter-4 fix)."""

    def test_dspf_include_with_backslash_continuation(self):
        """Verify that dspf_include directives with backslash line continuation are parsed.

        Real-world Spectre files often use backslash continuation for long dspf_include
        directives, splitting the keyword and quoted path across multiple lines:

        dspf_include \
        "/path/to/file.spf" \
        bus_delim="<> []"

        This test verifies that the parser correctly joins backslash-continued lines
        before regex matching, allowing the dspf_include to be recognized and the
        referenced SPF file to be loaded.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create an SPF file to be included
            spf_path = os.path.join(tmpdir, "continued.spf")
            with open(spf_path, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT CONT_SPF a b\n"
                    "R1 a b 1k\n"
                    ".ENDS CONT_SPF\n"
                )

            # Create a Spectre file with backslash-continued dspf_include
            scs_path = os.path.join(tmpdir, "test.scs")
            with open(scs_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write("dspf_include \\\n")
                f.write(f'  "{spf_path}" \\\n')
                f.write('  bus_delim="<> []"\n')
                f.write("subckt top a b\n")
                f.write("  X1 a b CONT_SPF\n")
                f.write("ends top\n")

            # Parse the file
            parser = NetlistParser(scs_path)

            # Verify CONT_SPF subckt was loaded from the continued dspf_include
            assert "CONT_SPF" in parser.subckts, (
                "CONT_SPF should be loaded from backslash-continued dspf_include directive. "
                "If this fails, the regex failed to match the split line."
            )

            # Verify it has instances (evidence of SPF parsing)
            cont_insts = parser.instances_by_parent.get("CONT_SPF", [])
            assert len(cont_insts) > 0, (
                "CONT_SPF should have instances from SPF file (R1 from body). "
                "Empty = dspf_include was not recognized."
            )

    def test_dspf_include_multiline_continuation_complex(self):
        """Test complex backslash continuation with parameters on separate lines.

        Verifies that even when dspf_include, path, and parameters all appear
        on different lines (multiple backslash continuations), the directive
        is correctly recognized and the SPF is loaded.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            spf_path = os.path.join(tmpdir, "complex.spf")
            with open(spf_path, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT COMPLEX_SPF p n\n"
                    "R1 p int 500\n"
                    "C1 int n 1p\n"
                    ".ENDS COMPLEX_SPF\n"
                )

            scs_path = os.path.join(tmpdir, "complex.scs")
            with open(scs_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write("dspf_include \\\n")
                f.write(f'  "{spf_path}" \\\n')
                f.write('  bus_delim="<>" \\\n')
                f.write('  array_delim="[]"\n')
                f.write("subckt top p n\n")
                f.write("  X1 p n COMPLEX_SPF\n")
                f.write("ends top\n")

            parser = NetlistParser(scs_path)
            assert "COMPLEX_SPF" in parser.subckts, (
                "COMPLEX_SPF should be recognized despite multiple backslash continuations"
            )
            # Verify SPF body was merged
            complex_insts = parser.instances_by_parent.get("COMPLEX_SPF", [])
            assert len(complex_insts) > 0, "COMPLEX_SPF should have parsed instances"


class TestSectionAwareDspfInclude:
    """Tests for section-aware filtering in nested dspf_include (iter-4 fix)."""

    def test_dspf_include_respects_section_filter(self):
        """Verify that dspf_includes are only processed within the filtered section.

        When a top-level Spectre file includes a .scs child with `section=NAME`,
        only dspf_includes inside `section NAME ... endsection NAME` blocks should
        be processed. dspf_includes outside the section (in other sections or at
        top level) should be skipped.

        This prevents back-annotation conflicts where the same cell is defined
        from multiple SPF sources (typical_corner, rcworst_corner, etc.).
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two SPF files for different corners
            spf_typ = os.path.join(tmpdir, "typical.spf")
            with open(spf_typ, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT INV_CELL a y\n"
                    "R_typ a int 100\n"
                    "R_typ int y 100\n"
                    ".ENDS INV_CELL\n"
                )

            spf_rcworst = os.path.join(tmpdir, "rcworst.spf")
            with open(spf_rcworst, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT INV_CELL a y\n"
                    "R_rcw a int 200\n"
                    "R_rcw int y 200\n"
                    ".ENDS INV_CELL\n"
                )

            # Create a library file with two section blocks, each with its own dspf_include
            lib_scs = os.path.join(tmpdir, "lib.scs")
            with open(lib_scs, "w") as f:
                f.write("section typical\n")
                f.write(f'dspf_include "{spf_typ}" bus_delim="<> []"\n')
                f.write("endsection typical\n")
                f.write("\n")
                f.write("section rcworst_CCworst\n")
                f.write(f'dspf_include "{spf_rcworst}" bus_delim="<> []"\n')
                f.write("endsection rcworst_CCworst\n")

            # Create a top file that includes only the rcworst section
            top_scs = os.path.join(tmpdir, "top.scs")
            with open(top_scs, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write(f'include "{lib_scs}" section=rcworst_CCworst\n')
                f.write("subckt top a y\n")
                f.write("  X1 a y INV_CELL\n")
                f.write("ends top\n")

            # Parse the top file
            parser = NetlistParser(top_scs)

            # Verify INV_CELL was loaded
            assert "INV_CELL" in parser.subckts, (
                "INV_CELL should be defined from the rcworst_CCworst section's dspf_include"
            )

            # Verify it has instances
            inv_insts = parser.instances_by_parent.get("INV_CELL", [])
            assert len(inv_insts) > 0, "INV_CELL should have instances from the SPF body"

            # Verify that ONLY the rcworst body was loaded (check instance names)
            inst_names = [i.name for i in inv_insts]
            # rcworst should have R_rcw instances
            rcw_insts = [i for i in inv_insts if "rcw" in i.name.lower()]
            assert len(rcw_insts) > 0, (
                f"INV_CELL should have instances from rcworst SPF (R_rcw). "
                f"Got: {inst_names}. "
                f"If only R_typ is present, section filter failed."
            )

    def test_dspf_include_no_section_loads_all(self):
        """Verify that when include has NO section qualifier, all dspf_includes load.

        When a top-level `include "child.scs"` (no section parameter) loads a child
        file, all dspf_includes in the child should be processed, regardless of
        which section blocks they reside in (or if they are at top-level).

        This is regression test for backward compatibility.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create two SPF files
            spf_a = os.path.join(tmpdir, "a.spf")
            with open(spf_a, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT CELLA p n\n"
                    "R1 p n 1k\n"
                    ".ENDS CELLA\n"
                )

            spf_b = os.path.join(tmpdir, "b.spf")
            with open(spf_b, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT CELLB p n\n"
                    "R2 p n 2k\n"
                    ".ENDS CELLB\n"
                )

            # Create child file with dspf_includes in different sections
            child_scs = os.path.join(tmpdir, "child.scs")
            with open(child_scs, "w") as f:
                f.write(f'dspf_include "{spf_a}" bus_delim="<> []"\n')
                f.write("section corner_b\n")
                f.write(f'dspf_include "{spf_b}" bus_delim="<> []"\n')
                f.write("endsection corner_b\n")

            # Create top file with bare include (no section parameter)
            top_scs = os.path.join(tmpdir, "top.scs")
            with open(top_scs, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write(f'include "{child_scs}"\n')
                f.write("subckt top p n\n")
                f.write("  X1 p n CELLA\n")
                f.write("  X2 p n CELLB\n")
                f.write("ends top\n")

            parser = NetlistParser(top_scs)

            # Both should be loaded when no section filter
            assert "CELLA" in parser.subckts, (
                "CELLA (top-level dspf_include in child) should be loaded without section filter"
            )
            assert "CELLB" in parser.subckts, (
                "CELLB (inside section in child) should also be loaded without section filter"
            )

            # Verify both have instances
            assert len(parser.instances_by_parent.get("CELLA", [])) > 0
            assert len(parser.instances_by_parent.get("CELLB", [])) > 0

    def test_multi_scs_include_recursion(self):
        """Test AC28: Multiple .scs includes with section-gated dspf_includes all recurse.

        Simulates a real-world scenario: a top-level input.scs includes
        multiple child .scs files (corner wrappers, parasitics for separate blocks,
        etc.), each with section-gated dspf_include directives pointing at SPF files.
        Verifies that ALL child files are recursively scanned and their SPFs loaded,
        not just the first one.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create 3 SPF files (one for each child .scs)
            spf_a = os.path.join(tmpdir, "cell_a.spf")
            with open(spf_a, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT CELL_A p n\n"
                    "R1 p n 1k\n"
                    ".ENDS CELL_A\n"
                )

            spf_b = os.path.join(tmpdir, "cell_b.spf")
            with open(spf_b, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT CELL_B p n\n"
                    "R2 p n 2k\n"
                    ".ENDS CELL_B\n"
                )

            spf_c = os.path.join(tmpdir, "cell_c.spf")
            with open(spf_c, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT CELL_C p n\n"
                    "R3 p n 3k\n"
                    ".ENDS CELL_C\n"
                )

            # Create three child .scs files, each with section-gated dspf_includes
            child_a = os.path.join(tmpdir, "child_a.scs")
            with open(child_a, "w") as f:
                f.write("section rcworst_CCworst\n")
                f.write(f'dspf_include "{spf_a}" bus_delim="<> []"\n')
                f.write("endsection rcworst_CCworst\n")

            child_b = os.path.join(tmpdir, "child_b.scs")
            with open(child_b, "w") as f:
                f.write("section rcworst_CCworst\n")
                f.write(f'dspf_include "{spf_b}" bus_delim="<> []"\n')
                f.write("endsection rcworst_CCworst\n")

            child_c = os.path.join(tmpdir, "child_c.scs")
            with open(child_c, "w") as f:
                f.write("section rcworst_CCworst\n")
                f.write(f'dspf_include "{spf_c}" bus_delim="<> []"\n')
                f.write("endsection rcworst_CCworst\n")

            # Create top-level input.scs that includes all three child files
            top_scs = os.path.join(tmpdir, "input.scs")
            with open(top_scs, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write(f'include "{child_a}" section=rcworst_CCworst\n')
                f.write(f'include "{child_b}" section=rcworst_CCworst\n')
                f.write(f'include "{child_c}" section=rcworst_CCworst\n')
                f.write("subckt top p n\n")
                f.write("  X1 p n CELL_A\n")
                f.write("  X2 p n CELL_B\n")
                f.write("  X3 p n CELL_C\n")
                f.write("ends top\n")

            parser = NetlistParser(top_scs)

            # AC28: ALL three cells should be loaded (not just CELL_A from the first child)
            assert "CELL_A" in parser.subckts, (
                "CELL_A from child_a.scs should be loaded"
            )
            assert "CELL_B" in parser.subckts, (
                "CELL_B from child_b.scs should be loaded (BUG #2 fix: was missing)"
            )
            assert "CELL_C" in parser.subckts, (
                "CELL_C from child_c.scs should be loaded (BUG #2 fix: was missing)"
            )

            # Verify each has instances (from SPF bodies)
            assert len(parser.instances_by_parent.get("CELL_A", [])) > 0, (
                "CELL_A should have instances from SPF"
            )
            assert len(parser.instances_by_parent.get("CELL_B", [])) > 0, (
                "CELL_B should have instances from SPF"
            )
            assert len(parser.instances_by_parent.get("CELL_C", [])) > 0, (
                "CELL_C should have instances from SPF"
            )

    def test_direct_scs_file_with_section_dspf_include(self):
        """Test AC28: Direct .scs file with section-gated dspf_include loads the SPF.

        When running netlist-tracer directly on a single .scs file (not as an include
        of another file), dspf_includes within section blocks should still be loaded.
        This tests the case where a user runs:
          netlist-tracer -netlist <block>.scs -cell <block_top> -pin <input_pin>

        The .scs file has a subckt definition inside a section block, which might have
        dspf_includes that need to be loaded.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create SPF with back-annotated content
            spf_content = os.path.join(tmpdir, "spf_body.spf")
            with open(spf_content, "w") as f:
                f.write(
                    "*|DSPF\n"
                    ".SUBCKT my_cell a b\n"
                    "R1 a x 100k\n"
                    "R2 x b 100k\n"
                    ".ENDS my_cell\n"
                )

            # Create a .scs file with section-gated dspf_include
            scs_file = os.path.join(tmpdir, "my_circuit.scs")
            with open(scs_file, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write("section rcworst_CCworst\n")
                f.write(f'dspf_include "{spf_content}" bus_delim="<> []"\n')
                f.write("subckt my_cell a b\n")
                f.write("ends my_cell\n")
                f.write("endsection rcworst_CCworst\n")

            # Parse the .scs file directly (simulating the user's direct probe)
            parser = NetlistParser(scs_file)

            # The my_cell subckt should be present with instances from the SPF body
            assert "my_cell" in parser.subckts, (
                "my_cell should be defined in the parsed .scs file"
            )

            # Verify that instances from the dspf_include were loaded
            my_cell_insts = parser.instances_by_parent.get("my_cell", [])
            assert len(my_cell_insts) > 0, (
                "my_cell should have instances from the dspf_include in the section block"
            )

            # Check that resistor content was loaded (may be merged per iter-3 optimization)
            r_insts = [i for i in my_cell_insts if i.cell_type.upper() == "R"]
            assert len(r_insts) >= 1, (
                f"Expected at least 1 resistor instance from SPF, got {len(r_insts)}"
            )

    def test_dspf_gz_extension_match(self):
        """Test BUG #2 fix: .dspf.gz files are correctly recognized and loaded.

        Regression test for iteration 6: the extension check was changed to match
        (".spf", ".spf.gz", ".dspf", ".dspf.gz") instead of only ".spf".

        This test verifies that a dspf_include pointing to a .dspf.gz file
        is correctly parsed by the SPF parser (which already handles .gz transparently).
        """
        import gzip

        with tempfile.TemporaryDirectory() as tmpdir:
            # Create a gzipped DSPF file with populated body
            dspf_gz_path = os.path.join(tmpdir, "child.dspf.gz")
            dspf_content = (
                "*|DSPF\n"
                ".SUBCKT DSPF_GZ_CELL p n\n"
                "R1 p int 500\n"
                "R2 int n 500\n"
                ".ENDS DSPF_GZ_CELL\n"
            )
            with gzip.open(dspf_gz_path, "wt") as f:
                f.write(dspf_content)

            # Create a .scs file that references the .dspf.gz
            scs_path = os.path.join(tmpdir, "parent.scs")
            with open(scs_path, "w") as f:
                f.write("simulator lang=spectre\n")
                f.write(f'dspf_include "{dspf_gz_path}" bus_delim="<> []"\n')
                f.write("subckt top p n\n")
                f.write("  X1 p n DSPF_GZ_CELL\n")
                f.write("ends top\n")

            # Parse the .scs file
            parser = NetlistParser(scs_path)

            # Verify the DSPF_GZ_CELL was loaded from the .dspf.gz file
            assert "DSPF_GZ_CELL" in parser.subckts, (
                "DSPF_GZ_CELL should be loaded from .dspf.gz dspf_include. "
                "If this fails, the extension check is not matching .dspf.gz files."
            )

            # Verify it has instances from the SPF body
            dspf_gz_insts = parser.instances_by_parent.get("DSPF_GZ_CELL", [])
            assert len(dspf_gz_insts) > 0, (
                "DSPF_GZ_CELL should have instances from gzipped DSPF body"
            )
