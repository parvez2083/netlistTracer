#!/usr/bin/env python3
"""Capture pre-refactor parser+tracer baselines for regression testing.

Run this script BEFORE the netlistTracer professionalization refactor
(PHASE 1 of the engagement). It exercises the EXISTING top-level
`netlist_parser.py` and `netlist_tracer.py` modules at the repo root and
serializes a deterministic snapshot of:

  - Parser-level statistics for each fixture (format, subckt count,
    instance count, sorted top-cell pin list, per-subckt pin counts +
    instance counts).
  - One representative trace from a deterministic (cell, pin) start point
    per fixture, captured via `format_path()` so the output string itself
    is the regression oracle.

The two output JSON files in `tests/fixtures/golden/` form the
behavior-preservation gate (acceptance criterion AC9): post-refactor code
must reproduce them byte-for-byte.

Usage:
    cd <repo-root>
    python3 tests/_capture_baseline.py

Exits 0 on success, non-zero on parse/trace failure (which is itself a
finding worth reporting back to the user).

This script intentionally has zero dependencies on the to-be-built
`netlist_tracer` package -- it imports the legacy top-level modules only.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

# Anchor the import path at the repo root regardless of cwd.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from netlist_tracer import BidirectionalTracer, NetlistParser, format_path  # noqa: E402

FIXTURES_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "vendored")
GOLDEN_DIR = os.path.join(REPO_ROOT, "tests", "fixtures", "golden")


# --- parser snapshot ------------------------------------------------------


def parser_snapshot(parser: NetlistParser) -> dict[str, Any]:
    """Serialize a deterministic, comparable view of parser state."""
    subckts_view: dict[str, dict[str, Any]] = {}
    for name in sorted(parser.subckts.keys()):
        sub = parser.subckts[name]
        subckts_view[name] = {
            "pins": list(sub.pins),  # preserve order (positional)
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


# --- trace snapshot -------------------------------------------------------


def trace_snapshot(
    tracer: BidirectionalTracer, start_cell: str, start_pin: str, max_depth: int | None = None
) -> dict[str, Any]:
    """Run a trace and serialize the deduplicated formatted-path output.

    The format_path() string is the canonical regression artifact: it
    encodes hierarchy, alias resolution, and peak detection in a single
    deterministic line per path.
    """
    paths = tracer.trace(start_cell, start_pin, max_depth=max_depth)
    seen = set()
    unique_formatted: list[str] = []
    for path in paths:
        sig = format_path(path)
        if sig in seen:
            continue
        seen.add(sig)
        unique_formatted.append(sig)
    return {
        "start_cell": start_cell,
        "start_pin": start_pin,
        "max_depth": max_depth,
        "raw_path_count": len(paths),
        "unique_path_count": len(unique_formatted),
        "paths": unique_formatted,
    }


# --- per-fixture drivers --------------------------------------------------


def capture_picorv32() -> dict[str, Any]:
    src = os.path.join(FIXTURES_DIR, "picorv32.v")
    parser = NetlistParser(src)
    tracer = BidirectionalTracer(parser)
    # Trace start: top module 'picorv32', clock pin 'clk'. Both are
    # universal in any picorv32 build and always present at the top-level
    # port list -- a maximally stable choice for a regression oracle.
    return {
        "fixture": "picorv32.v",
        "source_url": "https://raw.githubusercontent.com/cliffordwolf/picorv32/main/picorv32.v",
        "vendored_sha": "87c89acc18994c8cf9a2311e871818e87d304568",
        "parser": parser_snapshot(parser),
        "trace": trace_snapshot(tracer, start_cell="picorv32", start_pin="clk"),
    }


def capture_sky130_inv() -> dict[str, Any]:
    src = os.path.join(FIXTURES_DIR, "sky130_fd_sc_hd__inv_1.spice")
    parser = NetlistParser(src)
    tracer = BidirectionalTracer(parser)
    # Trace start: subckt 'sky130_fd_sc_hd__inv_1', input pin 'A'.
    # The cell has two MOSFET instances (X0 nfet, X1 pfet); tracing 'A'
    # exercises the SPICE instance-pin resolution path on a tiny but
    # representative standard-cell netlist.
    return {
        "fixture": "sky130_fd_sc_hd__inv_1.spice",
        "source_url": "https://raw.githubusercontent.com/google/skywater-pdk-libs-sky130_fd_sc_hd/main/cells/inv/sky130_fd_sc_hd__inv_1.spice",
        "vendored_sha": "ac7fb61f06e6470b94e8afdf7c25268f62fbd7b1",
        "parser": parser_snapshot(parser),
        "trace": trace_snapshot(tracer, start_cell="sky130_fd_sc_hd__inv_1", start_pin="A"),
    }


def capture_AND_gate() -> dict[str, Any]:
    src = os.path.join(FIXTURES_DIR, "AND_gate.edf")
    parser = NetlistParser(src)
    tracer = BidirectionalTracer(parser)
    # Trace start: top cell 'logic_gate', input pin 'a'.
    return {
        "fixture": "AND_gate.edf",
        "source_url": "https://github.com/byuccl/spydrnet",
        "vendored_sha": "2e7b78a5d04b2d77c630c4c75c97be12095a13fc",
        "parser": parser_snapshot(parser),
        "trace": trace_snapshot(tracer, start_cell="logic_gate", start_pin="a"),
    }


def capture_n_bit_counter() -> dict[str, Any]:
    src = os.path.join(FIXTURES_DIR, "n_bit_counter.edf")
    parser = NetlistParser(src)
    tracer = BidirectionalTracer(parser)
    # Trace start: top cell 'n_bit_counter', clock pin 'clk'.
    return {
        "fixture": "n_bit_counter.edf",
        "source_url": "https://github.com/byuccl/spydrnet",
        "vendored_sha": "2e7b78a5d04b2d77c630c4c75c97be12095a13fc",
        "parser": parser_snapshot(parser),
        "trace": trace_snapshot(tracer, start_cell="n_bit_counter", start_pin="clk"),
    }


def capture_one_counter() -> dict[str, Any]:
    src = os.path.join(FIXTURES_DIR, "one_counter.edf")
    parser = NetlistParser(src)
    tracer = BidirectionalTracer(parser)
    # Trace start: top cell 'one_counter', clock pin 'XP_CLK_A'.
    return {
        "fixture": "one_counter.edf",
        "source_url": "https://github.com/byuccl/spydrnet",
        "vendored_sha": "2e7b78a5d04b2d77c630c4c75c97be12095a13fc",
        "parser": parser_snapshot(parser),
        "trace": trace_snapshot(tracer, start_cell="one_counter", start_pin="XP_CLK_A"),
    }


# --- driver ---------------------------------------------------------------


def write_baseline(name: str, payload: dict[str, Any]) -> str:
    out_path = os.path.join(GOLDEN_DIR, f"{name}_baseline.json")
    os.makedirs(GOLDEN_DIR, exist_ok=True)
    with open(out_path, "w") as f:
        # sort_keys=True, fixed indent, trailing newline -> deterministic.
        json.dump(payload, f, indent=2, sort_keys=True)
        f.write("\n")
    return out_path


def main() -> int:
    print(f"Repo root: {REPO_ROOT}")
    print(f"Fixtures:  {FIXTURES_DIR}")
    print(f"Golden:    {GOLDEN_DIR}\n")

    rc = 0
    for name, capturer in [
        ("picorv32", capture_picorv32),
        ("sky130_fd_sc_hd__inv_1", capture_sky130_inv),
        ("AND_gate", capture_AND_gate),
        ("n_bit_counter", capture_n_bit_counter),
        ("one_counter", capture_one_counter),
    ]:
        print(f"=== Capturing baseline: {name} ===")
        try:
            payload = capturer()
        except Exception as e:
            print(f"  FAILED: {type(e).__name__}: {e}")
            rc = 1
            continue
        out = write_baseline(name, payload)
        p = payload["parser"]
        t = payload["trace"]
        print(
            f"  parser: format={p['format']} subckts={p['subckt_count']} "
            f"instances={p['total_instance_count']}"
        )
        print(
            f"  trace:  start={t['start_cell']}.{t['start_pin']} "
            f"raw={t['raw_path_count']} unique={t['unique_path_count']}"
        )
        print(f"  wrote:  {out}\n")
    return rc


if __name__ == "__main__":
    sys.exit(main())
