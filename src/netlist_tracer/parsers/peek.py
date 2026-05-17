"""Fast pre-scan to peek at cell pin list without full parse."""

from __future__ import annotations

import glob
import gzip
import json
import os
import re

from netlist_tracer._logging import get_logger
from netlist_tracer.parsers.detect import detect_format

_logger = get_logger(__name__)


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
            for ln in fh:
                ln = ln.rstrip()

                # Look for .SUBCKT line (case-insensitive)
                m = re.match(
                    r"^\s*\.SUBCKT\s+(\S+)\s+(.*)",
                    ln,
                    re.IGNORECASE,
                )
                if not m:
                    continue

                sbckt_nm = m.group(1)
                if sbckt_nm.lower() != cell.lower():
                    continue

                # Found matching .SUBCKT; collect pins from this line and continuations
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

                return pns if pns else None
        finally:
            fh.close()

        return None
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
    """Find 'module <cell> [#(...)] (port_list);' in a single file.

    Handle multi-line port lists and direction keywords.

    Inputs:
        flpth: Path to Verilog file
        cell: Module name to find

    Outputs:
        list[str] of port names (excluding direction keywords), or None
    """
    try:
        with open(flpth, encoding="utf-8", errors="replace") as fh:
            cnt = fh.read()

        # Find 'module <cell>' boundary (case-sensitive)
        pat = r"module\s+" + re.escape(cell) + r"\s*(?:#\s*\(|[\(\[])"
        m = re.search(pat, cnt)
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

        # Handle parameter block '#(...)' if present
        if m.group().find("#") != -1:
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


def peek_spf_subckts(spf_pth: str) -> list[tuple[str, list[str]]]:
    """Cheap scan of an SPF/DSPF file to discover all cell definitions.

    Read only enough to find every `.SUBCKT <name> <pins...>` declaration.
    No series-R reduction, no instance parsing, no body capture. Handles
    .gz transparently.

    Inputs:
        spf_pth: Absolute path to .spf, .spef, .dspf file (optional .gz suffix)

    Outputs:
        List of (cell_name, [pin1, pin2, ...]) tuples. One per .SUBCKT declaration.
        Empty list on parse error.
    """
    try:
        # Open via gzip if .gz, else regular open with error handling
        if spf_pth.endswith(".gz"):
            fh = gzip.open(spf_pth, "rt", errors="replace")
        else:
            fh = open(spf_pth, encoding="utf-8", errors="replace")

        try:
            rslts = []
            for ln in fh:
                ln = ln.rstrip()

                # Look for .SUBCKT line (case-insensitive)
                m = re.match(
                    r"^\s*\.SUBCKT\s+(\S+)\s+(.*)",
                    ln,
                    re.IGNORECASE,
                )
                if not m:
                    continue

                sbckt_nm = m.group(1)
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

                rslts.append((sbckt_nm, pns if pns else []))

            return rslts
        finally:
            fh.close()
    except Exception as e:
        _logger.debug(f"SPF peek failed for {spf_pth}: {e}")
        return []
