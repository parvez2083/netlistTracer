"""Parser for SPEF (Standard Parasitic Exchange Format) files."""

from __future__ import annotations

import gzip
import re
from dataclasses import dataclass, field

from netlist_tracer._logging import get_logger
from netlist_tracer.exceptions import NetlistParseError

_logger = get_logger(__name__)


################################################################################
# SECTION: SPEF Data Models
# Description: Dataclasses for SPEF file contents and overlay functionality.
################################################################################


@dataclass
class SpefNet:
    """Represents a single net entry from a SPEF *D_NET block."""

    name: str
    total_cap: float = 0.0  # Farads (post-scaling)
    total_res: float | None = None  # Ohms (post-scaling) or None if no *RES present
    num_caps: int = 0
    num_res: int = 0
    pins: list[str] = field(default_factory=list)


@dataclass
class SpefData:
    """Parsed SPEF aggregate populated by parse_spef()."""

    design_name: str = ""
    divider: str = "/"
    delimiter: str = ":"
    bus_delim_open: str = "["
    bus_delim_close: str = "]"
    t_unit_scale: float = 1.0  # Seconds (from *T_UNIT)
    c_unit_scale: float = 1.0  # Farads (from *C_UNIT)
    r_unit_scale: float = 1.0  # Ohms (from *R_UNIT)
    name_map: dict[str, str] = field(default_factory=dict)  # *5 -> real_net_name
    ports: list[str] = field(default_factory=list)  # *PORTS lines
    nets: dict[str, SpefNet] = field(default_factory=dict)  # name -> SpefNet


################################################################################
# SECTION: SPEF Parsing
# Description: Parse SPEF/SPEF.GZ files with unit scaling and name indirection.
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


def parse_spef(path: str) -> SpefData:
    """
    Parse a SPEF (.spef or .spef.gz) file into SpefData.

    Handles *NAME_MAP indirection, *PORTS, *D_NET blocks with *CONN, *CAP, *RES
    sub-sections, and unit scaling from *T_UNIT, *C_UNIT, *R_UNIT. Star-references
    (*5) are resolved to real names during parse so SpefData.nets keys are real names.

    Inputs:
        path: Filesystem path to .spef or .spef.gz file

    Outputs:
        SpefData populated with all parsed nets; raises NetlistParseError on
        malformed input

    Complexity:
        O(n) where n is file lines; one pass with state machine
    """
    data = SpefData()

    # Determine if file is gzipped and open accordingly
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rt", encoding="utf-8") as f:
                lines = f.readlines()
        else:
            with open(path, encoding="utf-8") as f:
                lines = f.readlines()
    except Exception as e:
        raise NetlistParseError(f"Failed to read SPEF file '{path}': {e}") from e

    if not lines:
        raise NetlistParseError(f"Empty SPEF file: {path}")

    # State machine: track current net being parsed
    crnt_net: SpefNet | None = None
    found_end = False

    for _line_num, line in enumerate(lines, 1):
        line = line.rstrip("\n\r")
        stripped = line.strip()

        # Skip empty lines and comments
        if not stripped or stripped.startswith("*'"):
            continue

        # *DESIGN <name>
        m = re.match(r"\*DESIGN\s+(\S+)", stripped)
        if m:
            data.design_name = m.group(1)
            continue

        # *DIVIDER <char>
        m = re.match(r"\*DIVIDER\s+(\S)", stripped)
        if m:
            data.divider = m.group(1)
            continue

        # *DELIMITER <char>
        m = re.match(r"\*DELIMITER\s+(\S)", stripped)
        if m:
            data.delimiter = m.group(1)
            continue

        # *BUS_DELIMITER_OPEN <char>
        m = re.match(r"\*BUS_DELIMITER_OPEN\s+(\S)", stripped)
        if m:
            data.bus_delim_open = m.group(1)
            continue

        # *BUS_DELIMITER_CLOSE <char>
        m = re.match(r"\*BUS_DELIMITER_CLOSE\s+(\S)", stripped)
        if m:
            data.bus_delim_close = m.group(1)
            continue

        # *T_UNIT <value> <unit>
        m = re.match(r"\*T_UNIT\s+(.+)", stripped)
        if m:
            data.t_unit_scale = _parse_unit_value(m.group(1))
            continue

        # *C_UNIT <value> <unit>
        m = re.match(r"\*C_UNIT\s+(.+)", stripped)
        if m:
            data.c_unit_scale = _parse_unit_value(m.group(1))
            continue

        # *R_UNIT <value> <unit>
        m = re.match(r"\*R_UNIT\s+(.+)", stripped)
        if m:
            data.r_unit_scale = _parse_unit_value(m.group(1))
            continue

        # *NAME_MAP <alias> <real_name>
        m = re.match(r"\*NAME_MAP\s+(\S+)\s+(\S+)", stripped)
        if m:
            alias = m.group(1)
            real_name = m.group(2)
            data.name_map[alias] = real_name
            continue

        # *PORTS <direction> [names...]
        m = re.match(r"\*PORTS\s+(.+)", stripped)
        if m:
            # Ports line; extract port names (skip the direction keyword)
            parts = m.group(1).split()
            if parts:
                # Typically: *PORTS I port1 port2 ...  or  *PORTS O port1 ...
                # We just collect names; direction is in parts[0]
                for prt_nm in parts[1:]:
                    if prt_nm not in data.ports:
                        data.ports.append(prt_nm)
            continue

        # *D_NET <name> <total_cap>
        m = re.match(r"\*D_NET\s+(\S+)\s+(.+)", stripped)
        if m:
            net_nm_raw = m.group(1)
            cap_str = m.group(2).strip()

            # Resolve name_map indirection
            net_nm = data.name_map.get(net_nm_raw, net_nm_raw)

            # Parse capacitance (raw value; will scale by c_unit_scale later)
            try:
                cap_raw = float(cap_str)
                cap_val = cap_raw * data.c_unit_scale
            except ValueError:
                _logger.warning(f"Could not parse capacitance '{cap_str}' for net '{net_nm}'")
                cap_val = 0.0

            crnt_net = SpefNet(name=net_nm, total_cap=cap_val, total_res=None)
            data.nets[net_nm] = crnt_net
            continue

        # *CONN (inside a *D_NET block)
        m = re.match(r"\*CONN\s+(.+)", stripped)
        if m and crnt_net:
            # Example: *CONN P inst_pin_ref  or  *CONN I inst pin
            parts = m.group(1).split()
            if len(parts) >= 1:
                conn_name = parts[0]
                # Store as pin reference (could be expanded later if needed)
                if conn_name not in crnt_net.pins:
                    crnt_net.pins.append(conn_name)
            continue

        # *CAP <order> <value> [<node1> <node2> ...]
        m = re.match(r"\*CAP\s+(\d+)\s+(.+)", stripped)
        if m and crnt_net:
            # Example: *CAP 1 1.5  (single capacitor, no nodes listed; value already in *D_NET)
            _cap_idx = m.group(1)
            cap_str = m.group(2).strip()
            try:
                cap_raw = float(cap_str)
                cap_val = cap_raw * data.c_unit_scale
                # Aggregate into total_cap (or replace if this is the full value)
                # For simplicity: if value appears in CAP line, it's already counted
                # in the *D_NET total_cap, so skip aggregation
                crnt_net.num_caps += 1
            except ValueError:
                _logger.warning(f"Could not parse capacitance '{cap_str}' in *CAP line")
            continue

        # *RES <order> <value> <node1> <node2> [...]
        m = re.match(r"\*RES\s+(\d+)\s+(.+)", stripped)
        if m and crnt_net:
            # Example: *RES 1 5.0  (single resistor)
            _res_idx = m.group(1)
            res_str = m.group(2).strip()
            try:
                res_raw = float(res_str)
                res_val = res_raw * data.r_unit_scale
                # Aggregate resistance (sum across all *RES lines)
                if crnt_net.total_res is None:
                    crnt_net.total_res = res_val
                else:
                    crnt_net.total_res += res_val
                crnt_net.num_res += 1
            except ValueError:
                _logger.warning(f"Could not parse resistance '{res_str}' in *RES line")
            continue

        # *END (end of current net)
        if stripped == "*END":
            crnt_net = None
            found_end = True
            continue

    # Validate that *END marker was found (IEEE 1481 requires it)
    if not found_end:
        raise NetlistParseError(f"SPEF file '{path}' missing required *END marker (IEEE 1481 compliance)")

    return data


################################################################################
# SECTION: SPEF Overlay for Trace-Time Annotation
# Description: Runtime lookup structure for annotating trace paths with SPEF data.
################################################################################


class SpefOverlay:
    """
    Thin wrapper around SpefData providing trace-time lookup with bus-bracket
    normalization and SPF suffix stripping for net-name matching.
    """

    def __init__(self, data: SpefData) -> None:
        """
        Build the runtime lookup structure; precompute bus-bracket variants
        for tolerant net-name matching.

        Inputs:
            data: SpefData from parse_spef()

        Outputs:
            Initialized overlay with internal name index
        """
        self.data = data
        # Precompute name variants: for each net, include the original name
        # plus variants with [x] <-> <x> bracket styles
        self.name_index: dict[str, SpefNet] = {}
        for nm, spef_net in data.nets.items():
            self.name_index[nm] = spef_net
            # Add bracket variant: [ -> < and ] -> >
            var1 = nm.replace("[", "<").replace("]", ">")
            if var1 != nm:
                self.name_index[var1] = spef_net
            # Add bracket variant: < -> [ and > -> ]
            var2 = nm.replace("<", "[").replace(">", "]")
            if var2 != nm:
                self.name_index[var2] = spef_net

    def lookup(self, net_name: str) -> dict | None:
        """
        Return overlay metadata for a net, or None if not present.

        Strategy:
          1. Try exact match
          2. Strip trailing ':pin' SPF suffix and retry
          3. Try bracket variants
          4. Return None if all miss

        Inputs:
            net_name: Net name from a TraceStep (post-walk)

        Outputs:
            {'C': float_farads, 'R': float_ohms_or_None, 'pins': [...]} or None
        """
        # Try exact match first
        if net_name in self.name_index:
            spef_net = self.name_index[net_name]
            return {
                "C": spef_net.total_cap,
                "R": spef_net.total_res,
                "pins": spef_net.pins,
            }

        # Try stripping SPF ':pin' suffix (e.g., 'inst/M1:G' -> 'inst/M1')
        idx = net_name.rfind(":")
        if idx > 0:
            base_net = net_name[:idx]
            if base_net in self.name_index:
                spef_net = self.name_index[base_net]
                return {
                    "C": spef_net.total_cap,
                    "R": spef_net.total_res,
                    "pins": spef_net.pins,
                }

        # Try bracket variants
        var1 = net_name.replace("[", "<").replace("]", ">")
        if var1 != net_name and var1 in self.name_index:
            spef_net = self.name_index[var1]
            return {
                "C": spef_net.total_cap,
                "R": spef_net.total_res,
                "pins": spef_net.pins,
            }

        var2 = net_name.replace("<", "[").replace(">", "]")
        if var2 != net_name and var2 in self.name_index:
            spef_net = self.name_index[var2]
            return {
                "C": spef_net.total_cap,
                "R": spef_net.total_res,
                "pins": spef_net.pins,
            }

        return None
