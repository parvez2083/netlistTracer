"""CLI for netlist parsing and JSON export."""

import argparse
import logging
import os
import sys

from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.parser import NetlistParser


def main() -> int:
    """Parse a netlist and export to JSON (CLI entry point).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    parser = argparse.ArgumentParser(description="Netlist Parser - Parse and export to JSON")
    parser.add_argument("-netlist", required=True, help="Path to netlist file or directory")
    parser.add_argument("-output", required=True, help="Output JSON file path")
    parser.add_argument("-defines", default=None, help="Comma-separated preprocessor defines")
    parser.add_argument("-topcell", default=None, help="Top-level cell name (optional)")
    parser.add_argument(
        "-include",
        dest="include_path",
        action="append",
        default=None,
        help="Additional directory to search for include files (repeatable)",
    )
    args = parser.parse_args()

    user_defines = set(args.defines.split(",")) if args.defines else None

    if not os.path.isfile(args.netlist) and not os.path.isdir(args.netlist):
        print(f"ERROR: Netlist file or directory not found: {args.netlist}", file=sys.stderr)
        return 1

    try:
        nl_parser = NetlistParser(
            args.netlist,
            defines=user_defines,
            top=args.topcell,
            include_paths=args.include_path,
            format=None,
        )
    except NetlistParseError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: Failed to parse netlist: {e}", file=sys.stderr)
        return 1

    print(f"Format: {nl_parser.format}")
    print(f"Found {len(nl_parser.subckts)} module/subcircuit definitions")
    total_insts = sum(len(insts) for insts in nl_parser.instances_by_parent.values())
    print(f"Found {total_insts} instances")

    try:
        nl_parser.dump_json(args.output)
        return 0
    except Exception as e:
        print(f"ERROR: Failed to write output: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
