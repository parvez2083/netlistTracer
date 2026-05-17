from __future__ import annotations

import os
import re
from typing import Optional

from netlist_tracer._logging import get_logger
from netlist_tracer.model import Instance, SubcktDef
from netlist_tracer.parsers.includes import expand_includes

_logger = get_logger(__name__)


################################################################################
# SECTION: Inline Comment Stripping
# Description: Remove inline comments (unquoted ; or $) while preserving
# quoted strings and continuation line integrity.
################################################################################


def _strip_inline_comments(line: str) -> str:
    """
    Strip the first unquoted ; or $ character and all following text.

    Preserves quoted strings (single or double quotes) so that quoted
    semicolons/dollar signs are kept. Does not handle escape sequences.

    Inputs:
        line: Input line string

    Outputs:
        str - Line with comment stripped, or original if no comment found
    """
    in_single_quote = False
    in_double_quote = False

    for i, char in enumerate(line):
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif (char == ";" or char == "$") and not in_single_quote and not in_double_quote:
            return line[:i]

    return line


################################################################################
# SECTION: Continuation Line Merging
# Description: Pre-pass to merge + continuation lines across * comment lines,
# before main parsing.
################################################################################


def _merge_continuation_lines(lines: list[str]) -> list[str]:
    """
    Merge + continuation lines across * comment lines.

    A line starting with + is a continuation and should be merged with
    preceding non-comment lines. Comment lines (* in first column) are
    preserved in the merged output but do not break continuation.

    Inputs:
        lines: List of input lines

    Outputs:
        list[str] - Lines with continuations merged
    """
    if not lines:
        return []

    result = []
    i = 0

    while i < len(lines):
        line = lines[i]
        i += 1

        # If this is a comment line, just add it and continue
        stripped = line.strip()
        if stripped.startswith("*"):
            result.append(line)
            continue

        # Otherwise, accumulate this line and any following continuations
        # Skip * comment lines but continue looking for + continuations
        accumulated = line
        pending_comments = []
        while i < len(lines):
            stripped_next = lines[i].lstrip()
            if stripped_next.startswith("*"):
                # Record comment but don't break accumulation
                pending_comments.append(lines[i])
                i += 1
            elif stripped_next.startswith("+"):
                # Merge the continuation (removing leading +)
                accumulated += " " + stripped_next[1:].strip()
                i += 1
                # Comments absorbed into continuation; clear pending
                pending_comments = []
            else:
                break

        result.append(accumulated)
        result.extend(pending_comments)

    return result


################################################################################
# SECTION: Controlled Source Parsing
# Description: Parse B/E/F/G/H elements (behavioral and controlled sources)
# with proper pin arity and parameter extraction.
################################################################################


def _parse_spice_element(text: str, parent_cell: str) -> Optional[Instance]:
    """
    Parse SPICE behavioral and controlled source elements (B/E/F/G/H).

    Element types:
      B - Behavioral source: B<name> <out+> <out-> <expr>
      E - VCVS: E<name> <out+> <out-> <in+> <in->
      G - VCCS: G<name> <out+> <out-> <in+> <in->
      F - CCCS: F<name> <out+> <out-> <Vctrl>
      H - CCVS: H<name> <out+> <out-> <Vctrl>

    Inputs:
        text: Element line text (already stripped/normalized)
        parent_cell: Parent subcircuit name

    Outputs:
        Instance - With cell_type in {B_BSRC, E_VCVS, G_VCCS, F_CCCS, H_CCVS},
                   F/H store Vctrl in params['_vctrl'], or None if invalid
    """
    text = re.sub(r"\s+", " ", text).strip()

    tokens = text.split()
    if not tokens:
        return None

    elem_name = tokens[0]
    elem_type_char = elem_name[0].upper() if elem_name else None

    if elem_type_char not in ("B", "E", "F", "G", "H"):
        return None

    if elem_type_char == "B":
        # B: 2 nets + expression (rest of line is expr)
        if len(tokens) < 4:
            return None
        nets = [tokens[1], tokens[2]]
        expr = " ".join(tokens[3:])
        return Instance(
            name=elem_name,
            cell_type="B_BSRC",
            nets=nets,
            parent_cell=parent_cell,
            params={"_expr": expr} if expr else {},
        )

    elif elem_type_char == "E":
        # E: 4 nets (out+, out-, in+, in-)
        if len(tokens) < 5:
            return None
        nets = tokens[1:5]
        # Optional gain parameter
        params = {}
        if len(tokens) > 5:
            try:
                gain = float(tokens[5])
                params["_gain"] = str(gain)
            except ValueError:
                pass
        return Instance(
            name=elem_name,
            cell_type="E_VCVS",
            nets=nets,
            parent_cell=parent_cell,
            params=params if params else {},
        )

    elif elem_type_char == "G":
        # G: 4 nets (out+, out-, in+, in-)
        if len(tokens) < 5:
            return None
        nets = tokens[1:5]
        # Optional transconductance parameter
        params = {}
        if len(tokens) > 5:
            try:
                gm = float(tokens[5])
                params["_gm"] = str(gm)
            except ValueError:
                pass
        return Instance(
            name=elem_name,
            cell_type="G_VCCS",
            nets=nets,
            parent_cell=parent_cell,
            params=params if params else {},
        )

    elif elem_type_char == "F":
        # F: 2 nets (out+, out-) + Vctrl (voltage source name)
        if len(tokens) < 4:
            return None
        nets = [tokens[1], tokens[2]]
        vctrl = tokens[3]
        # Optional gain
        params = {"_vctrl": vctrl}
        if len(tokens) > 4:
            try:
                gain = float(tokens[4])
                params["_gain"] = str(gain)
            except ValueError:
                pass
        return Instance(
            name=elem_name,
            cell_type="F_CCCS",
            nets=nets,
            parent_cell=parent_cell,
            params=params,
        )

    elif elem_type_char == "H":
        # H: 2 nets (out+, out-) + Vctrl (voltage source name)
        if len(tokens) < 4:
            return None
        nets = [tokens[1], tokens[2]]
        vctrl = tokens[3]
        # Optional resistance
        params = {"_vctrl": vctrl}
        if len(tokens) > 4:
            try:
                r = float(tokens[4])
                params["_r"] = str(r)
            except ValueError:
                pass
        return Instance(
            name=elem_name,
            cell_type="H_CCVS",
            nets=nets,
            parent_cell=parent_cell,
            params=params,
        )

    return None


################################################################################
# SECTION: Coupled Inductor Parsing
# Description: Parse K coupling directives (.coupling or K element syntax).
################################################################################


def _parse_spice_k_element(text: str, parent_cell: str) -> Optional[Instance]:
    """
    Parse SPICE K (coupled inductor) directive.

    Format: K<name> <L1> <L2> <k_coeff>
    Creates an Instance with cell_type='K_COUPLED', nets holding inductor
    names [L1, L2], and params['_k_coeff'] holding the coupling coefficient.

    Inputs:
        text: K directive line (already stripped/normalized)
        parent_cell: Parent subcircuit name

    Outputs:
        Instance - With cell_type='K_COUPLED' and inductor names in nets,
                   or None if invalid
    """
    text = re.sub(r"\s+", " ", text).strip()

    tokens = text.split()
    if len(tokens) < 4:
        return None

    k_name = tokens[0]
    if not k_name[0].upper() == "K":
        return None

    l1 = tokens[1]
    l2 = tokens[2]
    k_coeff = tokens[3]

    return Instance(
        name=k_name,
        cell_type="K_COUPLED",
        nets=[l1, l2],
        parent_cell=parent_cell,
        params={"_k_coeff": k_coeff},
    )


################################################################################
# SECTION: Global Directive Parsing
# Description: Parse .global directives to extract net names.
################################################################################


def _parse_spice_global_directive(line: str) -> list[str]:
    """
    Parse .global directive to extract global net names.

    Format: .global net1 net2 net3 ...
    Returns list of net names following .global.

    Inputs:
        line: Directive line

    Outputs:
        list[str] - List of global net names, empty list if not a .global
    """
    stripped = line.strip()
    if not stripped.upper().startswith(".GLOBAL"):
        return []

    # Remove .global prefix and split remaining tokens
    rest = stripped[7:].strip()
    if not rest:
        return []

    # Split on whitespace, filter out empty strings
    nets = [t.strip() for t in rest.split() if t.strip()]
    return nets


def parse_spice(
    filename: str, include_paths: Optional[list[str]] = None
) -> tuple[dict[str, SubcktDef], list[Instance], list[str]]:
    """Parse SPICE/CDL netlist file.

    Args:
        filename: Path to SPICE or CDL netlist file.
        include_paths: Optional list of additional search directories for includes.

    Returns:
        Tuple of (subckts_dict, instances_list, global_nets) where subckts_dict
        maps cell names to SubcktDef objects, instances_list is a list of Instance
        objects, and global_nets is a list of net names from .global directives.
    """
    # Expand includes first (note: spice.py doesn't use ahdl or spf sidechannels)
    expanded_lines, _, _ = expand_includes(filename, "spice", include_paths)
    lines = [line_text + "\n" for line_text, _, _ in expanded_lines]

    # Pre-pass: merge continuation lines
    lines = [line.rstrip() for line in lines]
    lines = _merge_continuation_lines(lines)

    subckts: dict[str, SubcktDef] = {}
    instances: list[Instance] = []
    global_nets: list[str] = []

    current_subckt: Optional[str] = None
    subckt_content: list[str] = []
    top_level_lines: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        line = _strip_inline_comments(line)

        subckt_match = re.match(r"^\.subckt\s+(\S+)\s*(.*)", line, re.IGNORECASE)
        if subckt_match:
            cell_name = subckt_match.group(1)
            pin_text = subckt_match.group(2)

            pin_text = re.sub(r"\+", " ", pin_text)
            pins = [p for p in pin_text.split() if p and not p.startswith("*") and "=" not in p]

            current_subckt = cell_name
            subckt_content = []
            subckts[cell_name] = SubcktDef(name=cell_name, pins=pins)

        elif re.match(r"^\.ends", line, re.IGNORECASE):
            if current_subckt:
                _parse_spice_instances(current_subckt, subckt_content, instances)
            current_subckt = None
            subckt_content = []

        elif re.match(r"^\.global", line, re.IGNORECASE):
            # Capture .global directive nets
            global_nets.extend(_parse_spice_global_directive(line))

        elif current_subckt:
            subckt_content.append(line)

        else:
            # Top-level: capture X-instance lines
            stripped = line.strip()
            if stripped and not stripped.startswith("*") and not stripped.startswith("."):
                if stripped[0].upper() == "X":
                    top_level_lines.append(line)

        i += 1

    # Synthesize flat-deck top if there are top-level instances
    _synthesize_flat_top(filename, top_level_lines, subckts, instances)

    return subckts, instances, global_nets


def _synthesize_flat_top(
    filename: str,
    top_level_lines: list[str],
    subckts: dict[str, SubcktDef],
    instances: list[Instance],
) -> None:
    """Synthesize a virtual top-level subcircuit for flat-deck testbenches.

    When a SPICE netlist has instance lines at file scope (not inside any .subckt block),
    this function creates a synthetic SubcktDef and parses those instances into it.
    The synthetic top is named __<filename_stem>__.

    Args:
        filename: Source netlist file path (used to derive the synthetic top name).
        top_level_lines: List of captured top-level instance lines (including continuations).
        subckts: Dictionary of SubcktDef objects to mutate (synthetic top is added).
        instances: List of Instance objects to mutate (top-level instances are appended).
    """
    if not top_level_lines:
        return

    # Derive synthetic top name: __<basename_no_ext>__
    stem = os.path.splitext(os.path.basename(filename))[0]
    top_name = f"__{stem}__"

    # Handle collision: if top_name already exists, append _synth suffix
    if top_name in subckts:
        _logger.warning(
            f"Synthetic top name '{top_name}' collides with existing cell; using '{top_name}_synth' instead"
        )
        top_name = f"{top_name}_synth"

    # Create synthetic top SubcktDef with empty pins
    subckts[top_name] = SubcktDef(name=top_name, pins=[])

    # Parse top-level instances into the synthetic top
    _parse_spice_instances(top_name, top_level_lines, instances)


def _parse_spice_instances(parent_cell: str, content: list[str], instances: list[Instance]) -> None:
    """Parse instances within a subckt body.

    Handles X-instances, B/E/F/G/H elements, and K couplings.

    Args:
        parent_cell: Name of the parent subcircuit.
        content: List of content lines from the subcircuit body.
        instances: List to append parsed Instance objects to.
    """
    i = 0
    while i < len(content):
        line = content[i]
        line = _strip_inline_comments(line)
        stripped = line.strip()

        if not stripped or stripped.startswith("*"):
            i += 1
            continue

        # X-instance (hierarchical)
        if stripped.upper().startswith("X"):
            instance = _parse_spice_instance(stripped, parent_cell)
            if instance:
                instances.append(instance)

        # B element (behavioral source)
        elif stripped.upper().startswith("B"):
            instance = _parse_spice_element(stripped, parent_cell)
            if instance:
                instances.append(instance)

        # E element (VCVS)
        elif stripped.upper().startswith("E"):
            instance = _parse_spice_element(stripped, parent_cell)
            if instance:
                instances.append(instance)

        # F element (CCCS)
        elif stripped.upper().startswith("F"):
            instance = _parse_spice_element(stripped, parent_cell)
            if instance:
                instances.append(instance)

        # G element (VCCS)
        elif stripped.upper().startswith("G"):
            instance = _parse_spice_element(stripped, parent_cell)
            if instance:
                instances.append(instance)

        # H element (CCVS)
        elif stripped.upper().startswith("H"):
            instance = _parse_spice_element(stripped, parent_cell)
            if instance:
                instances.append(instance)

        # K element (coupled inductors)
        elif stripped.upper().startswith("K"):
            instance = _parse_spice_k_element(stripped, parent_cell)
            if instance:
                instances.append(instance)

        i += 1


def _parse_spice_instance(text: str, parent_cell: str) -> Optional[Instance]:
    """Parse a single SPICE/CDL instance line.

    Args:
        text: Instance line text.
        parent_cell: Name of the parent subcircuit.

    Returns:
        Instance object or None if parsing fails.
    """
    text = re.sub(r"\s+", " ", text).strip()

    tokens = text.split()
    if len(tokens) < 2:
        return None
    if "=" in tokens[0]:
        return None

    inst_name = tokens[0]

    # CDL format: Xinst net1 net2 ... / celltype
    # SPICE format: Xinst net1 net2 ... celltype [params]
    if "/" in tokens:
        slash_idx = tokens.index("/")
        nets = tokens[1:slash_idx]
        cell_type = tokens[slash_idx + 1] if slash_idx + 1 < len(tokens) else None
    else:
        nets = []
        cell_type = None
        for j, tok in enumerate(tokens[1:], 1):
            if "=" in tok:
                break
            if j == len(tokens) - 1:
                cell_type = tok
            else:
                nets.append(tok)
        if cell_type is None and nets:
            cell_type = nets.pop()

    if cell_type is None:
        return None

    # Capture param=value pairs
    params = {}
    for tok in tokens:
        if "=" in tok and not tok.startswith("*"):
            k, v = tok.split("=", 1)
            params[k] = v.strip("'")

    return Instance(
        name=inst_name,
        cell_type=cell_type,
        nets=nets,
        parent_cell=parent_cell,
        params=params or {},
    )
