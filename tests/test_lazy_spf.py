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


if __name__ == '__main__':
    test_peek_spf_subckts_multi_cell()
    test_register_spf_placeholders()
    test_materialize_spf_idempotent()
    test_lazy_dump_json_eager_flush()
    test_lazy_pin_validation_no_materialize()
    print("All tests passed!")
