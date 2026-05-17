from __future__ import annotations

import os
import re
from typing import Optional

from netlist_tracer._logging import get_logger
from netlist_tracer.model import Instance, SubcktDef
from netlist_tracer.parsers.includes import expand_includes
from netlist_tracer.parsers.verilog.instances import _sv_parse_file
from netlist_tracer.parsers.verilog.specialize import _sv_assemble, _sv_specialize_modules

_logger = get_logger(__name__)


def parse_spectre(
    filename: str, include_paths: Optional[list[str]] = None
) -> tuple[dict[str, SubcktDef], list[Instance]]:
    """Parse Spectre netlist file.

    Args:
        filename: Path to Spectre netlist file.
        include_paths: Optional list of additional search directories for includes.

    Returns:
        Tuple of (subckts_dict, instances_list) where subckts_dict maps cell
        names to SubcktDef objects and instances_list is a list of Instance objects.
    """
    # Expand includes and collect SPF/ahdl paths via unified resolver (AC31)
    expanded_lines, ahdl_include_paths, spf_include_paths = expand_includes(
        filename, "spectre", include_paths
    )

    # Parse SPF files collected by the unified resolver
    from netlist_tracer.parsers.spf import parse_spf

    spf_subckts: dict[str, SubcktDef] = {}
    spf_insts: list[Instance] = []
    for spf_path in spf_include_paths:
        try:
            spf_sbckts, spf_inst_list, _ = parse_spf(spf_path, include_paths=include_paths)
            spf_subckts.update(spf_sbckts)
            spf_insts.extend(spf_inst_list)
            _logger.info(f"Parsed SPF: {spf_path}")
        except Exception as e:
            _logger.warning(f"Failed to parse SPF '{spf_path}': {type(e).__name__}: {e}")
    raw_lines = [line_text + "\n" for line_text, _, _ in expanded_lines]

    subckts: dict[str, SubcktDef] = {}
    instances: list[Instance] = []

    # Join backslash-continuation lines and strip bracket escaping
    lines = []
    buf = ""
    for raw in raw_lines:
        raw = raw.rstrip()
        if buf:
            raw = raw.lstrip()
        if raw.endswith("\\"):
            buf += raw[:-1] + " "
        else:
            buf += raw
            lines.append(buf)
            buf = ""
    if buf:
        lines.append(buf)

    # Strip Spectre escape syntax: \X -> X for special chars (<, >, (, ), [, ], ,).
    lines = [
        line_item.replace("\\<", "<")
        .replace("\\>", ">")
        .replace("\\(", "(")
        .replace("\\)", ")")
        .replace("\\[", "[")
        .replace("\\]", "]")
        .replace("\\,", ",")
        for line_item in lines
    ]  # noqa: E741

    skip_prefixes = (
        "simulator",
        "global",
        "parameters",
        "real",
        "model",
        "ends",
        "ahdl_include",
        "saveOptions",
        "save",
    )

    # First pass: collect subckt definitions and body lines
    subckt_bodies: dict[str, list[str]] = {}  # cell_name -> list of body lines
    current_subckt = None
    top_level_lines = []
    # Derive synthetic top-level cell name from filename stem with double-underscore convention
    stem = os.path.splitext(os.path.basename(filename))[0]
    top_cell = f"__{stem}__"

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//"):
            continue

        subckt_match = re.match(r"^subckt\s+(\S+)\s*(.*)", stripped)
        if subckt_match:
            cell_name = subckt_match.group(1)
            pin_text = subckt_match.group(2)
            # Remove optional parentheses around pins
            pin_text = pin_text.strip("()")
            pins = [p for p in pin_text.split() if p]
            subckts[cell_name] = SubcktDef(name=cell_name, pins=pins)
            current_subckt = cell_name
            subckt_bodies[cell_name] = []
            continue

        if re.match(r"^ends\b", stripped):
            current_subckt = None
            continue

        if current_subckt:
            subckt_bodies[current_subckt].append(stripped)
        else:
            top_level_lines.append(stripped)

    # Register top-level cell if it has content (testbench)
    if top_level_lines:
        if top_cell not in subckts:
            subckts[top_cell] = SubcktDef(name=top_cell, pins=[])
        subckt_bodies[top_cell] = top_level_lines

    # Load ahdl_include'd Verilog-A modules BEFORE second pass so instance lookups resolve against them.
    if ahdl_include_paths:
        _load_ahdl_include_modules(ahdl_include_paths, subckts)

    # Second pass: parse instances in each subckt body
    for cell_name, body_lines in subckt_bodies.items():
        for line in body_lines:
            stripped = line.strip()
            if any(stripped.startswith(p) for p in skip_prefixes):
                continue
            instance = _parse_spectre_instance(stripped, cell_name, subckts)
            if instance:
                instances.append(instance)

    # Merge SPF-included content: non-empty-wins (SPF can back-annotate empty shells)
    for spf_name, spf_sub in spf_subckts.items():
        if spf_name not in subckts:
            # New subckt from SPF
            subckts[spf_name] = spf_sub
        else:
            # Collision: check if Spectre version is empty and SPF is not
            spec_has_insts = any(i.parent_cell == spf_name for i in instances)
            spf_has_insts = any(i.parent_cell == spf_name for i in spf_insts)
            if not spec_has_insts and spf_has_insts:
                # SPF has content, Spectre is empty: back-annotate
                _logger.info(f"Back-annotating '{spf_name}': empty Spectre shell -> populated SPF body")
                subckts[spf_name] = spf_sub
    instances.extend(spf_insts)

    return subckts, instances


def _load_ahdl_include_modules(ahdl_paths: list[str], subckts: dict[str, SubcktDef]) -> None:
    """Parse Verilog-A files from ahdl_include directives and merge into subckts.

    Args:
        ahdl_paths: List of resolved absolute paths to .va files.
        subckts: Existing subckt dict — mutated in place.

    Returns:
        None.
    """
    # Deduplicate preserving order
    seen = set()
    unique_paths = []
    for p in ahdl_paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    all_va_modules = []
    for va_path in unique_paths:
        try:
            # _sv_parse_file expects a tuple: (filepath, tvars, defines, define_values)
            va_modules = _sv_parse_file((va_path, {}, set(), {}))
            if va_modules:
                all_va_modules.extend(va_modules)
        except Exception as e:
            _logger.warning(
                f"Skipping ahdl_include '{va_path}' — parse error: {type(e).__name__}: {e}"
            )

    if not all_va_modules:
        return

    # Specialize and assemble Verilog-A modules
    try:
        _sv_specialize_modules(all_va_modules, {})
        va_subckts_dict, _ = _sv_assemble(all_va_modules, top=None, define_values={})
    except Exception as e:
        _logger.warning(f"Verilog-A assembly failed: {type(e).__name__}: {e}")
        return

    # Merge into subckts (Spectre wins on collision)
    for name, subckt in va_subckts_dict.items():
        if name not in subckts:
            subckts[name] = subckt
        else:
            _logger.info(
                f"ahdl_include module '{name}' collides with existing Spectre subckt; "
                f"Spectre definition takes precedence"
            )


def _parse_spectre_instance(
    text: str, parent_cell: str, subckts: dict[str, SubcktDef]
) -> Optional[Instance]:
    """Parse a single Spectre instance line: name (nets) cell_type [params].

    Args:
        text: Instance line text.
        parent_cell: Name of the parent subcircuit.
        subckts: Dictionary of known subcircuit definitions.

    Returns:
        Instance object or None if parsing fails or cell_type is not known.
    """
    m = re.match(r"^(\S+)\s*\(([^)]*)\)\s*(\S+)", text)
    if not m:
        return None
    inst_name = m.group(1)
    nets = m.group(2).split()
    cell_type = m.group(3)
    # Only register instances whose cell_type is a known subckt
    if cell_type not in subckts:
        return None
    return Instance(name=inst_name, cell_type=cell_type, nets=nets, parent_cell=parent_cell)
