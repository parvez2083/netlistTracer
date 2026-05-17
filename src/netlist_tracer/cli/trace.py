from __future__ import annotations

import argparse
import difflib
import json
import logging
import os
import re
import sys
from typing import Any

from netlist_tracer import __version__
from netlist_tracer._logging import get_logger
from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.parser import NetlistParser
from netlist_tracer.parsers.spef import SpefOverlay, parse_spef
from netlist_tracer.tracer import BidirectionalTracer, TraceStep, format_path

_logger = get_logger(__name__)


def _format_step_for_json(step: TraceStep, spef_ovly: Any | None = None) -> dict:
    """Convert a TraceStep to JSON-serializable dict."""
    result = {
        "cell": step.cell,
        "pin_or_net": step.pin_or_net,
        "direction": step.direction,
        "instance_name": step.instance_name,
        "inst_stack": [list(item) for item in step.inst_stack],
    }

    # Append SPEF metadata if available
    if spef_ovly:
        ovl_dt = spef_ovly.lookup(step.pin_or_net)
        if ovl_dt:
            result["spef"] = {  # type: ignore
                "C": ovl_dt["C"],
                "R": ovl_dt["R"],
                "pins": ovl_dt["pins"],
            }

    return result


def main() -> int:
    """Trace signal paths through a netlist (CLI entry point).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Bidirectional Hierarchical Netlist Tracer")
    parser.add_argument(
        "-netlist", required=True, help="Path to netlist file or directory of .sv/.v files"
    )
    parser.add_argument(
        "-cell", required=False, help="Start cell or instance name (optional with -net)"
    )
    parser.add_argument(
        "-pin",
        action="append",
        default=None,
        help="Pin name(s) to trace. Exact bit-level (e.g. data[3]) or bare bus base name "
        "(e.g. data, expands to all data[0..N]). Comma-separated or repeated. "
        "Omit to trace every bit-level pin of cell. Mutually exclusive with -net.",
    )
    parser.add_argument(
        "-net",
        default=None,
        help="Net name to trace (alternative to -pin). Mutually exclusive with -pin. "
        "Traces from the specified net through all subckts containing it.",
    )
    parser.add_argument(
        "-spef",
        default=None,
        help="Optional SPEF file (.spef or .spef.gz) to overlay parasitic metadata "
        "(capacitance/resistance) on traced paths.",
    )
    parser.add_argument("-target", default=None, help="Target cell or instance name (optional)")
    parser.add_argument(
        "-max_depth", type=int, default=None, help="Cap each path to start + max_depth more nodes"
    )
    parser.add_argument(
        "-defines", default=None, help="Comma-separated list of preprocessor defines"
    )
    parser.add_argument(
        "-trace_format",
        choices=["text", "json"],
        default="text",
        help="Output format: text (default) or JSON",
    )
    parser.add_argument(
        "-include",
        dest="include_path",
        action="append",
        default=None,
        help="Additional directory to search for include files (repeatable)",
    )
    parser.add_argument(
        "-format",
        choices=["auto", "spice", "cdl", "spectre", "verilog", "edif"],
        default="auto",
        help="Override format auto-detection. Default: auto.",
    )
    args = parser.parse_args()

    # Post-validation: enforce mutually exclusive flags and requirements
    if args.net and args.pin:
        print("ERROR: cannot combine -net with -pin", file=sys.stderr)
        return 1

    if args.spef and not args.netlist:
        print("ERROR: SPEF overlay requires a primary netlist", file=sys.stderr)
        return 1

    if args.net and not args.cell:
        # -net mode: -cell is optional (becomes a filter)
        pass
    elif args.pin and not args.cell:
        # -pin mode: -cell is required
        print("ERROR: -cell is required with -pin mode", file=sys.stderr)
        return 1
    elif not args.net and not args.pin and not args.cell:
        # Neither -net nor -pin provided, and no -cell: error
        print("ERROR: -cell is required when neither -net nor -pin is provided", file=sys.stderr)
        return 1

    # Set logging level for JSON output
    if args.trace_format == "json":
        logging.getLogger().setLevel(logging.WARNING)

    user_defines = set(args.defines.split(",")) if args.defines else set()

    netlist_file = args.netlist
    start_name = args.cell
    target_name = args.target

    # Parse -pin arguments: build list from comma-separated and repeated flags
    pins: list[str] | None = None
    used_omit_mode = False
    if args.pin is None:
        # Omit-mode: no -pin flag provided
        pins = None
        used_omit_mode = True
    else:
        # Parse comma-separated and repeated flags
        all_pins: list[str] = []
        for pin_spec in args.pin:
            all_pins.extend(p.strip() for p in pin_spec.split(",") if p.strip())
        pins = list(dict.fromkeys(all_pins))  # type: ignore  # dedupe while preserving order

    if not os.path.isfile(netlist_file) and not os.path.isdir(netlist_file):
        print(f"ERROR: Netlist file or directory not found: {netlist_file}")
        return 1

    # Early-exit peek if both -cell and -pin provided
    if start_name and pins is not None:
        fmt_hnt = None if args.format == "auto" else args.format
        pk_rslt = NetlistParser.peek_pins(netlist_file, start_name, fmt=fmt_hnt)

        if pk_rslt is not None:
            # Peek succeeded: check pin validity
            pn_st = set(pk_rslt)
            bus_bss = {
                re.sub(r"\[\d+\]$|<\d+>$", "", p)
                for p in pk_rslt
                if re.search(r"\[\d+\]$|<\d+>$", p)
            }

            msng = []
            for pn_csv in pins:
                for pn in pn_csv.split(","):
                    pn = pn.strip()
                    if not pn:
                        continue
                    if pn in pn_st or pn in bus_bss:
                        continue
                    msng.append(pn)

            if msng:
                from netlist_tracer.tracer import suggest_pins

                for pn in msng:
                    print(
                        f"ERROR: Pin '{pn}' not found in cell '{start_name}' (peek)",
                        file=sys.stderr,
                    )
                    sgg = suggest_pins(pn, pk_rslt)
                    if sgg:
                        print(f"Did you mean: {sgg[:10]}", file=sys.stderr)
                    else:
                        print(
                            f"Available pins ({len(pk_rslt)} total): "
                            f"{pk_rslt[:10]}{'...' if len(pk_rslt) > 10 else ''}",
                            file=sys.stderr,
                        )
                sys.exit(1)
        # else: peek_result is None, fall through to full parse

    try:
        fmt = None if args.format == "auto" else args.format
        nl_parser = NetlistParser(
            netlist_file,
            defines=user_defines if user_defines else None,
            include_paths=args.include_path,
            format=fmt,
        )
    except NetlistParseError as e:
        print(f"ERROR: {e}")
        return 1
    except Exception as e:
        print(f"ERROR: Failed to parse netlist: {e}")
        return 1

    tracer = BidirectionalTracer(nl_parser)

    # Parse SPEF overlay if provided
    spef_ovly = None
    if args.spef:
        try:
            spef_dt = parse_spef(args.spef)
            spef_ovly = SpefOverlay(spef_dt)
            _logger.info(f"Loaded SPEF overlay from {args.spef} with {len(spef_dt.nets)} nets")
        except Exception as e:
            print(f"ERROR: Failed to parse SPEF file: {e}", file=sys.stderr)
            return 1

    # Handle per-net tracing mode (new)
    if args.net:
        results = tracer.trace_net(
            args.net, cell_filter=start_name, target_name=target_name, max_depth=args.max_depth
        )
        mode = "net"
    else:
        # Handle per-pin tracing mode (existing)
        # Validate start_name resolves before doing any output work, to avoid the
        # misleading "Tracing: <0 pins>" headers + format-help block.
        if not tracer.resolve_name(start_name):
            print(
                f"ERROR: '{start_name}' not found as cell type or instance name",
                file=sys.stderr,
            )
            # Suggest similar cell names using fuzzy matching
            all_cells = list(nl_parser.subckts.keys())
            suggestions = difflib.get_close_matches(start_name, all_cells, n=10, cutoff=0.6)
            if suggestions:
                print(f"Did you mean: {suggestions}", file=sys.stderr)
            return 1

        # Trace all requested pins
        results = tracer.trace_pins(
            start_name, pins=pins, target_name=target_name, max_depth=args.max_depth
        )
        mode = "pin"

    # If any explicitly-requested pin can't be expanded to bit-level pins
    # (neither an exact pin match nor a bus base name with indexed members),
    # tracer.trace() already printed "ERROR: Pin '...' not found" plus the
    # "Did you mean: [...]" suggestions. Exit non-zero so the user (or
    # caller scripts) sees the failure.
    # Skip this validation in net mode (pins are not applicable).
    if pins is not None and mode == "pin" and start_name:
        bad = False
        for start_cell, _ in tracer.resolve_name(start_name):
            subckt = nl_parser.subckts.get(start_cell)
            if subckt and any(not tracer.expand_pin(subckt, p) for p in pins):
                bad = True
                break
        if bad:
            return 1

    # Output in requested format
    if args.trace_format == "json":
        return _output_json(
            nl_parser, start_name, target_name, args.max_depth, results, spef_ovly, mode, args.spef
        )
    else:
        return _output_text(
            nl_parser, tracer, start_name, target_name, results, used_omit_mode, spef_ovly, mode
        )


def _output_text(
    nl_parser: NetlistParser,
    tracer: BidirectionalTracer,
    start_name: str | None,
    target_name: str | None,
    results: dict[str, list[list[TraceStep]]],
    used_omit_mode: bool,
    spef_ovly: Any | None = None,
    mode: str = "pin",
) -> int:
    """Output results in text format (with backward-compat single-pin handling)."""
    print(f"Format: {nl_parser.format}")
    print(f"Found {len(nl_parser.subckts)} module/subcircuit definitions")

    def _print_match(cell_type: str, chain: tuple[tuple[str, str], ...] | None) -> None:
        if chain:
            leaf_inst, leaf_parent = chain[-1]
            if len(chain) == 1:
                print(f"  -> instance {leaf_inst} of {cell_type} (in {leaf_parent})")
            else:
                hier = ".".join(c[0] for c in chain)
                print(f"  -> instance {hier} of {cell_type}")
        else:
            print(f"  -> cell type {cell_type}")

    # In net mode without -cell, skip the Start: resolution block and emit header from results keys instead
    if mode == "net" and start_name is None:
        # Net-mode without cell filter: print net header instead
        print("\nNet(s) traced (no cell filter):")
        for result_key in sorted(results.keys()):
            subckt_name = result_key.split(":")[0] if ":" in result_key else result_key
            net_name = result_key.split(":", 1)[1] if ":" in result_key else result_key
            print(f"  -> net {net_name} in subckt {subckt_name}")
    else:
        # Pin mode, or net mode with -cell: show Start: resolution
        start_matches = tracer.resolve_name(start_name) if start_name else []
        print(f"\nStart: {start_name}")
        for cell_type, chain in start_matches:
            _print_match(cell_type, chain)

    if target_name:
        target_matches = tracer.resolve_name(target_name)
        print(f"Target: {target_name}")
        for cell_type, chain in target_matches:
            _print_match(cell_type, chain)
    else:
        print("Target: (all endpoints)")

    # Check if this is legacy single-pin mode (backward-compat)
    is_single_pin = len(results) == 1 and not used_omit_mode
    if is_single_pin:
        pin_name = next(iter(results.keys()))
        paths = results[pin_name]
        if not paths:
            print(f"\nTracing: {start_name}.{pin_name} -> all endpoints")
            print("-" * 50)
            print("\nNo paths found.")
            print("Possible reasons:")
            print("  - The cells are not hierarchically connected")
            print("  - The pin name is incorrect")
            print("  - The cell has no connections through this pin")
            return 0

        # Legacy single-pin output (byte-identical to original)
        if target_name:
            print(f"\nTracing: {start_name}.{pin_name} -> {target_name}")
        else:
            print(f"\nTracing: {start_name}.{pin_name} -> all endpoints")

        print("-" * 50)

        seen = set()
        unique_paths = []
        for path in paths:
            sig = format_path(path, spef_ovly)
            if sig in seen:
                continue
            seen.add(sig)
            unique_paths.append((path, sig))
        print(f"\nFound {len(unique_paths)} path(s):")
        print(
            "Format: <CELL>|<HierarchicalInstanceName>|<PIN>   "
            "where HierarchicalInstanceName = <inst1>/<inst2>/.../<CELL_INST>"
        )
        print(
            "        <CELL>|<internal>|<NET>   for the topmost CELL's pin "
            "(same as NET) connecting to a subblock pin, OR a local "
            "maxima in the path where 2 subblock pins are connected by "
            "a NET inside the CELL\n"
        )
        for i, (_path, sig) in enumerate(unique_paths, 1):
            print(f"Path {i}: {sig}")
        return 0
    else:
        # Multi-pin output: sectioned format
        num_pins = len(results)
        if target_name:
            print(f"\nTracing: {start_name}.<{num_pins} pins> -> {target_name}")
        else:
            print(f"\nTracing: {start_name}.<{num_pins} pins> -> all endpoints")

        # Print format explanation once
        print(
            "\nFormat: <CELL>|<HierarchicalInstanceName>|<PIN>   "
            "where HierarchicalInstanceName = <inst1>/<inst2>/.../<CELL_INST>"
        )
        print(
            "        <CELL>|<internal>|<NET>   for the topmost CELL's pin "
            "(same as NET) connecting to a subblock pin, OR a local "
            "maxima in the path where 2 subblock pins are connected by "
            "a NET inside the CELL\n"
        )

        for pin_name in sorted(results.keys()):
            paths = results[pin_name]
            # Dedupe before printing the header so the count reflects what
            # the user will actually see below (was: len(paths) including dupes).
            seen = set()
            unique_paths = []
            for path in paths:
                sig = format_path(path, spef_ovly)
                if sig in seen:
                    continue
                seen.add(sig)
                unique_paths.append((path, sig))

            print()
            print("=" * 60)
            if mode == "net":
                print(f"== Net: {pin_name} ({len(unique_paths)} paths)")
            else:
                print(f"== Pin: {pin_name} ({len(unique_paths)} paths)")
            print("=" * 60)

            if not unique_paths:
                print("No paths found.")
                continue

            for i, (_path, sig) in enumerate(unique_paths, 1):
                print(f"Path {i}: {sig}")

        return 0


def _output_json(
    nl_parser: NetlistParser,
    start_name: str | None,
    target_name: str | None,
    max_depth: int | None,
    results: dict[str, list[list[TraceStep]]],
    spef_ovly: Any | None = None,
    mode: str = "pin",
    spef_path: str | None = None,
) -> int:
    """Output results in JSON format."""
    output: dict[str, object] = {
        "tool": "netlist-tracer",
        "version": __version__,
        "netlist": nl_parser.source_path,  # Source path from parser
        "cell": start_name,
        "target": target_name,
        "max_depth": max_depth,
        "pins": {},
    }

    # Add SPEF overlay metadata if present
    if spef_ovly and hasattr(spef_ovly, "data"):
        output["spef_overlay"] = {
            "file": spef_path or "unknown",
            "design": getattr(spef_ovly.data, "design_name", ""),
            "num_nets": len(spef_ovly.data.nets),
        }

    pins_dict: dict[str, object] = {}
    for pin_name, paths in results.items():
        pin_entry = {
            "paths": [
                {
                    "formatted": format_path(path, spef_ovly),
                    "steps": [_format_step_for_json(step, spef_ovly) for step in path],
                }
                for path in paths
            ]
        }
        pins_dict[pin_name] = pin_entry
    output["pins"] = pins_dict

    print(json.dumps(output, indent=2))
    return 0


if __name__ == "__main__":
    exit(main())
