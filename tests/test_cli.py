"""Tests for the netlist-tracer CLI."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from netlist_tracer import __version__


def test_cli_single_pin_byte_identical() -> None:
    """Verify single-pin CLI output is byte-identical to baseline."""
    repo_root = Path(__file__).parent.parent
    netlist_path = repo_root / "tests/fixtures/vendored/picorv32.v"
    baseline_path = repo_root / "tests/fixtures/golden/cli_picorv32_clk_baseline.txt"

    # Run the CLI command
    result = subprocess.run(
        [
            "netlist-tracer",
            "-netlist",
            str(netlist_path),
            "-cell",
            "picorv32",
            "-pin",
            "clk",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"

    # Read baseline
    with open(baseline_path) as f:
        baseline = f.read()

    # Compare byte-for-byte
    assert result.stdout == baseline, "Single-pin CLI output differs from baseline"


def test_cli_multipin_sectioned() -> None:
    """Verify multi-pin CLI output is sectioned correctly."""
    repo_root = Path(__file__).parent.parent
    netlist_path = repo_root / "tests/fixtures/vendored/picorv32.v"

    # Run the CLI with multiple pins
    result = subprocess.run(
        [
            "netlist-tracer",
            "-netlist",
            str(netlist_path),
            "-cell",
            "picorv32",
            "-pin",
            "clk",
            "-pin",
            "resetn",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    output = result.stdout

    # Check for sectioning headers
    assert "== Pin: clk" in output, "Missing section header for 'clk'"
    assert "== Pin: resetn" in output, "Missing section header for 'resetn'"
    assert "Tracing: picorv32.<2 pins>" in output, "Missing multi-pin tracing header"


def test_cli_trace_format_json() -> None:
    """Verify -output *.json produces valid JSON output."""
    import os
    import tempfile

    repo_root = Path(__file__).parent.parent
    netlist_path = repo_root / "tests/fixtures/vendored/picorv32.v"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        # Run the CLI with JSON format via -output file
        result = subprocess.run(
            [
                "netlist-tracer",
                "-netlist",
                str(netlist_path),
                "-cell",
                "picorv32",
                "-output",
                tmp_path,
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"CLI failed: {result.stderr}"

        # Read JSON from output file
        with open(tmp_path) as f:
            data = json.load(f)

        # Verify schema keys
        assert data["tool"] == "netlist-tracer", "Tool field is incorrect"
        assert data["version"] == __version__, "Version field must match netlist_tracer.__version__"
        assert data["cell"] == "picorv32", "Cell field is incorrect"
        assert data["target"] is None, "Target field should be None"
        assert isinstance(data["pins"], dict), "Pins should be a dict"

        # In omit-mode, pins dict should be non-empty (all bit-level pins)
        assert len(data["pins"]) > 0, "No pins traced in omit-mode"

        # Check structure of first pin
        first_pin = next(iter(data["pins"].values()))
        assert isinstance(first_pin["paths"], list), "Paths should be a list"
        if first_pin["paths"]:
            first_path = first_pin["paths"][0]
            assert "formatted" in first_path, "Missing 'formatted' in path"
            assert "steps" in first_path, "Missing 'steps' in path"
            if first_path["steps"]:
                first_step = first_path["steps"][0]
                assert "cell" in first_step, "Missing 'cell' in step"
                assert "pin_or_net" in first_step, "Missing 'pin_or_net' in step"
                assert "direction" in first_step, "Missing 'direction' in step"
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_cli_auto_detect_edif() -> None:
    """Verify -netlist with EDIF file auto-detects format without -format flag."""
    import tempfile

    repo_root = Path(__file__).parent.parent
    netlist_path = repo_root / "tests/fixtures/vendored/AND_gate.edf"

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [
                "netlist-parser",
                "-netlist",
                str(netlist_path),
                "-output",
                str(tmp_path),
            ],
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, f"CLI failed: {result.stderr}"
        with open(tmp_path) as f:
            data = json.load(f)
        assert data["format"] == "edif", "EDIF format should be auto-detected"
    finally:
        import os

        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def test_cli_edif_bad_cell_suggestion() -> None:
    """Verify CLI provides suggestions when EDIF cell not found."""
    repo_root = Path(__file__).parent.parent
    netlist_path = repo_root / "tests/fixtures/vendored/AND_gate.edf"

    result = subprocess.run(
        [
            "netlist-tracer",
            "-netlist",
            str(netlist_path),
            "-cell",
            "logic_gat",  # Near-miss to trigger fuzzy suggestion
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "Should fail for nonexistent cell"
    assert "Did you mean" in result.stderr, "Should provide fuzzy suggestion"
    assert "logic_gate" in result.stderr, "Suggestion should include correct cell name"


def test_cli_edif_bad_pin_suggestion() -> None:
    """Verify CLI provides suggestions when EDIF pin not found."""
    repo_root = Path(__file__).parent.parent
    netlist_path = repo_root / "tests/fixtures/vendored/AND_gate.edf"

    result = subprocess.run(
        [
            "netlist-tracer",
            "-netlist",
            str(netlist_path),
            "-cell",
            "logic_gate",
            "-pin",
            "qx",  # Near-miss to 'q' to trigger fuzzy suggestion
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0, "Should fail for nonexistent pin"
    # Pin suggestions are printed to stdout, not stderr
    assert "Did you mean" in (result.stderr + result.stdout), "Should provide fuzzy suggestion"


def test_cli_edif_extension_edn() -> None:
    """Verify CLI auto-detects .edn EDIF extension."""
    import tempfile

    repo_root = Path(__file__).parent.parent
    # Read AND_gate.edf content
    edif_path = repo_root / "tests/fixtures/vendored/AND_gate.edf"
    with open(edif_path) as f:
        edif_content = f.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a temp .edn file with EDIF content
        edn_path = Path(tmpdir) / "test_design.edn"
        edn_path.write_text(edif_content)

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                [
                    "netlist-parser",
                    "-netlist",
                    str(edn_path),
                    "-output",
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0, f"CLI failed: {result.stderr}"
            with open(tmp_path) as f:
                data = json.load(f)
            assert data["format"] == "edif", ".edn extension should be auto-detected as EDIF"
        finally:
            import os

            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def test_cli_include_flag() -> None:
    """Verify -include flag resolves include files from specified directory."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create child.sp
        child_file = os.path.join(tmpdir, "child.sp")
        with open(child_file, "w") as f:
            f.write(".subckt CHILD a b\n")
            f.write("R1 a b res=1k\n")
            f.write(".ends CHILD\n")

        # Create parent.sp in repo (not in tmpdir)
        repo_root = Path(__file__).parent.parent
        parent_file = repo_root / "tests" / "fixtures" / "synthetic" / "temp_parent.sp"
        with open(parent_file, "w") as f:
            f.write(".include 'child.sp'\n")
            f.write(".subckt PARENT a b\n")
            f.write("X1 a b CHILD\n")
            f.write(".ends PARENT\n")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name

        try:
            result = subprocess.run(
                [
                    "netlist-parser",
                    "-netlist",
                    str(parent_file),
                    "-output",
                    str(tmp_path),
                    "-include",
                    str(tmpdir),
                ],
                capture_output=True,
                text=True,
            )

            assert result.returncode == 0, f"CLI failed: {result.stderr}"
            with open(tmp_path) as f:
                data = json.load(f)
            assert "PARENT" in data["subckts"], "PARENT should be parsed"
            assert "CHILD" in data["subckts"], "CHILD should be resolved via include_path"
        finally:
            if parent_file.exists():
                parent_file.unlink()
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)


def test_cli_include_flag_repeated() -> None:
    """Verify -include flag can be repeated for multiple search directories."""
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir1:
        with tempfile.TemporaryDirectory() as tmpdir2:
            # Create child1.sp in tmpdir1
            child1_file = os.path.join(tmpdir1, "child1.sp")
            with open(child1_file, "w") as f:
                f.write(".subckt CHILD1 a b\n")
                f.write("R1 a b res=1k\n")
                f.write(".ends CHILD1\n")

            # Create child2.sp in tmpdir2
            child2_file = os.path.join(tmpdir2, "child2.sp")
            with open(child2_file, "w") as f:
                f.write(".subckt CHILD2 a b\n")
                f.write("R1 a b res=2k\n")
                f.write(".ends CHILD2\n")

            # Create parent.sp
            repo_root = Path(__file__).parent.parent
            parent_file = repo_root / "tests" / "fixtures" / "synthetic" / "temp_parent2.sp"
            with open(parent_file, "w") as f:
                f.write(".include 'child1.sp'\n")
                f.write(".include 'child2.sp'\n")
                f.write(".subckt PARENT a b c d\n")
                f.write("X1 a b CHILD1\n")
                f.write("X2 c d CHILD2\n")
                f.write(".ends PARENT\n")

            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
                tmp_path = tmp.name

            try:
                result = subprocess.run(
                    [
                        "netlist-parser",
                        "-netlist",
                        str(parent_file),
                        "-output",
                        str(tmp_path),
                        "-include",
                        str(tmpdir1),
                        "-include",
                        str(tmpdir2),
                    ],
                    capture_output=True,
                    text=True,
                )

                assert result.returncode == 0, f"CLI failed: {result.stderr}"
                with open(tmp_path) as f:
                    data = json.load(f)
                assert "PARENT" in data["subckts"], "PARENT should be parsed"
                assert "CHILD1" in data["subckts"], "CHILD1 should be resolved from tmpdir1"
                assert "CHILD2" in data["subckts"], "CHILD2 should be resolved from tmpdir2"
            finally:
                if parent_file.exists():
                    parent_file.unlink()
                if os.path.exists(tmp_path):
                    os.unlink(tmp_path)


def test_cli_trace_parse_error_returns_1() -> None:
    """netlist-tracer must exit 1 when parse fails on an unresolvable .include (v0.3.1).

    Regression for coordinator's live-validation observation that parse
    errors must propagate to a non-zero CLI exit code. Uses `.include`
    (strict semantics — raises) rather than `.lib` (try-and-degrade —
    warns and continues).
    """
    import os as _os
    import tempfile as _tf

    with _tf.TemporaryDirectory() as tmpdir:
        parent_file = _os.path.join(tmpdir, "parent.sp")
        missing_path = _os.path.join(tmpdir, "definitely_missing.sp")
        with open(parent_file, "w") as f:
            f.write(f".include '{missing_path}'\n")
            f.write(".subckt TOP a b c\n")
            f.write("R1 a b 1k\n")
            f.write(".ends TOP\n")

        result = subprocess.run(
            ["netlist-tracer", "-netlist", parent_file, "-cell", "TOP"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            f"Expected exit 1 on parse error; got {result.returncode}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        # Some 'ERROR' diagnostic should appear in stdout or stderr.
        combined = (result.stdout + result.stderr).lower()
        assert "error" in combined, (
            f"Expected ERROR diagnostic in output; got stdout={result.stdout!r} "
            f"stderr={result.stderr!r}"
        )


def test_cli_parse_parse_error_returns_1() -> None:
    """netlist-parser must exit 1 when parse fails on an unresolvable .include (v0.3.1)."""
    import os as _os
    import tempfile as _tf

    with _tf.TemporaryDirectory() as tmpdir:
        parent_file = _os.path.join(tmpdir, "parent.sp")
        missing_path = _os.path.join(tmpdir, "definitely_missing.sp")
        with open(parent_file, "w") as f:
            f.write(f".include '{missing_path}'\n")
            f.write(".subckt TOP a b c\n")
            f.write("R1 a b 1k\n")
            f.write(".ends TOP\n")
        with _tf.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tmp:
            out_path = tmp.name
        try:
            result = subprocess.run(
                ["netlist-parser", "-netlist", parent_file, "-output", out_path],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 1, (
                f"Expected exit 1 on parse error; got {result.returncode}. "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
        finally:
            if _os.path.exists(out_path):
                _os.unlink(out_path)


def test_cli_lib_unresolvable_returns_0_with_warning() -> None:
    """Try-and-degrade WARN+skip on bare .lib path -> CLI must exit 0 (v0.3.1).

    Confirms the inverse of the hard-error tests: deliverable H's WARN+skip
    behavior is a successful parse with a warning, NOT a parse failure.
    The CLI should produce a usable (if reduced) trace and exit 0.
    """
    import os as _os
    import tempfile as _tf

    with _tf.TemporaryDirectory() as tmpdir:
        parent_file = _os.path.join(tmpdir, "parent.sp")
        with open(parent_file, "w") as f:
            # Bare-form .lib with unresolvable path triggers H's WARN+skip
            f.write(".lib tt_allDevices_post\n")
            f.write(".subckt TOP a b c\n")
            f.write("R1 a b 1k\n")
            f.write(".ends TOP\n")

        result = subprocess.run(
            ["netlist-tracer", "-netlist", parent_file, "-cell", "TOP"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Expected exit 0 (WARN+skip is a success); got {result.returncode}. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )


def test_cli_peek_invalid_pin_early_exit(synthetic_spice_basic_sp: str) -> None:
    """Test CLI exits early with error when invalid pin provided during peek."""
    # Call CLI with a valid cell and an invalid pin
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "netlist_tracer.cli.trace",
            "-netlist",
            synthetic_spice_basic_sp,
            "-cell",
            "nand2",
            "-pin",
            "INVALID_PIN",
        ],
        capture_output=True,
        text=True,
    )
    # Should exit with code 1 (error)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    # stderr should contain error message about pin not found
    assert "Pin" in result.stderr or "pin" in result.stderr.lower()
    assert "INVALID_PIN" in result.stderr or "not found" in result.stderr


def test_cli_peek_valid_pin_no_regression(synthetic_spice_basic_sp: str) -> None:
    """Test CLI with valid pin works same as before (no regression)."""
    # Call CLI with valid cell and valid pin
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "netlist_tracer.cli.trace",
            "-netlist",
            synthetic_spice_basic_sp,
            "-cell",
            "nand2",
            "-pin",
            "Y",
        ],
        capture_output=True,
        text=True,
    )
    # Should succeed
    assert result.returncode == 0, (
        f"Expected exit 0 with valid pin; got {result.returncode}. stderr={result.stderr!r}"
    )


def test_cli_net_rejects_pin(synthetic_spice_basic_sp: str) -> None:
    """Test that -net and -pin cannot be combined."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "netlist_tracer.cli.trace",
            "-netlist",
            synthetic_spice_basic_sp,
            "-net",
            "sig_a",
            "-pin",
            "Y",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, "Should fail when both -net and -pin are supplied"
    assert "cannot combine" in result.stderr.lower(), "Error message should mention the conflict"


def test_cli_net_mode_finds_paths(synthetic_spice_basic_sp: str) -> None:
    """Test that -net mode traces from a named net and finds paths."""
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "netlist_tracer.cli.trace",
            "-netlist",
            synthetic_spice_basic_sp,
            "-net",
            "sig_a",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"CLI failed: {result.stderr}"
    # Verify net-mode output contains net-related text
    assert "net" in result.stdout.lower(), \
        "Output should contain reference to traced net"
