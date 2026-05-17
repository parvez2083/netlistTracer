#!/usr/bin/env python3
"""Tests for lazy SPF parsing functionality."""

import gzip
import logging
import os
import tempfile

from netlist_tracer.parser import NetlistParser
from netlist_tracer.tracer import BidirectionalTracer


def test_peek_spf_subckts_multi_cell():
    """Test peek_spf_subckts on synthetic SPF with three .SUBCKT declarations."""
    from netlist_tracer.parsers.peek import peek_spf_subckts

    with tempfile.NamedTemporaryFile(mode='w', suffix='.spf', delete=False) as f:
        f.write(""".SUBCKT cell_a a b c
.ENDS

.SUBCKT cell_b x y
.ENDS

.SUBCKT cell_c p q r s
.ENDS
""")
        f.flush()
        try:
            rslt = peek_spf_subckts(f.name)
            assert len(rslt) == 3
            assert ('cell_a', ['a', 'b', 'c']) in rslt
            assert ('cell_b', ['x', 'y']) in rslt
            assert ('cell_c', ['p', 'q', 'r', 's']) in rslt
        finally:
            os.unlink(f.name)


def test_register_spf_placeholders():
    """Test that SPF cells are registered as lazy placeholders (not eagerly materialized)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create SPF file
        spf_file = os.path.join(tmpdir, 'test.spf')
        with open(spf_file, 'w') as f:
            f.write('.SUBCKT cell_from_spf p1 p2\n.ENDS\n')

        # Create synthetic Spectre file that references the SPF
        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f'subckt top a b\nends\ndspf_include "{spf_file}"\n')

        # Parse Spectre (registers placeholders, does NOT eagerly materialize)
        parser = NetlistParser(spec_file)

        # Check that cell was registered from SPF as a placeholder
        assert 'cell_from_spf' in parser.subckts
        subckt = parser.subckts['cell_from_spf']
        assert subckt.pins == ['p1', 'p2']
        assert subckt.is_placeholder is True
        assert subckt.placeholder_source == spf_file

        # Lazy: cell is pending, not yet materialized
        assert 'cell_from_spf' in parser.pndg_spf_fls
        assert parser.pndg_spf_fls['cell_from_spf'] == spf_file
        assert spf_file not in parser.mtrl_spf_fls


def test_materialize_spf_idempotent():
    """Test that materialize_spf is idempotent (no double-parse)."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spf_file = os.path.join(tmpdir, 'test.spf')
        with open(spf_file, 'w') as f:
            f.write('.SUBCKT cell_x p1 p2\nX1 p1 p2 dummy\n.ENDS\n')

        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f'subckt top a b\nends\ndspf_include "{spf_file}"\n')

        parser = NetlistParser(spec_file)

        # SPF is initially pending (not yet materialized)
        assert spf_file not in parser.mtrl_spf_fls
        assert 'cell_x' in parser.pndg_spf_fls

        # First materialization
        cnt = parser.mtrl_spf(spf_file)
        assert cnt > 0  # Was pending, now materialized
        assert spf_file in parser.mtrl_spf_fls

        # Second materialization is idempotent
        cnt = parser.mtrl_spf(spf_file)
        assert cnt == 0  # Already materialized, so returns 0


def test_lazy_trace_selective_materialization():
    """Test that lazy mode defers materialization: only materialized files when traced/accessed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create small SPF with ~5 lines
        spf_tiny = os.path.join(tmpdir, 'tiny.spf')
        with open(spf_tiny, 'w') as f:
            f.write('.SUBCKT cell_tiny p1 p2\nR1 p1 p2 1k\n.ENDS\n')

        # Create huge SPF with 100+ R lines (would OOM if eagerly parsed)
        spf_huge1 = os.path.join(tmpdir, 'huge1.spf')
        with open(spf_huge1, 'w') as f:
            f.write('.SUBCKT cell_huge1 p1 p2\n')
            for i in range(150):
                f.write(f'R{i} n{i} n{i+1} 1 m=100\n')
            f.write('.ENDS\n')

        # Create another huge SPF
        spf_huge2 = os.path.join(tmpdir, 'huge2.spf')
        with open(spf_huge2, 'w') as f:
            f.write('.SUBCKT cell_huge2 p1 p2\n')
            for i in range(150):
                f.write(f'R{i} n{i} n{i+1} 1 m=100\n')
            f.write('.ENDS\n')

        # Create Spectre referencing all three
        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f"""\
subckt top in out
X1 in m1 cell_tiny
X2 m1 out cell_huge1
ends
dspf_include "{spf_tiny}"
dspf_include "{spf_huge1}"
dspf_include "{spf_huge2}"
""")

        # Parse with lazy mode: placeholders registered, NONE materialized yet
        parser = NetlistParser(spec_file)

        # All three cells should be in subckts (as placeholders)
        assert 'cell_tiny' in parser.subckts
        assert 'cell_huge1' in parser.subckts
        assert 'cell_huge2' in parser.subckts

        # All should be pending (not yet materialized)
        assert len(parser.pndg_spf_fls) == 3, f"Expected 3 pending, got {len(parser.pndg_spf_fls)}"
        assert len(parser.mtrl_spf_fls) == 0, f"Expected 0 materialized, got {len(parser.mtrl_spf_fls)}"

        # All should have is_placeholder=True
        assert parser.subckts['cell_tiny'].is_placeholder is True
        assert parser.subckts['cell_huge1'].is_placeholder is True
        assert parser.subckts['cell_huge2'].is_placeholder is True

        # Now materialize only the tiny one (simulating selective trace)
        cnt = parser.mtrl_spf(spf_tiny)
        assert cnt > 0, "Expected materialization to happen"

        # After materializing tiny: 1 materialized, 2 pending
        assert len(parser.mtrl_spf_fls) == 1, f"Expected 1 materialized, got {len(parser.mtrl_spf_fls)}"
        assert len(parser.pndg_spf_fls) == 2, f"Expected 2 pending, got {len(parser.pndg_spf_fls)}"

        # The materialized cell should no longer be a placeholder
        assert parser.subckts['cell_tiny'].is_placeholder is False


def test_lazy_dump_json_eager_flush():
    """Test that dump_json materializes pending SPFs before serialization."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spf_file = os.path.join(tmpdir, 'test.spf')
        with open(spf_file, 'w') as f:
            f.write('.SUBCKT cell_y p1 p2\n.ENDS\n')

        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f'subckt top a b\nends\ndspf_include "{spf_file}"\n')

        parser = NetlistParser(spec_file)

        # Cell is registered as placeholder (not yet materialized)
        assert 'cell_y' in parser.subckts
        assert spf_file not in parser.mtrl_spf_fls
        assert 'cell_y' in parser.pndg_spf_fls
        assert parser.pndg_spf_fls['cell_y'] == spf_file

        # Dump JSON (should materialize pending SPFs as part of serialization)
        out_file = os.path.join(tmpdir, 'cache.json')
        parser.dump_json(out_file)

        # Verify cache contains the cell
        import json
        with open(out_file) as f:
            cache = json.load(f)
        assert 'cell_y' in cache['subckts']


def test_lazy_pin_validation_no_materialize():
    """Test that pin validation via peek on SPF succeeds without parsing the full netlist."""
    with tempfile.TemporaryDirectory() as tmpdir:
        spf_file = os.path.join(tmpdir, 'test.spf')
        with open(spf_file, 'w') as f:
            f.write('.SUBCKT cell_z p1 p2 p3\n.ENDS\n')

        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f'subckt top a b\nends\ndspf_include "{spf_file}"\n')

        # Peek at pins on SPF directly (cheap scan, no full parse)
        pins = NetlistParser.peek_pins(spf_file, 'cell_z')
        assert pins == ['p1', 'p2', 'p3']

        # Parse Spectre (registers as lazy placeholders)
        parser = NetlistParser(spec_file)
        assert 'cell_z' in parser.subckts
        assert 'cell_z' in parser.pndg_spf_fls  # Pending, not yet materialized
        assert parser.pndg_spf_fls['cell_z'] == spf_file
        assert spf_file not in parser.mtrl_spf_fls


def test_lazy_tracer_driven_selective_materialization():
    """Test that BFS walk via BidirectionalTracer triggers lazy materialization via _mtrl_if_pndg.

    This test verifies that the tracer's _mtrl_if_pndg callback is invoked
    during BFS walk and correctly materializes only the SPF files needed
    for the traced path, leaving untouched SPF files pending.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create three SPF files
        spf_a = os.path.join(tmpdir, 'cell_a.spf')
        with open(spf_a, 'w') as f:
            f.write('.SUBCKT cell_a p1 p2\nR1 p1 p2 1k\n.ENDS\n')

        spf_b = os.path.join(tmpdir, 'cell_b.spf')
        with open(spf_b, 'w') as f:
            f.write('.SUBCKT cell_b p1 p2\nR1 p1 p2 2k\n.ENDS\n')

        spf_c = os.path.join(tmpdir, 'cell_c.spf')
        with open(spf_c, 'w') as f:
            f.write('.SUBCKT cell_c p1 p2\nR1 p1 p2 3k\n.ENDS\n')

        # Create Spectre with empty shells for all cells and dspf_include for SPFs
        # Only cell_a is instantiated in top; cell_b and cell_c are included but not used
        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f"""\
subckt cell_a (p1 p2)
ends

subckt cell_b (p1 p2)
ends

subckt cell_c (p1 p2)
ends

subckt top (in out)
    X1 (in out) cell_a
ends

dspf_include "{spf_a}"
dspf_include "{spf_b}"
dspf_include "{spf_c}"
""")

        # Parse Spectre with lazy mode: all three cells are placeholders
        parser = NetlistParser(spec_file)

        # Verify all three are pending before trace
        assert len(parser.pndg_spf_fls) == 3, f"Expected 3 pending, got {len(parser.pndg_spf_fls)}"
        assert len(parser.mtrl_spf_fls) == 0, f"Expected 0 materialized, got {len(parser.mtrl_spf_fls)}"
        assert 'cell_a' in parser.pndg_spf_fls
        assert 'cell_b' in parser.pndg_spf_fls
        assert 'cell_c' in parser.pndg_spf_fls

        # Trace from top/in: tracer will descend into cell_a (instantiated in top)
        # At line 533 of tracer.py, _mtrl_if_pndg(curr_cell='top') is called
        # At line 541, _mtrl_if_pndg(inst.cell_type='cell_a') is called, materializing spf_a
        tracer = BidirectionalTracer(parser)
        tracer.trace('top', 'in')

        # After trace: at least spf_a should be materialized (touched by BFS walk)
        assert len(parser.mtrl_spf_fls) >= 1, f"Expected >= 1 materialized, got {len(parser.mtrl_spf_fls)}"
        assert spf_a in parser.mtrl_spf_fls, f"Expected spf_a to be materialized, got {parser.mtrl_spf_fls}"

        # Verify cell_a is no longer a placeholder
        assert 'cell_a' not in parser.pndg_spf_fls, "Expected cell_a to be removed from pending"

        # cell_b and cell_c should still be pending (not reached by BFS)
        assert 'cell_b' in parser.pndg_spf_fls, "Expected cell_b to remain pending"
        assert 'cell_c' in parser.pndg_spf_fls, "Expected cell_c to remain pending"
        assert spf_b not in parser.mtrl_spf_fls, "Expected spf_b to NOT be materialized"
        assert spf_c not in parser.mtrl_spf_fls, "Expected spf_c to NOT be materialized"


def test_peek_spf_subckts_gz():
    """Test peek_spf_subckts on gzipped SPF files.

    Verifies that peek_spf_subckts can transparently decompress .spf.gz
    files and extract cell/pin information without full parsing.
    """
    from netlist_tracer.parsers.peek import peek_spf_subckts

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create plaintext SPF
        spf_plain = os.path.join(tmpdir, 'test.spf')
        with open(spf_plain, 'w') as f:
            f.write(""".SUBCKT cell_x a b c
.ENDS

.SUBCKT cell_y x y z w
.ENDS
""")

        # Gzip it
        spf_gz = os.path.join(tmpdir, 'test.spf.gz')
        with open(spf_plain, 'rb') as f_in:
            with gzip.open(spf_gz, 'wb') as f_out:
                f_out.writelines(f_in)

        # Peek at gzipped file
        rslt_plain = peek_spf_subckts(spf_plain)
        rslt_gz = peek_spf_subckts(spf_gz)

        # Both should yield identical results
        assert len(rslt_gz) == 2
        assert ('cell_x', ['a', 'b', 'c']) in rslt_gz
        assert ('cell_y', ['x', 'y', 'z', 'w']) in rslt_gz
        assert rslt_plain == rslt_gz


def test_lazy_trace_json_eager_flush():
    """Test that JSON serialization via dump_json flushes all pending SPF materialization.

    Verifies that after dump_json completes, all pending SPFs have been
    materialized and are no longer in pndg_spf_fls (all moved to mtrl_spf_fls).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        spf_file = os.path.join(tmpdir, 'test.spf')
        with open(spf_file, 'w') as f:
            f.write('.SUBCKT cell_json p1 p2\nR1 p1 p2 1k\n.ENDS\n')

        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f'subckt top a b\nends\ndspf_include "{spf_file}"\n')

        parser = NetlistParser(spec_file)

        # Before dump: SPF is pending
        assert 'cell_json' in parser.pndg_spf_fls
        assert spf_file not in parser.mtrl_spf_fls

        # Dump JSON (should materialize all pending SPFs)
        out_file = os.path.join(tmpdir, 'cache.json')
        parser.dump_json(out_file)

        # After dump: all pending SPFs should be materialized
        # Either pndg_spf_fls is empty, or if the cell wasn't in pndg_spf_fls
        # before dump, it's been moved to mtrl_spf_fls
        assert spf_file in parser.mtrl_spf_fls, "Expected SPF to be materialized after dump_json"
        assert 'cell_json' not in parser.pndg_spf_fls or parser.subckts['cell_json'].is_placeholder is False


def test_lazy_back_annotation_merge(caplog):
    """Test that back-annotation log fires at materialization time, not parse time.

    Verifies that when an empty Spectre shell is replaced by a populated SPF body,
    the back-annotation INFO log message fires at materialization time (during
    mtrl_spf or tracer walk), not at initial parse time.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create SPF with populated FOO cell
        spf_file = os.path.join(tmpdir, 'foo.spf')
        with open(spf_file, 'w') as f:
            f.write('.SUBCKT FOO a b\nR1 a b 1k\n.ENDS\n')

        # Create Spectre with empty FOO shell
        spec_file = os.path.join(tmpdir, 'test.scs')
        with open(spec_file, 'w') as f:
            f.write(f"""\
subckt FOO (a b)
ends

subckt top (in out)
    X1 (in out) FOO
ends

dspf_include "{spf_file}"
""")

        # Parse Spectre (registers FOO as placeholder, no back-annotation log yet)
        with caplog.at_level(logging.INFO):
            parser = NetlistParser(spec_file)

        # Capture logs during parse
        parse_logs = [record.message for record in caplog.records]
        back_annot_log = [msg for msg in parse_logs if "Back-annotating" in msg]

        # Back-annotation should NOT have fired during parse (FOO is a placeholder)
        assert len(back_annot_log) == 0, f"Unexpected back-annotation during parse: {back_annot_log}"

        # Now trigger materialization via tracer walk
        caplog.clear()
        with caplog.at_level(logging.INFO):
            tracer = BidirectionalTracer(parser)
            tracer.trace('top', 'in')

        # After trace: back-annotation log should appear (during _mtrl_if_pndg -> mtrl_spf)
        trace_logs = [record.message for record in caplog.records]
        back_annot_log = [msg for msg in trace_logs if "Back-annotating 'FOO'" in msg]

        assert len(back_annot_log) >= 1, (
            f"Expected back-annotation log at materialization time, got: {trace_logs}"
        )


def test_folder_gz_discovery():
    """Test that folder mode discovers .dspf.gz, .spf.gz files in addition to uncompressed."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create .dspf.gz file
        dspf_plain = os.path.join(tmpdir, 'parasitic.dspf')
        with open(dspf_plain, 'w') as f:
            f.write('.SUBCKT from_dspf_gz p1 p2\n.ENDS\n')

        dspf_gz = os.path.join(tmpdir, 'parasitic.dspf.gz')
        with open(dspf_plain, 'rb') as f_in:
            with gzip.open(dspf_gz, 'wb') as f_out:
                f_out.writelines(f_in)

        # Create .spice file (uncompressed)
        spice_file = os.path.join(tmpdir, 'circuit.spi')
        with open(spice_file, 'w') as f:
            f.write('.SUBCKT top a b\nX1 a b from_dspf_gz\n.ENDS\n')

        # Parse folder (should discover both .dspf.gz and .spi)
        parser = NetlistParser(tmpdir)

        # Verify both files were detected and parsed
        assert 'top' in parser.subckts, "Expected top cell from SPICE file"
        assert 'from_dspf_gz' in parser.subckts, "Expected DSPF cell from .dspf.gz file"
        assert len(parser.files) >= 2, f"Expected at least 2 files, got {parser.files}"


def test_folder_lazy_spf_registration():
    """Test that pure SPF folders apply lazy registration.

    Create a folder with ONLY SPF files (no SPICE); verify SPF cells are
    registered as placeholders (not eagerly materialized).
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create SPF file
        spf_file = os.path.join(tmpdir, 'parasitic.spf')
        with open(spf_file, 'w') as f:
            f.write('.SUBCKT lazy_cell p1 p2\nR1 p1 p2 1k\n.ENDS\n')

        # Parse folder (pure SPF: lazy mode applies)
        parser = NetlistParser(tmpdir)

        # Verify SPF cells are registered as placeholders
        assert 'lazy_cell' in parser.subckts
        assert 'lazy_cell' in parser.pndg_spf_fls, "Expected lazy_cell to be pending"
        assert spf_file not in parser.mtrl_spf_fls, "Expected SPF to NOT be materialized yet"
        assert parser.subckts['lazy_cell'].is_placeholder is True


def test_folder_lazy_selective_materialization():
    """Test that pure SPF folder mode selective materialization works correctly.

    Create a folder with ONLY 3 SPF files (no SPICE). Parse folder (all
    SPFs become placeholders). Manually materialize one SPF; verify only
    that one is materialized, others remain pending.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create three SPF files (pure SPF folder)
        spf1 = os.path.join(tmpdir, 'cell1.spf')
        with open(spf1, 'w') as f:
            f.write('.SUBCKT cell1 p1 p2\nR1 p1 p2 1k\n.ENDS\n')

        spf2 = os.path.join(tmpdir, 'cell2.spf')
        with open(spf2, 'w') as f:
            f.write('.SUBCKT cell2 p1 p2\nR1 p1 p2 2k\n.ENDS\n')

        spf3 = os.path.join(tmpdir, 'cell3.spf')
        with open(spf3, 'w') as f:
            f.write('.SUBCKT cell3 p1 p2\nR1 p1 p2 3k\n.ENDS\n')

        # Parse pure SPF folder (all SPFs lazy)
        parser = NetlistParser(tmpdir)

        # All three should be pending
        assert 'cell1' in parser.pndg_spf_fls
        assert 'cell2' in parser.pndg_spf_fls
        assert 'cell3' in parser.pndg_spf_fls
        assert len(parser.mtrl_spf_fls) == 0

        # Materialize only cell1
        cnt = parser.mtrl_spf(spf1)
        assert cnt > 0, "Expected cell1 to materialize"

        # Verify state: 1 materialized, 2 pending
        assert spf1 in parser.mtrl_spf_fls
        assert spf2 not in parser.mtrl_spf_fls
        assert spf3 not in parser.mtrl_spf_fls
        assert 'cell1' not in parser.pndg_spf_fls
        assert 'cell2' in parser.pndg_spf_fls
        assert 'cell3' in parser.pndg_spf_fls


def test_peek_spef_subckts():
    """Test peek_spf_subckts on SPEF format (auto-detect format).

    SPEF uses *DESIGN and *PORTS, not .SUBCKT; verify peek works for both.
    """
    from netlist_tracer.parsers.peek import peek_spf_subckts

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create SPEF file
        spef_file = os.path.join(tmpdir, 'test.spef')
        with open(spef_file, 'w') as f:
            f.write("""\
*DESIGN my_design
*PORTS
p1 p2 p3
*D_NET
""")

        # Peek at SPEF (should auto-detect format)
        rslt = peek_spf_subckts(spef_file)

        # Should return design name and port list
        assert len(rslt) == 1
        assert rslt[0][0] == 'my_design'
        assert 'p1' in rslt[0][1]
        assert 'p2' in rslt[0][1]
        assert 'p3' in rslt[0][1]


def test_folder_spef_lazy_registration():
    """Test that pure SPEF folders are also registered lazily."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create SPEF file (pure SPEF folder, no SPICE)
        spef_file = os.path.join(tmpdir, 'design.spef')
        with open(spef_file, 'w') as f:
            f.write("""\
*DESIGN my_design
*PORTS
pin_a pin_b
*D_NET
*END
""")

        # Parse pure SPEF folder (SPEF should be lazy)
        parser = NetlistParser(tmpdir)

        # Verify SPEF was detected and registered as lazy
        assert 'my_design' in parser.subckts
        assert 'my_design' in parser.pndg_spf_fls, "Expected SPEF design to be pending"
        assert spef_file not in parser.mtrl_spf_fls, "Expected SPEF to NOT be materialized"


def test_mtrl_spf_format_detection():
    """Test that mtrl_spf auto-detects SPEF format and dispatches correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create SPEF file with design definition
        spef_file = os.path.join(tmpdir, 'test.spef')
        with open(spef_file, 'w') as f:
            f.write("""\
*DESIGN test_design
*PORTS
p1 p2
*D_NET
*END
""")

        # Create a minimal spectre file to trigger lazy registration path
        scs_file = os.path.join(tmpdir, 'test.scs')
        with open(scs_file, 'w') as f:
            f.write('subckt top a b\nends\n')

        # Parse Spectre (no SPFs declared, but we'll manually register)
        parser = NetlistParser(scs_file)

        # Manually register the SPEF file as pending
        parser.pndg_spf_fls['test_design'] = spef_file

        # Materialize (should auto-detect SPEF format and use parse_spef)
        parser.mtrl_spf(spef_file)

        # Verify materialization succeeded and used SPEF parser
        assert spef_file in parser.mtrl_spf_fls, "Expected SPEF to be materialized"
        # design should be registered in subckts
        assert 'test_design' in parser.subckts


if __name__ == '__main__':
    test_peek_spf_subckts_multi_cell()
    test_register_spf_placeholders()
    test_materialize_spf_idempotent()
    test_lazy_dump_json_eager_flush()
    test_lazy_pin_validation_no_materialize()
    test_folder_gz_discovery()
    test_folder_lazy_spf_registration()
    test_folder_lazy_selective_materialization()
    test_peek_spef_subckts()
    test_folder_spef_lazy_registration()
    test_mtrl_spf_format_detection()
    print("All tests passed!")
