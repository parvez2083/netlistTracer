from __future__ import annotations

import re
from collections import defaultdict
from typing import Optional

from netlist_tracer.model import SubcktDef
from netlist_tracer.parsers.verilog.structure import (
    _sv_extract_instances,
    _sv_extract_wires_2d,
    _sv_split_concat_pieces,
)

_RE_BRACKET_EXPR = re.compile(r"\[([^\[\]]+)\]")
_RE_SAFE_ARITH = re.compile(r"^[\d\s+\-*/()]+$")


def _sv_eval_bracket_arith(text: str) -> str:
    """Inside every [...] subscript, evaluate purely-numeric arithmetic."""

    def _repl(m):
        expr = m.group(1).strip()
        if ":" in expr:
            return m.group(0)
        if not _RE_SAFE_ARITH.match(expr):
            return m.group(0)
        try:
            return f"[{int(eval(expr))}]"
        except Exception:
            return m.group(0)

    prev = None
    for _ in range(3):
        new = _RE_BRACKET_EXPR.sub(_repl, text)
        if new == prev or new == text:
            text = new
            break
        prev = text
        text = new
    return text


def _sv_mangle_value(val) -> str:
    """Mangle a parameter value into a valid identifier."""
    s = str(val).strip()
    m = re.match(r"^\d+'[bBoOdDhH](\w+)$", s)
    if m:
        s = m.group(1)
    return re.sub(r"[^A-Za-z0-9_]", "_", s)


def _sv_mangle_name(cell: str, overrides: dict) -> str:
    """Generate mangled specialized cell name."""
    parts = [cell]
    for k in sorted(overrides):
        parts.append(f"{k}_{_sv_mangle_value(overrides[k])}")
    return "__".join(parts)


def _sv_specialize_modules(all_modules: list, define_values: dict, max_combos: int = 64) -> int:
    """Generate parameter-specialized copies of parameterized modules.

    Returns the number of specialized modules created.
    """
    by_name = {m["name"]: m for m in all_modules}
    combos = defaultdict(set)
    for mod in all_modules:
        for inst in mod["insts"]:
            if not inst.get("o"):
                continue
            ctype = inst["c"]
            target = by_name.get(ctype)
            if target is None or not target.get("body"):
                continue
            valid_params = set(target.get("param_names") or [])
            kept = {k: v for k, v in inst["o"].items() if k in valid_params}
            if not kept:
                continue
            combos[ctype].add(frozenset(kept.items()))
    new_modules = []
    spec_lookup = {}
    for ctype, combo_set in combos.items():
        if len(combo_set) > max_combos:
            from netlist_tracer._logging import get_logger

            logger = get_logger(__name__)
            logger.warning(
                f"WARNING: {ctype} has {len(combo_set)} parameter combos "
                f"(>{max_combos}); skipping specialization"
            )
            continue
        target = by_name[ctype]
        base_body = target["body"]
        for combo in combo_set:
            ovr = dict(combo)
            mangled = _sv_mangle_name(ctype, ovr)
            spec_lookup[(ctype, combo)] = mangled
            if mangled in by_name:
                continue
            sub_body = base_body
            param_res = {
                pname: re.compile(r"\b" + re.escape(pname) + r"\b") for pname in ovr.keys()
            }
            for pname, pval in ovr.items():
                sval = str(pval).strip()
                sub_body = param_res[pname].sub(sval, sub_body)
            sub_body = _sv_eval_bracket_arith(sub_body)
            new_insts = []
            for iname, ictype, pmap, sub_ovr in _sv_extract_instances(sub_body, define_values):
                new_insts.append(
                    {
                        "n": iname,
                        "c": ictype,
                        "p": pmap,
                        "o": sub_ovr,
                    }
                )
            spec_wires_2d = _sv_extract_wires_2d(sub_body, define_values)
            new_modules.append(
                {
                    "name": mangled,
                    "ports": list(target["ports"]),
                    "insts": new_insts,
                    "body": "",
                    "param_names": [],
                    "wires_2d": spec_wires_2d,
                    "aliases": list(target.get("aliases") or []),
                }
            )
    for mod in all_modules:
        for inst in mod["insts"]:
            if not inst.get("o"):
                continue
            ctype = inst["c"]
            target = by_name.get(ctype)
            if target is None or not target.get("body"):
                continue
            valid_params = set(target.get("param_names") or [])
            kept = {k: v for k, v in inst["o"].items() if k in valid_params}
            if not kept:
                continue
            key = (ctype, frozenset(kept.items()))
            mangled = spec_lookup.get(key)
            if mangled:
                inst["c"] = mangled
                inst["o"] = {}
    all_modules.extend(new_modules)
    return len(new_modules)


def _sv_flatten_ports(ports: list) -> list:
    """Flatten list of port dicts to a flat list of bit-level pin names."""
    out = []
    for p in ports:
        if isinstance(p, dict):
            out.extend(p["bits"])
        else:
            out.append(p)
    return out


def _sv_assemble(
    all_modules: list, top: Optional[str] = None, define_values: Optional[dict] = None
) -> tuple[dict[str, SubcktDef], list]:
    """Build flat subckt + per-instance lists from parsed modules.

    Returns tuple of (subckts_dict, instances_list) where instances_list
    contains dicts (to be converted to Instance objects by caller).
    """
    from netlist_tracer.parsers.verilog.structure import _sv_expand_pin_net

    lookup = {m["name"]: m for m in all_modules}
    define_values = define_values or {}
    if top and top in lookup:
        keep = set()
        queue = [top]
        while queue:
            name = queue.pop()
            if name in keep:
                continue
            keep.add(name)
            mod = lookup.get(name)
            if mod:
                for inst in mod["insts"]:
                    queue.append(inst["c"])
        lookup = {k: v for k, v in lookup.items() if k in keep}
    subckts_flat = {name: _sv_flatten_ports(m["ports"]) for name, m in lookup.items()}
    instances = []
    for mod_name, mod in lookup.items():
        parent_wires_2d = mod.get("wires_2d") or {}
        for inst in mod["insts"]:
            ctype = inst["c"]
            pmap = inst["p"]
            if ctype in lookup:
                cell_ports = lookup[ctype]["ports"]
                nets = []
                for port in cell_ports:
                    if isinstance(port, dict):
                        pname = port["name"]
                        bits = port["bits"]
                        net_str = pmap.get(pname, "")
                        if len(bits) == 1:
                            nets.append(net_str)
                        else:
                            expanded = _sv_expand_pin_net(
                                net_str, len(bits), define_values, parent_wires_2d
                            )
                            if expanded is not None:
                                nets.extend(expanded)
                            else:
                                if net_str:
                                    # Check if net_str is a top-level concatenation
                                    stripped = net_str.strip()
                                    if stripped.startswith("{") and stripped.endswith("}"):
                                        # Attempt concat-aware split
                                        operands = _sv_split_concat_pieces(stripped[1:-1])
                                        port_width = len(bits)
                                        n_operands = len(operands)
                                        if port_width % n_operands == 0:
                                            bits_per_op = port_width // n_operands
                                            concat_result = []
                                            for operand in operands:
                                                operand = operand.strip()
                                                # Only emit indexed names if operand is a bare identifier
                                                if re.match(r"^[A-Za-z_]\w*$", operand):
                                                    for i in range(bits_per_op - 1, -1, -1):
                                                        concat_result.append(f"{operand}[{i}]")
                                                else:
                                                    # Non-bare operand: fall back to replicating operand
                                                    concat_result.extend([operand] * bits_per_op)
                                            nets.extend(concat_result)
                                        else:
                                            # Uneven split: replicate raw concat
                                            nets.extend([net_str] * len(bits))
                                    else:
                                        # Non-concat fallback: single-net per-bit emission
                                        for bit_name in bits:
                                            bm = re.match(r"^[A-Za-z_]\w*\[(\d+)\]$", bit_name)
                                            if bm:
                                                nets.append(f"{net_str}[{bm.group(1)}]")
                                            else:
                                                nets.append(net_str)
                                else:
                                    nets.extend([""] * len(bits))
                    else:
                        nets.append(pmap.get(port, ""))
            else:
                nets = list(pmap.values())
            instances.append(
                {
                    "name": inst["n"],
                    "cell_type": ctype,
                    "nets": nets,
                    "parent_cell": mod_name,
                }
            )
    # Convert subckts to SubcktDef objects
    subckts_out = {}
    for name, pins_list in subckts_flat.items():
        params = {}
        alias_pairs = []
        # Look up module definition to get params and aliases
        mod = lookup.get(name)
        if mod:
            params = mod.get("params", {})
            alias_pairs = mod.get("aliases") or []
        sub = SubcktDef(name=name, pins=pins_list, params=params)
        # Merge aliases from module definitions
        if alias_pairs:
            from netlist_tracer.model import merge_aliases_into_subckt

            merge_aliases_into_subckt(sub, alias_pairs)
        subckts_out[name] = sub

    return subckts_out, instances
