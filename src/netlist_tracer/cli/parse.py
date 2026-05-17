"""CLI for netlist parsing and JSON export."""

import argparse
import logging
import os
import sys

from netlist_tracer._logging import get_logger
from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.parser import NetlistParser

_logger = get_logger(__name__)


def _parse_plus_args(argv: list[str]) -> tuple[list[str], list[str], list[str]]:
    """
    Extract +define+ and +incdir+ tokens from argv.

    Parses Verilog tool-style flags:
    - +define+MACRO[=VAL] → macro name (value part dropped if present; logged as debug)
    - +incdir+/path → include path

    Multiple instances accumulate.

    Inputs:
        argv: Command-line argument list (typically sys.argv[1:])

    Outputs:
        Tuple of (filtered_argv, defines, incdirs) where:
          - filtered_argv has the +-prefix tokens removed
          - defines is the list of macro names from +define+ tokens
          - incdirs is the list of include paths from +incdir+ tokens
    """
    defines: list[str] = []
    incdirs: list[str] = []
    filtered: list[str] = []

    for arg in argv:
        if arg.startswith("+define+"):
            # Extract macro name; drop any =VAL part
            dfn_part = arg[8:]  # Strip "+define+"
            if "=" in dfn_part:
                macro_nm = dfn_part.split("=")[0]
                val_part = dfn_part.split("=", 1)[1]
                _logger.debug(f"Macro {macro_nm} has value {val_part}; value-form defines not supported, registering bare name")
            else:
                macro_nm = dfn_part
            if macro_nm:
                defines.append(macro_nm)
        elif arg.startswith("+incdir+"):
            # Extract include path
            inc_pth = arg[8:]  # Strip "+incdir+"
            if inc_pth:
                incdirs.append(inc_pth)
        else:
            filtered.append(arg)

    return filtered, defines, incdirs


def main() -> int:
    """Parse a netlist and export to JSON (CLI entry point).

    Returns:
        Exit code (0 for success, 1 for error).
    """
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    # Parse +define+ / +incdir+ Verilog tool-style flags before argparse
    filtered_argv, plus_defines, plus_incdirs = _parse_plus_args(sys.argv[1:])

    parser = argparse.ArgumentParser(
        description="Netlist Parser - Parse and export to JSON",
        epilog="Preprocessor defines: Use +define+MACRO[=VAL] (Verilog tool-style). "
               "Include paths: Use +incdir+PATH (Verilog tool-style, repeatable)."
    )
    parser.add_argument("-netlist", required=True, help="Path to netlist file or directory")
    parser.add_argument("-output", required=True, help="Output JSON file path")
    parser.add_argument("-topcell", default=None, help="Top-level cell name (optional)")
    args = parser.parse_args(filtered_argv)

    # Use defines from +define+ flags
    user_defines = set(plus_defines) if plus_defines else None

    if not os.path.isfile(args.netlist) and not os.path.isdir(args.netlist):
        print(f"ERROR: Netlist file or directory not found: {args.netlist}", file=sys.stderr)
        return 1

    try:
        nl_parser = NetlistParser(
            args.netlist,
            defines=user_defines,
            top=args.topcell,
            include_paths=plus_incdirs if plus_incdirs else None,
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
