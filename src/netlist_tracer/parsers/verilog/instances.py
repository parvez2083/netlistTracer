from __future__ import annotations

import re

from netlist_tracer._logging import get_logger
from netlist_tracer.parsers.verilog.preprocess import (
    _sv_preprocess,
    _sv_strip_comments,
    _sv_substitute_vars,
)
from netlist_tracer.parsers.verilog.structure import (
    _PRIMITIVES,
    _parse_interface_definition,
    _sv_extract_alias_pairs,
    _sv_extract_instances,
    _sv_extract_wire_widths_1d,
    _sv_extract_wires_2d,
    _sv_find_begin_end,
    _sv_make_port_entry,
    _sv_match_paren,
    _sv_parse_ports,
)

# Logger setup
_logger = get_logger(__name__)

# Pre-compiled patterns
_RE_MODULE = re.compile(r"\bmodule\s+(\w+)")
_RE_INTERFACE = re.compile(r"\binterface\s+(\w+)")
_RE_PRIMITIVE = re.compile(r"\bprimitive\s+(\w+)")
_RE_ENDMOD = re.compile(r"\bendmodule\b")
_RE_ENDINTERFACE = re.compile(r"\bendinterface\b")
_RE_ENDPRIMITIVE = re.compile(r"\bendprimitive\b")
_RE_DEFPARAM = re.compile(r"\bdefparam\s+([\w\.]+)\.(\w+)\s*=\s*([^;]+);")
_RE_PKG_IMPORT = re.compile(r"\bimport\s+\w+\s*::\s*(?:\*|\w+(?:\s*,\s*\w+)*)\s*;")


def _get_primitive_types() -> set:
    """Return set of built-in primitive type names."""
    return _PRIMITIVES


def _sv_collect_defparam_overrides(body: str) -> dict[str, dict[str, str]]:
    """
    Collect all defparam parameter overrides from module body.

    Parses `defparam leaf_inst_name.param_name = value;` statements.
    Returns mapping of instance leaf name to {param_name: value_str}.

    Inputs:
        body: Module body text

    Outputs:
        {leaf_inst_name: {param_name: value_str}} dict
    """
    overrides = {}
    for m in _RE_DEFPARAM.finditer(body):
        inst_path = m.group(1)
        param_name = m.group(2)
        param_value = m.group(3).strip()
        # Extract leaf instance name (last component of path)
        leaf_inst = inst_path.split(".")[-1]
        if leaf_inst not in overrides:
            overrides[leaf_inst] = {}
        # Detect path collision: same parameter redefined
        if param_name in overrides[leaf_inst]:
            existing_value = overrides[leaf_inst][param_name]
            if existing_value != param_value:
                _logger.warning(
                    f"defparam collision: {leaf_inst}.{param_name} redefined from {existing_value} to {param_value}"
                )
        # defparam wins over inline #() per Verilog LRM
        overrides[leaf_inst][param_name] = param_value
    return overrides


def _sv_synthesize_primitive_modules(instances: list, primitive_names: set) -> tuple[list, list]:
    """
    Synthesize module definitions for built-in gate primitives.

    Scans instances for primitives and creates a synthetic SubcktDef for each
    unique (type, arity) pair. Updates instance cell_type to reference synthesized name.

    Inputs:
        instances: List of {n, c, p, o} instance dicts
        primitive_names: Set of primitive type names (and, nand, or, etc.)

    Outputs:
        (updated_instances, synthesized_modules) tuple where:
        - updated_instances: instances list with cell_type patched for primitives
        - synthesized_modules: list of new module dicts for primitives
    """
    from netlist_tracer.parsers.verilog.structure import _primitive_pin_names

    # Collect unique (prim_type, arity) pairs from instances
    prim_arities = set()
    for inst in instances:
        cell_type = inst["c"]
        if cell_type in primitive_names:
            arity = len(inst["p"])
            prim_arities.add((cell_type, arity))

    # Synthesize modules for each unique pair
    syn_modules = []
    syn_cell_names = {}  # Maps (type, arity) -> synthesized cell name
    for prim_type, arity in sorted(prim_arities):
        syn_name = f"__prim_{prim_type}_{arity}__"
        syn_cell_names[(prim_type, arity)] = syn_name
        # Create port list from pin names
        pin_names = _primitive_pin_names(prim_type, arity)
        ports = [{"name": p, "bits": [p], "hi": None, "lo": None} for p in pin_names]
        syn_modules.append(
            {
                "name": syn_name,
                "ports": ports,
                "insts": [],
                "body": "",
                "param_names": [],
                "wires_2d": {},
                "aliases": [],
            }
        )

    # Update instance cell_types for primitives
    updated = []
    for inst in instances:
        if inst["c"] in primitive_names:
            arity = len(inst["p"])
            syn_name = syn_cell_names[(inst["c"], arity)]
            inst_copy = dict(inst)
            inst_copy["c"] = syn_name
            updated.append(inst_copy)
        else:
            updated.append(inst)

    return updated, syn_modules


def _strip_analog_blocks(body: str) -> str:
    """Remove analog behavioral blocks from Verilog-A module body.

    Removes both 'analog begin ... end' multi-statement blocks and single-statement
    'analog ...;' forms. Replaces removed content with equivalent-length whitespace
    to preserve character offsets.

    Inputs:
        body: Module body text

    Outputs:
        body with analog blocks replaced by whitespace
    """
    result = body
    pos = 0

    # Remove 'analog begin ... end' blocks
    while True:
        m = re.search(r"\banalog\s+begin\b", result[pos:], re.IGNORECASE)
        if not m:
            break

        analog_start = pos + m.start()
        begin_pos = pos + m.end() - 1
        analog_end = _sv_find_begin_end(result, begin_pos)

        # Extract original content for length preservation
        removed = result[analog_start:analog_end]
        # Replace with whitespace (preserve newlines for readability)
        replacement = re.sub(r"[^\n]", " ", removed)
        result = result[:analog_start] + replacement + result[analog_end:]
        pos = analog_start + len(replacement)

    # Remove single-statement 'analog ...;' forms
    pos = 0
    while True:
        m = re.search(r"\banalog\s+([^;]*?);", result[pos:], re.IGNORECASE | re.DOTALL)
        if not m:
            break

        analog_start = pos + m.start()
        analog_end = pos + m.end()

        # Extract and replace with whitespace
        removed = result[analog_start:analog_end]
        replacement = re.sub(r"[^\n]", " ", removed)
        result = result[:analog_start] + replacement + result[analog_end:]
        pos = analog_start + len(replacement)

    return result


def _sv_parse_file(args: tuple[str, dict, set, dict]) -> list:
    """Parse one file → list of module dicts (designed for Pool.map)."""
    filepath, tvars, defines, define_values = args
    try:
        with open(filepath, errors="replace") as fh:
            raw = fh.read()
    except OSError:
        return []
    if tvars:
        raw = _sv_substitute_vars(raw, tvars)
    raw = _sv_strip_comments(raw)
    raw = _sv_preprocess(raw, defines)
    raw = _strip_analog_blocks(raw)
    modules = []
    pos = 0
    while True:
        # Find next module, interface, or primitive definition
        mm = _RE_MODULE.search(raw, pos)
        im = _RE_INTERFACE.search(raw, pos)
        pm = _RE_PRIMITIVE.search(raw, pos)

        # Determine which comes first
        next_match = None
        is_interface = False
        is_primitive = False

        candidates = []
        if mm:
            candidates.append((mm.start(), mm, "module"))
        if im:
            candidates.append((im.start(), im, "interface"))
        if pm:
            candidates.append((pm.start(), pm, "primitive"))

        if candidates:
            start_pos, next_match, match_type = min(candidates, key=lambda x: x[0])
            is_interface = match_type == "interface"
            is_primitive = match_type == "primitive"
        else:
            break

        if is_primitive:
            # UDP (user-defined primitive) parsing
            prim_nm = next_match.group(1)
            paren_opn = raw.find("(", next_match.end())
            semi = raw.find(";", next_match.end())

            if paren_opn < 0 or (0 <= semi < paren_opn):
                pos = max(semi + 1, next_match.end())
                continue

            port_cls = _sv_match_paren(raw, paren_opn + 1)
            if port_cls < 0:
                pos = next_match.end()
                continue

            # Extract port list from primitive header
            ports = _sv_parse_ports(raw[paren_opn + 1 : port_cls], define_values)

            # Find endprimitive
            epm = _RE_ENDPRIMITIVE.search(raw, port_cls)
            body_end = epm.start() if epm else len(raw)
            end_pos = epm.end() if epm else len(raw)

            # UDP body is irrelevant for tracing (truth table), skip it
            # Create a SubcktDef for the UDP with empty body and no instances
            modules.append(
                {
                    "name": prim_nm,
                    "ports": ports,
                    "insts": [],
                    "body": "",
                    "param_names": [],
                    "wires_2d": {},
                    "aliases": [],
                }
            )
            pos = end_pos
        elif is_interface:
            # Interface parsing
            intrfc_nm = next_match.group(1)
            paren_opn = raw.find("(", next_match.end())
            semi = raw.find(";", next_match.end())

            # Interfaces can have optional port lists: interface name #(...) (ports) ; body endinterface
            # or just: interface name ; body endinterface
            port_cls = -1

            if paren_opn >= 0 and (semi < 0 or paren_opn < semi):
                # There's a paren before the semicolon - it's a port list
                btwn = raw[next_match.end() : paren_opn].strip()
                if btwn.startswith("#"):
                    # Has parameters: interface name #(...) (ports)
                    param_cls = _sv_match_paren(raw, paren_opn + 1)
                    if param_cls < 0:
                        pos = next_match.end()
                        continue
                    paren_opn = raw.find("(", param_cls + 1)
                    if paren_opn < 0:
                        # No port list after parameters
                        hdr = raw[next_match.end() : param_cls + 1]
                        port_cls = param_cls
                    else:
                        port_cls = _sv_match_paren(raw, paren_opn + 1)
                        if port_cls < 0:
                            pos = next_match.end()
                            continue
                        hdr = raw[next_match.end() : port_cls + 1]
                else:
                    # No parameters, just port list
                    port_cls = _sv_match_paren(raw, paren_opn + 1)
                    if port_cls < 0:
                        pos = next_match.end()
                        continue
                    hdr = raw[next_match.end() : port_cls + 1]
            else:
                # No port list at all: interface name ;
                if semi < 0:
                    pos = next_match.end()
                    continue
                hdr = raw[next_match.end() : semi]
                port_cls = semi

            eim = _RE_ENDINTERFACE.search(raw, port_cls + 1)
            bdy_end = eim.start() if eim else len(raw)
            end_pos = eim.end() if eim else len(raw)
            bdy = raw[port_cls + 1 : bdy_end]
            intrfc_dcts = _parse_interface_definition(intrfc_nm, hdr, bdy, defines, define_values)
            modules.extend(intrfc_dcts)
            pos = end_pos
        else:
            # Module parsing
            mod_name = next_match.group(1)
            paren_open = raw.find("(", next_match.end())
            semi = raw.find(";", next_match.end())
            # Skip forward-declaration form: module name; (no port list)
            if paren_open < 0:
                pos = max(semi + 1, next_match.end())
                continue
            # Strip package_import_declarations from text between module name and port list
            between = raw[next_match.end() : paren_open]
            between_clean = _RE_PKG_IMPORT.sub("", between)
            # Check for malformed header: any non-import semicolon before port list
            if ";" in between_clean:
                pos = max(semi + 1, next_match.end())
                continue
            param_text = ""
            between_clean = between_clean.strip()
            if between_clean.startswith("#"):
                param_close = _sv_match_paren(raw, paren_open + 1)
                if param_close < 0:
                    pos = next_match.end()
                    continue
                param_text = raw[paren_open + 1 : param_close]
                paren_open = raw.find("(", param_close + 1)
                if paren_open < 0:
                    pos = next_match.end()
                    continue
            port_close = _sv_match_paren(raw, paren_open + 1)
            if port_close < 0:
                pos = next_match.end()
                continue
            ports = _sv_parse_ports(raw[paren_open + 1 : port_close], define_values)
            insts = []
            em = _RE_ENDMOD.search(raw, port_close)
            body_end = em.start() if em else len(raw)
            end_pos = em.end() if em else len(raw)
            body = raw[port_close + 1 : body_end]
            if param_text:
                body = param_text + "\n" + body
            # Collect defparam overrides first
            defparam_ovr = _sv_collect_defparam_overrides(body)
            raw_instances = []
            for iname, ctype, pmap, ovr in _sv_extract_instances(body, define_values):
                # Merge defparam overrides (defparam wins over inline #())
                if iname in defparam_ovr:
                    ovr = {**ovr, **defparam_ovr[iname]}
                raw_instances.append({"n": iname, "c": ctype, "p": pmap, "o": ovr})

            # Synthesize primitive modules and update instance cell_types
            insts, syn_prim_modules = _sv_synthesize_primitive_modules(
                raw_instances, _get_primitive_types()
            )
            modules.extend(syn_prim_modules)
            param_names = []
            if param_text:
                for pm in re.finditer(r"\bparameter\s+(?:\w+\s+)?(\w+)\s*=", param_text):
                    param_names.append(pm.group(1))
            wires_2d = _sv_extract_wires_2d(body, define_values)
            port_2d = _sv_extract_wires_2d(raw[paren_open + 1 : port_close], define_values)
            wires_2d.update(port_2d)
            wire_widths_1d = _sv_extract_wire_widths_1d(body, define_values)
            port_widths_1d = _sv_extract_wire_widths_1d(
                raw[paren_open + 1 : port_close], define_values
            )
            wire_widths_1d.update(port_widths_1d)

            # Non-ANSI port-style fix: when the port list is just bare names,
            # expand any bare-name port whose width is now known from the body decls.
            for idx, p in enumerate(ports):
                if isinstance(p, dict) and p.get("hi") is None and p.get("lo") is None:
                    name = p["name"]
                    w = wire_widths_1d.get(name) or wires_2d.get(name)
                    if w and w > 1:
                        ports[idx] = _sv_make_port_entry(name, w - 1, 0)

            aliases = _sv_extract_alias_pairs(body, define_values, wire_widths_1d, wires_2d)
            modules.append(
                {
                    "name": mod_name,
                    "ports": ports,
                    "insts": insts,
                    "body": body if param_names else "",
                    "param_names": param_names,
                    "wires_2d": wires_2d,
                    "aliases": aliases,
                }
            )
            pos = end_pos
    return modules
