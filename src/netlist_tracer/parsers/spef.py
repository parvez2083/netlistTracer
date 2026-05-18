#!/usr/bin/env python3
################################################################################
# AI GENERATED CODE - Review and test before production use
# Author: AI Generated | Date: 2026-05-17
#
# Description: Parser for SPEF (Standard Parasitic Exchange Format) files.
# Converts SPEF files into the standard tracer model (SubcktDef + Instance lists)
# so SPEF is treated as a first-class netlist format alongside SPF/Spectre/SPICE.
#
# Usage: Called internally by NetlistParser; not intended for direct use.
#   Example: subckts, insts, global_nets = parse_spef('design.spef')
#
# Changelog:
#   [2026-05-17] - Rewrite SPEF as first-class format: deleted SpefData/SpefNet/
#                  SpefOverlay; returns (subckts_dict, instances_list, []) tuple
#                  matching SPF parser signature; handles *D_NET, *CONN, *CAP,
#                  *RES, *NAME_MAP indirection, and *C_UNIT/*R_UNIT scaling.
################################################################################

from __future__ import annotations

import gzip
import re

from netlist_tracer._logging import get_logger
from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.model import Instance, SubcktDef

_logger = get_logger(__name__)


################################################################################
# SECTION: SPEF Unit Value Parsing
# Description: Parse SPEF unit directives (*T_UNIT, *C_UNIT, *R_UNIT).
################################################################################


def _parse_unit_value(unit_str: str) -> float:
    """
    Parse SPEF unit directives (*T_UNIT, *C_UNIT, *R_UNIT).

    Format: `<number> <unit>` (e.g., '1 FF', '1 PF', '1 OHM')

    Inputs:
        unit_str: Unit directive string (e.g., '1 FF')

    Outputs:
        float — scaling factor to base SI unit (Farads for capacitance,
                Ohms for resistance, Seconds for time)
    """
    parts = unit_str.strip().split()
    if len(parts) < 2:
        raise NetlistParseError(f"Invalid unit directive: '{unit_str}'")

    try:
        value = float(parts[0])
    except ValueError as e:
        raise NetlistParseError(f"Non-numeric unit value in: '{unit_str}'") from e

    unit = parts[1].upper()

    # Capacitance units (target: Farads)
    if unit == "FF":
        return value * 1e-15
    elif unit == "PF":
        return value * 1e-12
    elif unit == "NF":
        return value * 1e-9
    elif unit == "UF":
        return value * 1e-6
    elif unit == "MF":
        return value * 1e-3
    elif unit == "F":
        return value * 1.0
    # Resistance units (target: Ohms)
    elif unit == "OHM":
        return value * 1.0
    elif unit == "KOHM":
        return value * 1e3
    elif unit == "MOHM":
        return value * 1e6
    # Time units (target: Seconds)
    elif unit == "PS":
        return value * 1e-12
    elif unit == "NS":
        return value * 1e-9
    elif unit == "US":
        return value * 1e-6
    elif unit == "MS":
        return value * 1e-3
    elif unit == "S":
        return value * 1.0
    else:
        _logger.warning(f"Unknown SPEF unit: {unit}. Defaulting to 1.0.")
        return value


################################################################################
# SECTION: SPEF State Management
# Description: Track parsing state for SPEF directives and net resolution.
################################################################################


class _SpefState:
    """Per-parse state for SPEF directives and name indirection."""

    def __init__(self) -> None:
        self.design_name: str = ""
        self.divider: str = "/"
        self.delimiter: str = ":"
        self.bus_delim_open: str = "["
        self.bus_delim_close: str = "]"
        self.t_unit_scale: float = 1.0  # Seconds (from *T_UNIT)
        self.c_unit_scale: float = 1.0  # Farads (from *C_UNIT)
        self.r_unit_scale: float = 1.0  # Ohms (from *R_UNIT)
        self.name_map: dict[str, str] = {}  # *5 -> real_net_name
        self.ports: list[str] = []  # Port names from *PORTS


################################################################################
# SECTION: SPEF Parsing
# Description: Main parser for SPEF file format.
################################################################################


def parse_spef(
    filepath: str, include_paths: list[str] | None = None
) -> tuple[dict[str, SubcktDef], list[Instance], list[str]]:
    """
    Parse a SPEF (.spef or .spef.gz) file into tracer model.

    Returns a SubcktDef for the design and Instance objects for R/C elements,
    matching the SPF parser interface. Handles *NAME_MAP indirection,
    *PORTS, *D_NET blocks with *CONN, *CAP, *RES sub-sections, and unit
    scaling from *T_UNIT, *C_UNIT, *R_UNIT. Star-references (*5) are
    resolved to real names during parse so downstream code sees only real names.

    Inputs:
        filepath: Filesystem path to .spef or .spef.gz file
        include_paths: Reserved for future use; not consumed in v0.6.0

    Outputs:
        (subckts dict with single design entry, instances list of R/C/X,
         global_nets empty list) or raises NetlistParseError on malformed input

    Complexity:
        O(n) where n is file lines; one pass with state machine
    """
    stt = _SpefState()

    # Determine if file is gzipped and open accordingly
    try:
        if filepath.lower().endswith(".gz"):
            with gzip.open(filepath, "rt", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        else:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
    except OSError as e:
        raise NetlistParseError(f"Failed to read SPEF file '{filepath}': {e}") from e

    if not lines:
        raise NetlistParseError(f"Empty SPEF file: {filepath}")

    # State machine: track current net and section modes
    crnt_net: str | None = None  # Name of net currently inside *D_NET block
    net_pins: dict[str, list[str]] = {}  # net_name -> list of pin refs
    r_insts: list[Instance] = []  # R/C instances to emit
    found_end = False

    # Section state flags (for multi-line section formats)
    in_name_map = False
    in_ports_sec = False
    in_cap_section = False
    in_res_section = False

    for _line_num, line in enumerate(lines, 1):
        line = line.rstrip("\n\r")
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("*'"):
            continue

        # *SPEF "version" "time_unit" "capacitance_unit" "resistance_unit"
        # This is a header marker; parse it but don't require it
        m = re.match(r"\*SPEF\s+", stripped)
        if m:
            continue

        # *DESIGN <name>
        m = re.match(r"\*DESIGN\s+(\S+)", stripped)
        if m:
            stt.design_name = m.group(1).strip("'\"")
            continue

        # *DIVIDER <char>
        m = re.match(r"\*DIVIDER\s+(\S)", stripped)
        if m:
            stt.divider = m.group(1)
            continue

        # *DELIMITER <char>
        m = re.match(r"\*DELIMITER\s+(\S)", stripped)
        if m:
            stt.delimiter = m.group(1)
            continue

        # *BUS_DELIMITER_OPEN <char>
        m = re.match(r"\*BUS_DELIMITER_OPEN\s+(\S)", stripped)
        if m:
            stt.bus_delim_open = m.group(1)
            continue

        # *BUS_DELIMITER_CLOSE <char>
        m = re.match(r"\*BUS_DELIMITER_CLOSE\s+(\S)", stripped)
        if m:
            stt.bus_delim_close = m.group(1)
            continue

        # *T_UNIT <value> <unit>
        m = re.match(r"\*T_UNIT\s+(.+)", stripped)
        if m:
            stt.t_unit_scale = _parse_unit_value(m.group(1))
            continue

        # *C_UNIT <value> <unit>
        m = re.match(r"\*C_UNIT\s+(.+)", stripped)
        if m:
            stt.c_unit_scale = _parse_unit_value(m.group(1))
            continue

        # *R_UNIT <value> <unit>
        m = re.match(r"\*R_UNIT\s+(.+)", stripped)
        if m:
            stt.r_unit_scale = _parse_unit_value(m.group(1))
            continue

        # *NAME_MAP section header (bare keyword)
        if stripped == "*NAME_MAP":
            in_name_map = True
            continue

        # NAME_MAP data lines within section: *<idx> <real_name>
        if in_name_map:
            # Check if this is a NAME_MAP data line: *<number> <name>
            m = re.match(r"^(\*\d+)\s+(\S+)", stripped)
            if m:
                alias = m.group(1)
                real_name = m.group(2)
                stt.name_map[alias] = real_name
                continue
            elif stripped.startswith("*"):
                # New directive encountered; exit NAME_MAP section
                in_name_map = False
                # Fall through to process this line as new directive
            else:
                # Skip non-* lines (blank or other content)
                continue

        # Inline *NAME_MAP entries: *NAME_MAP <alias> <real_name>
        m = re.match(r"\*NAME_MAP\s+(\S+)\s+(\S+)", stripped)
        if m:
            alias = m.group(1)
            real_name = m.group(2)
            stt.name_map[alias] = real_name
            continue

        # *PORTS section header
        if stripped == "*PORTS":
            in_ports_sec = True
            continue

        # PORTS section data lines: *<idx> <direction>
        if in_ports_sec:
            if stripped.startswith("*") and not re.match(r"^\*\d+\s+", stripped):
                # End of PORTS section (new directive, not *<idx>)
                in_ports_sec = False
                # Fall through to process this line as new directive
            else:
                # Port data line: *<idx> <direction>
                # Per IEEE 1481, *NAME_MAP must precede *PORTS section
                m = re.match(r"^\*(\d+)\s+([IOB])", stripped)
                if m:
                    alias = f"*{m.group(1)}"
                    real_name = stt.name_map.get(alias, alias)
                    if real_name not in stt.ports:
                        stt.ports.append(real_name)
                    continue
                else:
                    continue

        # Inline *PORTS: *PORTS <direction> [names...]
        m = re.match(r"\*PORTS\s+(.+)", stripped)
        if m and not in_ports_sec:
            parts = m.group(1).split()
            if parts:
                # Skip first part if it looks like a direction (I/O/B)
                start_idx = 1 if parts[0] in ("I", "O", "B") else 0
                for prt_nm in parts[start_idx:]:
                    if prt_nm not in stt.ports:
                        stt.ports.append(prt_nm)
            continue

        # *D_NET <name> <total_cap>
        m = re.match(r"\*D_NET\s+(\S+)\s+(.+)", stripped)
        if m:
            net_nm_raw = m.group(1)
            cap_str = m.group(2).strip()

            # Resolve name_map indirection
            net_nm = stt.name_map.get(net_nm_raw, net_nm_raw)

            # Parse capacitance (raw value; will scale by c_unit_scale)
            try:
                float(cap_str)  # Validate but don't store (for future use)
            except ValueError:
                _logger.warning(f"Could not parse capacitance '{cap_str}' for net '{net_nm}'")

            crnt_net = net_nm
            net_pins[net_nm] = []
            in_cap_section = False
            in_res_section = False
            continue

        # *CONN (inside a *D_NET block)
        # Format: *CONN *I <inst>:<pin> I/O *L <load> [*D <cell>]
        #      or *CONN *P <port> I/O ...
        m = re.match(r"\*CONN\s+(.+)", stripped)
        if m and crnt_net is not None:
            parts = m.group(1).split()
            # First part is the key: *I (instance) or *P (port)
            if parts and len(parts) >= 2:
                # conn_type = parts[0]  # *I or *P (unused in current implementation)
                conn_name = parts[1]  # inst:pin or port
                net_pins[crnt_net].append(conn_name)
            continue

        # *CAP section header
        if stripped == "*CAP" and crnt_net is not None:
            in_cap_section = True
            in_res_section = False
            continue

        # CAP section data lines: <idx> <node_a> <node_b> <value>
        if in_cap_section and crnt_net is not None:
            if stripped.startswith("*"):
                # End of CAP section
                in_cap_section = False
                # Fall through to process this line as new directive
            else:
                # CAP data line
                m = re.match(r"^(\S+)\s+(\S+)\s+(\S+)\s+(.+)", stripped)
                if m:
                    cap_idx = m.group(1)
                    node_a = stt.name_map.get(m.group(2), m.group(2))
                    node_b = stt.name_map.get(m.group(3), m.group(3))
                    cap_str = m.group(4).strip()

                    try:
                        cap_raw = float(cap_str)
                        cap_val = cap_raw * stt.c_unit_scale
                    except ValueError:
                        _logger.warning(f"Could not parse capacitance '{cap_str}' in *CAP line")
                        continue

                    # Create C-prefix instance
                    inst_name = f"C{cap_idx}"
                    r_insts.append(
                        Instance(
                            name=inst_name,
                            cell_type="C",
                            nets=[node_a, node_b],
                            parent_cell=stt.design_name or "top",
                            params={"_value": f"{cap_val:g}"},
                        )
                    )
                continue

        # *CAP inline entry: *CAP <index> <node_a> <node_b> <value>
        m = re.match(r"\*CAP\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)", stripped)
        if m and crnt_net is not None and not in_cap_section:
            cap_idx = m.group(1)
            node_a = stt.name_map.get(m.group(2), m.group(2))
            node_b = stt.name_map.get(m.group(3), m.group(3))
            cap_str = m.group(4).strip()

            try:
                cap_raw = float(cap_str)
                cap_val = cap_raw * stt.c_unit_scale
            except ValueError:
                _logger.warning(f"Could not parse capacitance '{cap_str}' in *CAP line")
                continue

            # Create C-prefix instance
            inst_name = f"C{cap_idx}"
            r_insts.append(
                Instance(
                    name=inst_name,
                    cell_type="C",
                    nets=[node_a, node_b],
                    parent_cell=stt.design_name or "top",
                    params={"_value": f"{cap_val:g}"},
                )
            )
            continue

        # *RES section header
        if stripped == "*RES" and crnt_net is not None:
            in_res_section = True
            in_cap_section = False
            continue

        # RES section data lines: <idx> <node_a> <node_b> <value>
        if in_res_section and crnt_net is not None:
            if stripped.startswith("*"):
                # End of RES section
                in_res_section = False
                # Fall through to process this line as new directive
            else:
                # RES data line
                m = re.match(r"^(\S+)\s+(\S+)\s+(\S+)\s+(.+)", stripped)
                if m:
                    res_idx = m.group(1)
                    node_a = stt.name_map.get(m.group(2), m.group(2))
                    node_b = stt.name_map.get(m.group(3), m.group(3))
                    res_str = m.group(4).strip()

                    try:
                        res_raw = float(res_str)
                        res_val = res_raw * stt.r_unit_scale
                    except ValueError:
                        _logger.warning(f"Could not parse resistance '{res_str}' in *RES line")
                        continue

                    # Create R-prefix instance
                    inst_name = f"R{res_idx}"
                    r_insts.append(
                        Instance(
                            name=inst_name,
                            cell_type="R",
                            nets=[node_a, node_b],
                            parent_cell=stt.design_name or "top",
                            params={"_value": f"{res_val:g}"},
                        )
                    )
                continue

        # *RES inline entry: *RES <index> <node_a> <node_b> <value>
        m = re.match(r"\*RES\s+(\S+)\s+(\S+)\s+(\S+)\s+(.+)", stripped)
        if m and crnt_net is not None and not in_res_section:
            res_idx = m.group(1)
            node_a = stt.name_map.get(m.group(2), m.group(2))
            node_b = stt.name_map.get(m.group(3), m.group(3))
            res_str = m.group(4).strip()

            try:
                res_raw = float(res_str)
                res_val = res_raw * stt.r_unit_scale
            except ValueError:
                _logger.warning(f"Could not parse resistance '{res_str}' in *RES line")
                continue

            # Create R-prefix instance
            inst_name = f"R{res_idx}"
            r_insts.append(
                Instance(
                    name=inst_name,
                    cell_type="R",
                    nets=[node_a, node_b],
                    parent_cell=stt.design_name or "top",
                    params={"_value": f"{res_val:g}"},
                )
            )
            continue

        # *END (end of current net or section)
        if stripped == "*END":
            crnt_net = None
            in_cap_section = False
            in_res_section = False
            found_end = True
            continue

    # Validate that *END marker was found (IEEE 1481 requires it)
    if not found_end:
        _logger.warning(f"SPEF file '{filepath}' missing *END marker (IEEE 1481 compliance)")

    # Build top-level SubcktDef from *DESIGN + *PORTS
    design_name = stt.design_name or _derive_design_name_from_path(filepath)
    top_def = SubcktDef(name=design_name, pins=stt.ports)

    # All R/C/X instances belong to the top-level design
    for inst in r_insts:
        inst.parent_cell = design_name

    sbckts: dict[str, SubcktDef] = {design_name: top_def}
    return sbckts, r_insts, []


def _derive_design_name_from_path(filepath: str) -> str:
    """
    Derive a design name from a file path if *DESIGN is missing.

    Extracts the base filename without extension.

    Inputs:
        filepath: Filesystem path to SPEF file

    Outputs:
        str — design name (basename without suffix)
    """
    import os

    basename = os.path.basename(filepath)
    # Strip .gz if present
    if basename.lower().endswith(".gz"):
        basename = basename[:-3]
    # Strip .spef extension
    if basename.lower().endswith(".spef"):
        basename = basename[:-5]
    return basename or "top"
