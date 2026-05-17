"""Synthesis of virtual SubcktDef for SPICE primitive instances."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import TYPE_CHECKING

from netlist_tracer._logging import get_logger
from netlist_tracer.model import SubcktDef

if TYPE_CHECKING:
    from netlist_tracer.model import Instance
    from netlist_tracer.parser import NetlistParser

_logger = get_logger(__name__)

################################################################################
# SECTION: Primitive Synthesis Configuration
# Description: Policy for synthesizing SubcktDefs for primitive device types.
################################################################################

KNOWN_PASSIVE_PREFIXES = frozenset({"R", "L", "C", "K", "V", "I"})
"""
Skip-list of device type prefixes that MUST remain unsynthesized.

These types have hardcoded lateral-walk behavior in tracer.py:
  - R, L → _LATERAL_WALK_THRU_TYPES (galvanic thru)
  - C, K → _LATERAL_SKIP_TYPES (parasitic skip)
  - V, I → fall to lateral-walk endpoint emission

Their SubcktDef MUST be None so the tracer's BFS correctly detects walk-thru
or skip behavior. Synthesizing a SubcktDef for these types would break the
tracer's control flow.

All other device types (M, D, Q, J, X, and custom models) are candidates for
synthesis unless they already have an explicit SubcktDef.
"""


################################################################################
# SECTION: Primitive Pin Label Factories
# Description: Generate pin labels based on terminal count.
################################################################################


def _leaf_first_char(inst_name: str) -> str:
    """
    Extract first character of leaf name (after last / or .).

    For hierarchical flat-SPF instance names like 'Xtop/Isub/M1', splits
    on both dividers and takes the leaf segment (last component), then returns
    its first character in uppercase.

    Inputs:
        inst_name: Full instance name from parser.Instance

    Outputs:
        str — uppercase first character of leaf name, or empty string if name is empty/degenerate
    """
    if not inst_name:
        return ""
    leaf = re.split(r"[/.]", inst_name)[-1]
    return leaf[0].upper() if leaf else ""


def _pins_by_terminal_count(n: int) -> list[str]:
    """
    Default pin labels by terminal count for SPICE primitives.

    When SPF :pin annotations are absent (pure SPICE files), fall back to
    SPICE conventions: 2-terminal devices use diode-like pins (A, K);
    3-terminal FETs use (D, G, S); 4-terminal FETs add bulk (D, G, S, B);
    higher counts fall back to positional labeling (1, 2, ..., N).

    Inputs:
        n: Number of nets (terminals) on the instance

    Outputs:
        list[str] — pin labels in canonical order
    """
    if n == 2:
        return ["A", "K"]              # diode-like
    if n == 3:
        return ["D", "G", "S"]         # 3-term FET
    if n == 4:
        return ["D", "G", "S", "B"]    # 4-term FET
    return [str(i) for i in range(1, n + 1)]  # positional fallback


def _derive_pins_from_instances(insts: list[Instance], net_count: int) -> list[str]:
    """
    Derive pin labels for a cell_type by scanning SPF :pin annotations
    on its instances' connections. Falls back to terminal-count based labels
    when annotations are insufficient.

    AC36: For each instance:
      - Compute the stripped name (strip leading 'X' if present)
      - For each position p in inst.nets:
          - If net at position p starts with '<stripped_name>:' or '<inst.name>:',
            extract the suffix as the pin label at position p

    Aggregate across instances: for each position, take the most common
    extracted label (or first-seen on tie).

    If we have extracted labels for ALL positions, use them directly.
    If we have extracted labels for SOME positions but not all, use the
    extracted labels for annotated positions and terminal-count labels for
    unannotated positions (mixing extraction with fallback).
    If we have NO annotations, fall back to terminal-count labels (preserving
    SPICE convention for files without SPF markup).

    Inputs:
        insts: List of Instance objects of the same cell_type
        net_count: Number of nets (terminals) for this cell_type

    Outputs:
        list[str] — pin labels in canonical order, derived from annotations or fallback
    """
    pos_labels: dict[int, Counter[str]] = defaultdict(Counter)

    for inst in insts:
        stripped = inst.name[1:] if inst.name.startswith("X") else inst.name
        for p, net in enumerate(inst.nets):
            if p >= net_count:
                break
            # Try both stripped and full name as prefix
            for prefix in (stripped, inst.name):
                if net.startswith(prefix + ":"):
                    label = net[len(prefix) + 1 :]
                    if label and not label.isdigit():  # avoid subnode numeric suffixes
                        pos_labels[p][label] += 1
                        break

    # Check if we have ANY annotations across ANY position
    has_any_annotation = len(pos_labels) > 0

    if not has_any_annotation:
        # No :pin annotations found anywhere — use terminal-count fallback
        return _pins_by_terminal_count(net_count)

    # AC36: Build final pin list with extracted labels where available.
    # For unannotated positions, use terminal-count fallback (preserves real pin names)
    terminal_count_labels = _pins_by_terminal_count(net_count)
    pins = []
    for p in range(net_count):
        if pos_labels[p]:
            # most common label at this position (deterministic tie-break: first seen)
            most_common = pos_labels[p].most_common(1)[0][0]
            pins.append(most_common)
        else:
            # Use terminal-count fallback for unannotated positions
            pins.append(terminal_count_labels[p])

    return pins


################################################################################
# SECTION: Primitive Synthesis
# Description: Post-parse pass to synthesize SubcktDefs for SPICE primitives.
################################################################################


def synthesize_primitive_subckts(parser: NetlistParser) -> int:
    """
    Post-parse pass: scan all instances and synthesize virtual SubcktDefs for
    SPICE primitives whose cell_types lack an explicit .SUBCKT.

    AC34: Inverted policy using skip-list. For each instance whose cell_type
    has no existing SubcktDef:
      - Extract the leaf instance name (split on / or ., take last segment)
      - Take the first character (uppercase) — this is the device prefix
      - If prefix is in KNOWN_PASSIVE_PREFIXES {R, L, C, K, V, I}, skip
        (these require SubcktDef=None for lateral-walk behavior in the tracer)
      - Otherwise, synthesize a SubcktDef with pins from AC36 extraction

    AC36: Pin labels derived from SPF instance connection annotations.
    For each candidate cell_type, scan all its instances. For each connection
    net at position p: if the net matches `<inst_name_or_inst_without_X>:<pin>`
    form, the suffix is THIS cell_type's pin label at position p. If multiple
    instances annotate position p with different labels, take the most common.
    For positions with NO annotations across any instance, use positional
    fallback ('1', '2', ...).

    This policy correctly handles:
      - Instance names starting with X (SPICE subckt convention)
      - Hierarchical flat-SPF names like Xtop/Isub/M1 (extracts leaf M1)
      - Custom model names that don't follow SPICE letter conventions
      - Real pin names from :pin SPF annotations (D/G/S/B for MOSFETs, A/K for diodes)

    Inputs:
        parser: NetlistParser after format-specific parse; mutated in-place

    Outputs:
        int — number of synthesized SubcktDefs added to parser.subckts

    Notes:
        - Synthesis is strictly non-destructive: existing SubcktDefs are never
          overwritten (preserves user-provided wrapper subckts).
        - For width disambiguation (e.g. 3-term vs 4-term), uses the FIRST
          encountered instance's net count as canonical. Subsequent instances
          with different widths log a warning and use the maximum width seen.
        - Synthesized SubcktDefs have empty body (no internal instances, aliases,
          or params) — pin_to_pos only.
        - Call this function from NetlistParser.__init__ immediately after
          format-specific parse step, BEFORE cache dump.
    """
    synth_cnt = 0
    seen_widths: dict[str, int] = {}

    # Scan all instances to find candidates for synthesis
    for _parent_cell, insts in parser.instances_by_parent.items():
        for inst in insts:
            cell_type = inst.cell_type
            inst_name = inst.name
            net_count = len(inst.nets)

            # Skip if already defined
            if cell_type in parser.subckts:
                continue

            # AC34: Check if cell_type itself is a known-passive (e.g. cell_type="R" or "C")
            # These require SubcktDef=None for lateral-walk behavior in the tracer
            cell_type_pfx = cell_type[0].upper() if cell_type else ""
            if cell_type_pfx in KNOWN_PASSIVE_PREFIXES:
                continue

            # Extract leaf name and prefix from instance (handles hierarchical names)
            leaf_pfx = _leaf_first_char(inst_name)
            if not leaf_pfx:
                continue

            # Skip if instance leaf name starts with a known-passive prefix
            # (e.g. instance name "R1" even if cell_type is a custom name)
            if leaf_pfx in KNOWN_PASSIVE_PREFIXES:
                continue

            # Check for width mismatch (use max width across instances of same cell_type)
            if cell_type in seen_widths:
                prev_width = seen_widths[cell_type]
                if prev_width != net_count:
                    _logger.warning(
                        f"Instance '{inst_name}' of cell_type '{cell_type}' has "
                        f"inconsistent net count: first instance had {prev_width}, "
                        f"current has {net_count}. Using maximum width."
                    )
                    net_count = max(prev_width, net_count)
            else:
                seen_widths[cell_type] = net_count

            # AC36: Collect all instances of this cell_type for pin label extraction
            candidate_insts = parser.instances_by_celltype.get(cell_type, [])
            if not candidate_insts:
                candidate_insts = [inst]

            # Derive pin labels from SPF :pin annotations on instances
            pins = _derive_pins_from_instances(candidate_insts, net_count)

            # Create virtual SubcktDef
            synth_def = SubcktDef(name=cell_type, pins=pins)
            parser.subckts[cell_type] = synth_def
            synth_cnt += 1
            _logger.debug(
                f"Synthesized SubcktDef '{cell_type}' with pins={pins} "
                f"(instances={len(candidate_insts)})"
            )

    return synth_cnt
