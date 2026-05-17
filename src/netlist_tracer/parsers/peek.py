"""Fast pre-scan to peek at cell pin list without full parse."""

from __future__ import annotations

import glob
import gzip
import json
import os
import re
from typing import Any

from netlist_tracer._logging import get_logger
from netlist_tracer.parsers.detect import detect_format

_logger = get_logger(__name__)


def _collect_subckt_pins_from_handle(
    fh: Any,  # file handle (gzip or regular, text mode)
    target_cell: str | None,
) -> Any:  # Returns list[str] | None if target_cell, list[tuple[str, list[str]]] if target_cell is None
    """Collect .SUBCKT pin declarations from file handle.

    Scans for '.SUBCKT <name> <pins...>' lines, handling '+' continuations.
    Filters by target_cell name (case-insensitive) if provided.

    Inputs:
        fh: Open file handle (text mode, already decompressed if .gz)
        target_cell: Cell name to match (None for ALL cells)

    Outputs:
        If target_cell is provided: list[str] of pins (or None if not found)
        If target_cell is None: list[(cell_name, pins_list)] for all cells
    """
    all_cells = []
    for ln in fh:
        ln = ln.rstrip()

        # Look for .SUBCKT line (case-insensitive)
        m = re.match(r"^\s*\.SUBCKT\s+(\S+)\s+(.*)", ln, re.IGNORECASE)
        if not m:
            continue

        sbckt_nm = m.group(1)

        # If target_cell specified, skip non-matching cells
        if target_cell is not None and sbckt_nm.lower() != target_cell.lower():
            continue

        pns = []
        rst_ln = m.group(2).strip()

        # Collect tokens from the rest of this line
        tks = rst_ln.split()
        for tk in tks:
            # Skip params (contain '=')
            if "=" not in tk:
                pns.append(tk)

        # Peek next lines for '+' continuations
        for cn_ln in fh:
            cn_ln = cn_ln.rstrip()
            if not cn_ln.lstrip().startswith("+"):
                break
            # Remove the '+' and split
            cn_rst = cn_ln.lstrip()[1:].strip()
            cn_tks = cn_rst.split()
            for tk in cn_tks:
                if "=" not in tk:
                    pns.append(tk)

        result = (sbckt_nm, pns)

        # Early return if searching for specific cell
        if target_cell is not None:
            return pns if pns else None

        # Accumulate if collecting all cells
        all_cells.append(result)

    # Return accumulated list if target_cell was None, else not found
    if target_cell is None:
        return all_cells
    return None


def peek_pins(flpth: str, cell: str, fmt: str | None = None) -> list[str] | None:
    """Top-level peek dispatcher. Auto-detects format if not provided.

    Fast pre-scan to find a cell's pin list WITHOUT running full parse.
    Returns None if cell not found (safe fall-through to full parse).

    Inputs:
        flpth: File or directory path
        cell: Cell/module name to find
        fmt: Optional explicit format hint ('spice', 'cdl', 'spectre', 'spf',
            'verilog', 'edif', or None for auto-detect)

    Outputs:
        list[str] of pin names if cell found, None otherwise (NEVER raises
        on cell-not-found; raises only on bad inputs like nonexistent path)
    """
    # Validate path exists
    if not os.path.exists(flpth):
        raise FileNotFoundError(f"Path not found: {flpth}")

    # JSON cache: fast load
    if flpth.endswith(".json") and os.path.isfile(flpth):
        return _peek_json(flpth, cell)

    # Verilog directory
    if os.path.isdir(flpth):
        return _peek_vrlog_dr(flpth, cell)

    # Single file: detect format or use hint
    if fmt is None:
        fmt = detect_format([flpth])

    # Dispatch to format-specific peek
    if fmt == "verilog":
        return _peek_vrlog_sf(flpth, cell)
    elif fmt == "spectre":
        return _peek_spctr(flpth, cell)
    elif fmt == "spf":
        return _peek_spce_fmly(flpth, cell)
    elif fmt == "edif":
        return _peek_edf(flpth, cell)
    else:  # spice, cdl, unknown -> treat as spice-family
        return _peek_spce_fmly(flpth, cell)


def _peek_json(flpth: str, cell: str) -> list[str] | None:
    """Load JSON cache, return subckt pins.

    Cache files are already fast to load. Handles both list and dict
    subckt entry shapes.

    Inputs:
        flpth: Path to JSON cache file
        cell: Cell/subckt name to look up

    Outputs:
        list[str] of pin names, or None if cell not found
    """
    try:
        with open(flpth) as f:
            dt = json.load(f)

        sbckts = dt.get("subckts", {})
        if cell not in sbckts:
            return None

        ent = sbckts[cell]
        # Handle both dict (with 'pins' key) and list (direct pin list)
        if isinstance(ent, dict):
            pins = ent.get("pins", [])
        else:
            pins = ent
        return list(pins) if isinstance(pins, list) else None
    except Exception as e:
        _logger.debug(f"JSON peek failed for {cell}: {e}")
        return None


def _peek_spce_fmly(flpth: str, cell: str) -> list[str] | None:
    """Line-scan for '.SUBCKT <cell> ...' (case-insensitive).

    Handle '+' continuation lines. Also covers SPF/CDL.

    Inputs:
        flpth: Path to SPICE/CDL/SPF file (or .gz)
        cell: Cell name to find

    Outputs:
        list[str] of pin names, or None if .SUBCKT not found
    """
    try:
        # Open via gzip if .gz, else regular open with error handling
        if flpth.endswith(".gz"):
            fh = gzip.open(flpth, "rt", errors="replace")
        else:
            fh = open(flpth, encoding="utf-8", errors="replace")

        try:
            return _collect_subckt_pins_from_handle(fh, cell)
        finally:
            fh.close()
    except Exception as e:
        _logger.debug(f"SPICE-family peek failed for {cell}: {e}")
        return None


def _peek_spctr(flpth: str, cell: str) -> list[str] | None:
    """Line-scan for 'subckt <cell> ...' (case-sensitive).

    Spectre uses backslash '\\' line continuation.

    Inputs:
        flpth: Path to Spectre file
        cell: Cell name to find

    Outputs:
        list[str] of pin names, or None if subckt not found
    """
    try:
        with open(flpth, encoding="utf-8", errors="replace") as fh:
            for ln in fh:
                ln_rw = ln.rstrip()

                # Spectre: 'subckt' (no dot, case-sensitive)
                m = re.match(r"^subckt\s+(\S+)\s+(.*)", ln_rw)
                if not m:
                    continue

                sbckt_nm = m.group(1)
                if sbckt_nm != cell:
                    continue

                # Found matching subckt; collect pins
                pns = []
                rst_ln = m.group(2).strip()

                # Remove surrounding parens if present
                if rst_ln.startswith("("):
                    rst_ln = rst_ln[1:].strip()
                if rst_ln.endswith(")"):
                    rst_ln = rst_ln[:-1].strip()

                tks = rst_ln.split()
                for tk in tks:
                    if "=" not in tk:
                        pns.append(tk)

                # Handle backslash continuations
                while ln_rw.endswith("\\"):
                    try:
                        ln = next(fh)
                        ln_rw = ln.rstrip()
                        # Remove trailing ')' and backslash
                        if ln_rw.endswith(")"):
                            ln_rw = ln_rw[:-1].rstrip()
                        if ln_rw.endswith("\\"):
                            ln_rw = ln_rw[:-1].rstrip()
                        tks = ln_rw.split()
                        for tk in tks:
                            if "=" not in tk:
                                pns.append(tk)
                    except StopIteration:
                        break

                return pns if pns else None

        return None
    except Exception as e:
        _logger.debug(f"Spectre peek failed for {cell}: {e}")
        return None


def _peek_vrlog_sf(flpth: str, cell: str) -> list[str] | None:
    """Find 'module <cell> [#(...)] (port_list);' or 'primitive <cell> (port_list);' in a single file.

    Handle multi-line port lists and direction keywords. Also handles Verilog UDPs.

    Inputs:
        flpth: Path to Verilog file
        cell: Module or UDP name to find

    Outputs:
        list[str] of port names (excluding direction keywords), or None
    """
    try:
        with open(flpth, encoding="utf-8", errors="replace") as fh:
            cnt = fh.read()

        # Find 'module <cell>' or 'primitive <cell>' boundary (case-sensitive)
        # Try module first
        pat = r"module\s+" + re.escape(cell) + r"\s*(?:#\s*\(|[\(\[])"
        m = re.search(pat, cnt)
        is_primitive = False
        if not m:
            # Try primitive
            pat = r"primitive\s+" + re.escape(cell) + r"\s*\("
            m = re.search(pat, cnt)
            is_primitive = True
        if not m:
            return None

        # Find the opening parenthesis of the port list
        st = m.start()
        # Skip to the '(' or '['
        i = st + len(m.group())
        if cnt[i - 1] == "(":
            i -= 1  # Back up to the '('
        elif cnt[i - 1] == "[":
            i -= 1  # Back up to the '['

        # Handle parameter block '#(...)' if present (modules only, not primitives)
        if not is_primitive and m.group().find("#") != -1:
            # Skip parameter block
            prn_cnt = 1
            j = m.end()
            while j < len(cnt) and prn_cnt > 0:
                if cnt[j] == "(":
                    prn_cnt += 1
                elif cnt[j] == ")":
                    prn_cnt -= 1
                j += 1
            # Now find the port list opening paren
            m2 = re.search(r"\s*\(", cnt[j:])
            if m2:
                i = j + m2.start()
            else:
                return None
        else:
            i = m.end() - 1  # Back to the '('

        # Find matching closing paren
        prn_cnt = 1
        j = i + 1
        while j < len(cnt) and prn_cnt > 0:
            if cnt[j] == "(":
                prn_cnt += 1
            elif cnt[j] == ")":
                prn_cnt -= 1
            j += 1

        if prn_cnt != 0:
            return None

        prt_lst = cnt[i + 1 : j - 1]

        # Strip line and block comments before tokenizing
        prt_lst = re.sub(r"//[^\n]*", "", prt_lst)
        prt_lst = re.sub(r"/\*.*?\*/", "", prt_lst, flags=re.DOTALL)

        # Strip backtick preprocessor directives. Directives with a macro-name
        # argument (ifdef/ifndef/elif/define/undef/include) consume that token
        # too so we don't mistake it for a pin name. Bare directives
        # (else/endif/celldefine/timescale/...) are removed as standalone tokens.
        prt_lst = re.sub(r"`(ifdef|ifndef|elif|define|undef|include)\s+\S+", "", prt_lst)
        prt_lst = re.sub(r"`\w+", "", prt_lst)

        # Remove direction keywords and net-type qualifiers
        dirs = r"\b(input|output|inout|wire|reg|logic|bit|byte|int|real|string)\b"
        prt_lst = re.sub(dirs, "", prt_lst, flags=re.IGNORECASE)

        # Remove bit/array dimensions: [N:M], [N], <N>, etc.
        prt_lst = re.sub(r"\s*[\[\<]\s*[^\]\>]*[\]\>]", "", prt_lst)

        # Extract identifiers (alphanumeric, underscore, dollar). Deduplicate
        # while preserving first-seen order: a Verilog `ifdef ... `else ... `endif
        # block may declare the same port name in both branches; we want it once.
        seen = set()
        ids = []
        for tok in re.findall(r"\b[a-zA-Z_$][a-zA-Z0-9_$]*\b", prt_lst):
            if tok not in seen:
                seen.add(tok)
                ids.append(tok)

        return ids if ids else None
    except Exception as e:
        _logger.debug(f"Verilog single-file peek failed for {cell}: {e}")
        return None


def _peek_vrlog_dr(drpth: str, cell: str) -> list[str] | None:
    """Glob .v/.sv/.va/.vams/.vha/.psv files; on first peek hit, return.

    Iterate in sorted order for determinism.

    Inputs:
        drpth: Directory path
        cell: Module name to find

    Outputs:
        list[str] of port names from first file matching, or None
    """
    try:
        exts = ("psv", "sv", "v", "va", "vams", "vha")
        fls = []
        for ext in exts:
            fls.extend(glob.glob(os.path.join(drpth, "**", f"*.{ext}"), recursive=True))
            fls.extend(glob.glob(os.path.join(drpth, f"*.{ext}")))

        fls = sorted(set(fls))

        for fl in fls:
            rslt = _peek_vrlog_sf(fl, cell)
            if rslt is not None:
                return rslt

        return None
    except Exception as e:
        _logger.debug(f"Verilog directory peek failed for {cell}: {e}")
        return None


def _peek_edf(flpth: str, cell: str) -> list[str] | None:
    """Mini s-expression scan for '(cell <name> ... (interface (port ...)))'.

    Extract port names. If too complex, return None (acceptable per fall-through rule).

    Inputs:
        flpth: Path to EDIF file
        cell: Cell name to find

    Outputs:
        list[str] of port names, or None if implementation too complex or not found
    """
    try:
        with open(flpth, encoding="utf-8", errors="replace") as fh:
            cnt = fh.read()

        # Simple heuristic: find '(cell <name>' and extract port names
        # This is intentionally simplified; EDIF is complex
        pat = r"\(\s*cell\s+" + re.escape(cell) + r"\b"
        m = re.search(pat, cnt, re.IGNORECASE)
        if not m:
            return None

        # For now, return None (acceptable per blueprint rule 6)
        # A full implementation would parse the s-expression tree
        _logger.debug(f"EDIF peek for {cell}: not implemented (falling through to full parse)")
        return None
    except Exception as e:
        _logger.debug(f"EDIF peek failed for {cell}: {e}")
        return None


def _collect_spef_cells_from_handle(fh: Any) -> list[tuple[str, list[str]]]:
    """Collect *DESIGN and *PORTS declarations from SPEF file handle.

    SPEF format: *DESIGN <design_name>, *PORTS line(s) with port names.
    Returns single entry for the design with its port list.

    Inputs:
        fh: Open file handle (text mode, already decompressed if .gz)

    Outputs:
        List with single (design_name, [port1, port2, ...]) tuple, or empty
        list if not found or parse fails
    """
    design_name = ""
    ports = []

    for ln in fh:
        ln = ln.rstrip()

        # Detect *DESIGN directive
        if ln.startswith("*DESIGN"):
            pts = ln.split()
            if len(pts) >= 2:
                design_name = pts[1]
                break

    if not design_name:
        return []

    # Collect *PORTS lines
    name_map = {}
    for ln in fh:
        ln = ln.rstrip()

        # Stop at next major section
        if ln.startswith("*"):
            if ln.startswith("*PORTS"):
                continue
            elif ln.startswith("*NAME_MAP"):
                # Switch to collecting NAME_MAP entries (before PORTS)
                for nm_ln in fh:
                    nm_ln = nm_ln.rstrip()
                    if nm_ln.startswith("*"):
                        # End of NAME_MAP, fall through to process this line
                        ln = nm_ln
                        break
                    m = re.match(r"^\*(\d+)\s+(.+)", nm_ln)
                    if m:
                        name_map[f"*{m.group(1)}"] = m.group(2)
                # Continue with current ln (which is a directive line)
                if not ln.startswith("*PORTS"):
                    break
                continue
            else:
                # End of PORTS section
                break

        # Extract port names from *PORTS content
        if ln and not ln.startswith("*"):
            # Port lines: *N I/O/B or space-separated names
            m = re.match(r"^\*(\d+)\s+[IOB]", ln)
            if m:
                # Section-format PORTS with *N I/O/B; resolve via name_map
                alias = f"*{m.group(1)}"
                if alias in name_map:
                    ports.append(name_map[alias])
            else:
                # Traditional inline port names: space-separated
                pts = ln.split()
                ports.extend(pts)

    # Return design as single subckt-like entry
    if design_name:
        return [(design_name, ports)]
    return []


def peek_spf_subckts(spf_pth: str) -> list[tuple[str, list[str]]]:
    """Cheap scan of SPF/DSPF/SPEF file to discover all cell definitions.

    Auto-detects format (SPF/DSPF use .SUBCKT, SPEF uses *DESIGN/*PORTS).
    Read only enough to find definitions. No series-R reduction, no instance
    parsing, no body capture. Handles .gz transparently.

    Inputs:
        spf_pth: Absolute path to .spf, .dspf, .spef file (optional .gz suffix)

    Outputs:
        List of (cell_name, [pin1, pin2, ...]) tuples. One per cell definition.
        Empty list on parse error.
    """
    try:
        # Open via gzip if .gz, else regular open with error handling
        if spf_pth.endswith(".gz"):
            fh = gzip.open(spf_pth, "rt", errors="replace")
        else:
            fh = open(spf_pth, encoding="utf-8", errors="replace")

        try:
            # Auto-detect format: peek first few lines
            # SPEF files have *DESIGN, SPF/DSPF files have .SUBCKT
            fh_peek_line = fh.readline()
            is_spef = "*DESIGN" in fh_peek_line or "*design" in fh_peek_line.lower()

            # Reset file position for actual parse
            fh.seek(0)

            if is_spef:
                # SPEF format: *DESIGN + *PORTS
                rslts = _collect_spef_cells_from_handle(fh)
            else:
                # SPF/DSPF format: .SUBCKT
                rslts = _collect_subckt_pins_from_handle(fh, None)

            # Convert None to empty list (for consistency with API contract)
            return rslts if rslts is not None else []
        finally:
            fh.close()
    except Exception as e:
        _logger.debug(f"SPF/SPEF peek failed for {spf_pth}: {e}")
        return []


def _extract_spce_nets_from_body(fh: Any, cell: str) -> list[str] | None:
    """Extract internal net names from SPICE/CDL subckt body.

    Scans from .SUBCKT line to .ENDS, tokenizing instance connection lists
    and net declarations. Liberal extraction: identifier-shaped tokens that
    are not the instance name or model type. Joins continuation lines ('+')
    before tokenizing to capture nets across multi-line instances.

    Inputs:
        fh: Open file handle (text mode)
        cell: Cell name to search for

    Outputs:
        list[str] of candidate net names, or None if cell not found
    """
    in_subckt = False
    nets = set()
    lines = [ln.rstrip() for ln in fh]  # Read entire file into list for indexing
    i = 0

    while i < len(lines):
        ln = lines[i]
        i += 1

        # Look for .SUBCKT line
        m = re.match(r"^\s*\.SUBCKT\s+(\S+)\s+(.*)", ln, re.IGNORECASE)
        if m:
            sbckt_nm = m.group(1)
            if sbckt_nm.lower() != cell.lower():
                continue
            in_subckt = True
            continue

        if not in_subckt:
            continue

        # Check for .ENDS
        if re.match(r"^\s*\.ENDS", ln, re.IGNORECASE):
            break

        # Skip comments and empty lines
        if ln.lstrip().startswith("*") or not ln.strip():
            continue

        # Parse instance line: [X<name> <net1> <net2> ... <model> [params]]
        # Skip plain continuation lines; they're collected below
        if ln.lstrip().startswith("+"):
            continue

        # Collect continuation lines for this instance
        full_ln = ln
        while i < len(lines) and lines[i].lstrip().startswith("+"):
            cn_rst = lines[i].lstrip()[1:].strip()
            full_ln = full_ln + " " + cn_rst
            i += 1

        # Tokenize the joined line
        tks = full_ln.split()
        if not tks or tks[0].lower().startswith("*"):
            continue

        # Instance lines start with X (or other device letters in SPICE)
        # Skip purely declarative lines (.param, etc.)
        if tks[0].lower().startswith("."):
            continue

        # For device instances (X..., R..., M..., etc.), collect net tokens
        # Strategy: skip the first token (instance name) and the last (or last few for model),
        # then collect identifiers. Liberal approach: accept all identifier-shaped tokens.
        if len(tks) > 2:
            # Skip first token (instance name) and last token (usually model name)
            for tk in tks[1:-1]:
                # Skip tokens with '=' (parameters)
                if "=" in tk:
                    continue
                # Skip numeric literals
                if re.match(r"^\d+", tk):
                    continue
                # Accept identifier-shaped tokens (alphanumeric + underscore/brackets)
                if re.match(r"^[a-zA-Z_][a-zA-Z0-9_\[\]]*$", tk):
                    nets.add(tk)

    return list(nets) if nets and in_subckt else None


def peek_nets(flpth: str, cell: str, fmt: str | None = None) -> list[str] | None:
    """Fast pre-scan to discover internal net names in a cell WITHOUT full parse.

    Scans the subckt body for identifier-shaped tokens in instance connections
    and declarations. Liberal extraction: false positives accepted; full parse
    validates downstream.

    Inputs:
        flpth: File or directory path
        cell: Cell/module name to find
        fmt: Optional explicit format hint

    Outputs:
        list[str] of candidate internal net names if cell found, None otherwise
    """
    # Validate path exists
    if not os.path.exists(flpth):
        raise FileNotFoundError(f"Path not found: {flpth}")

    # JSON cache: not used for nets (peek_nets always scans)
    # Verilog directory
    if os.path.isdir(flpth):
        return None  # Simplified: skip directory handling for now

    # Single file: detect format or use hint
    if fmt is None:
        fmt = detect_format([flpth])

    # Dispatch to format-specific peek
    if fmt == "verilog":
        return None  # Verilog internal nets not easily peeked; full parse needed
    elif fmt == "spectre":
        return None  # Spectre internal nets not easily peeked
    elif fmt == "spf":
        return _extract_spce_nets_from_body_spf(flpth, cell)
    elif fmt == "edif":
        return None
    else:  # spice, cdl, unknown -> treat as spice-family
        try:
            if flpth.endswith(".gz"):
                fh = gzip.open(flpth, "rt", errors="replace")
            else:
                fh = open(flpth, encoding="utf-8", errors="replace")
            try:
                return _extract_spce_nets_from_body(fh, cell)
            finally:
                fh.close()
        except Exception as e:
            _logger.debug(f"SPICE-family peek_nets failed for {cell}: {e}")
            return None


def _extract_spce_nets_from_body_spf(flpth: str, cell: str) -> list[str] | None:
    """Extract internal net names from SPF/DSPF file body."""
    try:
        if flpth.endswith(".gz"):
            fh = gzip.open(flpth, "rt", errors="replace")
        else:
            fh = open(flpth, encoding="utf-8", errors="replace")
        try:
            return _extract_spce_nets_from_body(fh, cell)
        finally:
            fh.close()
    except Exception as e:
        _logger.debug(f"SPF peek_nets failed for {cell}: {e}")
        return None
