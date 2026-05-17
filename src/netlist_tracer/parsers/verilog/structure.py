from __future__ import annotations

import bisect
import re
from typing import Optional

from netlist_tracer._logging import get_logger
from netlist_tracer.parsers.verilog.preprocess import _sv_resolve_bound, _sv_resolve_width_expr

_logger = get_logger(__name__)

# Pre-compiled patterns
_RE_INST = re.compile(r"\b([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)?\(")
_RE_PARAM_BLOCK = re.compile(r"#\s*\((?:[^()]*|\((?:[^()]*|\([^()]*\))*\))*\)")
_RE_PIN = re.compile(r"\.(\w+)\s*\(")
_RE_LOCALPARAM = re.compile(r"\b(?:localparam|parameter)\s+(?:\w+\s+)?(\w+)\s*=\s*([^;,)]+)")
_RE_DOLLAR_SIZE = re.compile(r"\$size\s*\(\s*(\w+)\s*\)")

_KEYWORDS = frozenset(
    {
        "module",
        "endmodule",
        "input",
        "output",
        "inout",
        "wire",
        "reg",
        "logic",
        "real",
        "integer",
        "time",
        "string",
        "void",
        "assign",
        "always",
        "always_ff",
        "always_comb",
        "always_latch",
        "initial",
        "generate",
        "endgenerate",
        "for",
        "if",
        "else",
        "case",
        "casex",
        "casez",
        "default",
        "endcase",
        "begin",
        "end",
        "parameter",
        "localparam",
        "genvar",
        "function",
        "endfunction",
        "task",
        "endtask",
        "assert",
        "assume",
        "cover",
        "property",
        "sequence",
        "typedef",
        "enum",
        "struct",
        "union",
        "interface",
        "endinterface",
        "modport",
        "import",
        "export",
        "virtual",
        "class",
        "endclass",
        "bind",
        "disable",
        "fork",
        "join",
        "wait",
        "return",
        "automatic",
        "covergroup",
        "constraint",
        "package",
        "endpackage",
    }
)

_PRIMITIVES = frozenset(
    {
        "and",
        "nand",
        "or",
        "nor",
        "xor",
        "xnor",
        "not",
        "buf",
        "bufif0",
        "bufif1",
        "notif0",
        "notif1",
        "pmos",
        "nmos",
        "cmos",
        "rpmos",
        "rnmos",
        "rcmos",
        "tran",
        "tranif0",
        "tranif1",
        "rtran",
        "rtranif0",
        "rtranif1",
        "pullup",
        "pulldown",
    }
)


def _primitive_pin_names(prim_type: str, arity: int) -> list[str]:
    """
    Generate pin names for a built-in primitive gate based on its type and arity.

    For and/or/nand/nor/xor/xnor: output first, then inputs (arity-1 inputs).
    For buf/not: outputs first (arity-1 outputs), then single input.
    For other primitives: generic _pN naming.

    Inputs:
        prim_type: Primitive type (and, nand, buf, not, etc.)
        arity: Total number of pins

    Outputs:
        Ordered list of pin names [pin0, pin1, ...]
    """
    if prim_type in {"and", "nand", "or", "nor", "xor", "xnor"}:
        # Output first, then inputs
        if arity < 2:
            return [f"_p{i}" for i in range(arity)]
        return ["out"] + [f"in{i}" for i in range(arity - 1)]
    elif prim_type in {"buf", "not"}:
        # Outputs first, then single input
        if arity < 2:
            return [f"_p{i}" for i in range(arity)]
        return [f"out{i}" for i in range(arity - 1)] + ["in"]
    else:
        # Generic naming for other primitives
        return [f"_p{i}" for i in range(arity)]


_RE_BUS_DECL = re.compile(
    r"\b(?:input|output|inout|wire|logic|reg)\s+(?:wire\s+|logic\s+|reg\s+)?"
    r"\[\s*(\d+)\s*:\s*(\d+)\s*\]\s+(\w+)"
)
_RE_BUS_DECL_2D = re.compile(
    r"\b(?:input|output|inout|wire|logic|reg)\s+(?:wire\s+|logic\s+|reg\s+)?"
    r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s*"
    r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s+(\w+)"
)
_RE_BUS_DECL_UNPACKED = re.compile(
    r"\b(?:input|output|inout|wire|logic|reg)\s+(?:wire\s+|logic\s+|reg\s+)?"
    r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s+(\w+)\s*"
    r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s*[;,)]"
)
_RE_BUS_DECL_MULTI = re.compile(
    r"\b(?:input|output|inout|wire|logic|reg)\s+(?:wire\s+|logic\s+|reg\s+)?"
    r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s+"
    r"([A-Za-z_]\w*(?:\s*,\s*[A-Za-z_]\w*)*)\s*[;,)]"
)
_RE_ASSIGN = re.compile(r"\bassign\s+(.+?)\s*=\s*(.+?)\s*;", re.DOTALL)
_RE_BEGIN_END = re.compile(r"\b(begin|end)\b")
_RE_GENFOR = re.compile(
    r"\bfor\s*\(\s*(?:genvar\s+)?(?:int\s+)?(\w+)\s*=\s*([^;]+?)\s*;\s*\w+\s*(<=?)\s*([^;]+?)\s*;\s*([^)]+?)\s*\)"
    r"\s*begin\s*:\s*(\w+)"
)
_RE_GENIF = re.compile(r"\bgenerate\s+if\s*\(((?:[^()]|\([^()]*\))*?)\)\s*begin\s*(?::(\w+))?")
_RE_GENCASE = re.compile(r"\bgenerate\s+case\s*\(((?:[^()]|\([^()]*\))*?)\)")


def _sv_make_port_entry(name: str, hi: Optional[int] = None, lo: Optional[int] = None) -> dict:
    """Build a port-info dict (scalar or bus, MSB-first bits)."""
    if hi is None or lo is None:
        return {"name": name, "bits": [name], "hi": None, "lo": None}
    if hi >= lo:
        order = range(hi, lo - 1, -1)
    else:
        order = range(lo, hi - 1, -1)
    bits = [f"{name}[{i}]" for i in order]
    return {"name": name, "bits": bits, "hi": hi, "lo": lo}


def _sv_parse_ports(port_text: str, define_values: Optional[dict[str, int]] = None) -> list:
    """Extract ordered port info from module port declaration text."""
    port_text = re.sub(r"\s+", " ", port_text).strip()
    if not port_text:
        return []
    items = []
    depth = 0
    buf = []
    for ch in port_text:
        if ch in ("(", "["):
            depth += 1
            buf.append(ch)
        elif ch in (")", "]"):
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        items.append("".join(buf).strip())
    ports = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        im = re.match(r"(\w+)\.(\w+)\s+(\w+)", item)
        if im and im.group(1) not in (
            "input",
            "output",
            "inout",
            "logic",
            "wire",
            "reg",
            "real",
            "electrical",
        ):
            ports.append(_sv_make_port_entry(im.group(3)))
            continue
        item = re.sub(r"^(input|output|inout)\s+", "", item)
        item = re.sub(r"^(logic|reg|wire|real|integer|electrical)\s+", "", item)
        item = item.strip()
        hi = lo = None
        inner_hi = inner_lo = None
        bm = re.match(r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s*(.*)$", item)
        if bm:
            hi_expr = bm.group(1)
            lo_expr = bm.group(2)
            rest = bm.group(3).strip()
            hi = _sv_resolve_width_expr(hi_expr, define_values)
            lo = _sv_resolve_width_expr(lo_expr, define_values)
            # 2D port? `[hi:lo][inner_hi:inner_lo] name`
            bm2 = re.match(r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s*(.*)$", rest)
            if bm2:
                inner_hi = _sv_resolve_width_expr(bm2.group(1), define_values)
                inner_lo = _sv_resolve_width_expr(bm2.group(2), define_values)
                rest = bm2.group(3).strip()
            item = rest
        else:
            item = re.sub(r"\[[^\]]*\]\s*", "", item).strip()
        if item and re.match(r"^[A-Za-z_]\w*$", item):
            if hi is not None and lo is not None and inner_hi is not None and inner_lo is not None:
                # 2D port: expand to outer*inner individual bits
                outer = range(hi, lo - 1, -1) if hi >= lo else range(lo, hi - 1, -1)
                inner = (
                    range(inner_hi, inner_lo - 1, -1)
                    if inner_hi >= inner_lo
                    else range(inner_lo, inner_hi - 1, -1)
                )
                bits = [f"{item}[{i}][{j}]" for i in outer for j in inner]
                ports.append({"name": item, "bits": bits, "hi": hi, "lo": lo})
            elif hi is not None and lo is not None:
                ports.append(_sv_make_port_entry(item, hi, lo))
            else:
                ports.append(_sv_make_port_entry(item))
    return ports


def _sv_split_concat_pieces(inner: str) -> list:
    """Split text inside a {...} concatenation by top-level commas."""
    pieces = []
    depth = 0
    buf = []
    for ch in inner:
        if ch in ("{", "(", "["):
            depth += 1
            buf.append(ch)
        elif ch in ("}", ")", "]"):
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            pieces.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if buf:
        pieces.append("".join(buf).strip())
    return pieces


def _sv_expand_piece(
    piece: str,
    define_values: Optional[dict[str, int]] = None,
    port_signals: Optional[dict] = None,
    wires_2d: Optional[dict[str, int]] = None,
) -> Optional[list]:
    """Expand a single concatenation piece into a list of per-bit net names."""
    if define_values is None:
        define_values = {}
    if wires_2d is None:
        wires_2d = {}
    p = piece.strip()
    if not p:
        return None
    if p.startswith("{") and p.endswith("}"):
        return _sv_expand_concat_str(p[1:-1], define_values, port_signals, wires_2d)
    rm = re.match(r"^([^\s{}]+)\s*\{(.*)\}$", p)
    if rm:
        n_expr = rm.group(1)
        inner = rm.group(2)
        n = _sv_resolve_bound(n_expr, define_values or {})
        if n is not None:
            sub = _sv_expand_concat_str(inner, define_values, port_signals, wires_2d)
            if sub is not None:
                return sub * n
        return None
    # Width-cast WIDTH'(EXPR) — strip cast, expand inner.
    cm = re.match(r"^[`]?[A-Za-z_0-9]+\s*'\s*\((.+)\)\s*$", p, re.DOTALL)
    if cm:
        return _sv_expand_piece(cm.group(1).strip(), define_values, port_signals, wires_2d)
    lm = re.match(r"^(\d+)'([bBoOdDhH])([0-9a-fA-FxXzZ_?]+)$", p)
    if lm:
        width = int(lm.group(1))
        base = lm.group(2).lower()
        digits = lm.group(3).replace("_", "")
        try:
            if base == "b":
                bits = digits.zfill(width)[-width:]
            elif base == "o":
                val = int(digits, 8)
                bits = bin(val)[2:].zfill(width)[-width:]
            elif base == "d":
                val = int(digits, 10)
                bits = bin(val)[2:].zfill(width)[-width:]
            elif base == "h":
                val = int(digits, 16)
                bits = bin(val)[2:].zfill(width)[-width:]
            else:
                return None
            return [f"1'b{c}" for c in bits]
        except Exception:
            return None
    if p in ("0", "1"):
        return [f"1'b{p}"]
    dm = re.match(
        r"^([A-Za-z_]\w*)\s*\[\s*([^\]]+?)\s*\]\s*"
        r"\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s*$",
        p,
    )
    if dm:
        name = dm.group(1)
        idx_expr = dm.group(2).strip()
        idx = _sv_resolve_bound(idx_expr, define_values or {})
        hi = _sv_resolve_bound(dm.group(3), define_values or {})
        lo = _sv_resolve_bound(dm.group(4), define_values or {})
        if hi is None or lo is None:
            return None
        idx_str = str(idx) if idx is not None else idx_expr
        if hi >= lo:
            return [f"{name}[{idx_str}][{i}]" for i in range(hi, lo - 1, -1)]
        else:
            return [f"{name}[{idx_str}][{i}]" for i in range(lo, hi - 1, -1)]
    dm2 = re.match(
        r"^([A-Za-z_]\w*)\s*\[\s*([^\]]+?)\s*\]\s*"
        r"\[\s*([^\]]+?)\s*\]\s*$",
        p,
    )
    if dm2:
        name = dm2.group(1)
        idx_expr = dm2.group(2).strip()
        bit_expr = dm2.group(3).strip()
        idx = _sv_resolve_bound(idx_expr, define_values or {})
        bit = _sv_resolve_bound(bit_expr, define_values or {})
        idx_str = str(idx) if idx is not None else idx_expr
        bit_str = str(bit) if bit is not None else bit_expr
        return [f"{name}[{idx_str}][{bit_str}]"]
    bm = re.match(r"^([A-Za-z_]\w*)\s*\[\s*([^:\]]+?)\s*:\s*([^\]]+?)\s*\]\s*$", p)
    if bm:
        name = bm.group(1)
        hi = _sv_resolve_bound(bm.group(2), define_values or {})
        lo = _sv_resolve_bound(bm.group(3), define_values or {})
        if hi is None or lo is None:
            return None
        if hi >= lo:
            return [f"{name}[{i}]" for i in range(hi, lo - 1, -1)]
        else:
            return [f"{name}[{i}]" for i in range(lo, hi - 1, -1)]
    sm = re.match(r"^([A-Za-z_]\w*)\s*\[\s*([^\]]+?)\s*\]\s*$", p)
    if sm:
        name = sm.group(1)
        idx_expr = sm.group(2).strip()
        idx = _sv_resolve_bound(idx_expr, define_values or {})
        inner_w = (wires_2d or {}).get(name)
        idx_str = str(idx) if idx is not None else idx_expr
        if inner_w is not None and inner_w > 0:
            return [f"{name}[{idx_str}][{i}]" for i in range(inner_w - 1, -1, -1)]
        if idx is None:
            return [f"{name}[{idx_expr}]"]
        return [f"{name}[{idx}]"]
    if re.match(r"^[A-Za-z_]\w*$", p):
        return [p]
    return None


def _sv_expand_concat_str(
    inner: str,
    define_values: Optional[dict[str, int]] = None,
    port_signals: Optional[dict] = None,
    wires_2d: Optional[dict[str, int]] = None,
) -> Optional[list]:
    """Expand concatenation text to per-bit net names."""
    if define_values is None:
        define_values = {}
    if wires_2d is None:
        wires_2d = {}
    pieces = _sv_split_concat_pieces(inner)
    out = []
    for piece in pieces:
        bits = _sv_expand_piece(piece, define_values, port_signals, wires_2d)
        if bits is None:
            return None
        out.extend(bits)
    return out


def _sv_expand_pin_net(
    net_str: str,
    width: int,
    define_values: Optional[dict[str, int]] = None,
    wires_2d: Optional[dict[str, int]] = None,
) -> Optional[list]:
    """Expand an instance pin's net expression to a list of `width` bit-nets."""
    if define_values is None:
        define_values = {}
    if wires_2d is None:
        wires_2d = {}
    if width <= 0:
        return []
    s = net_str.strip()
    if not s:
        return [""] * width
    if s.startswith("{") and s.endswith("}"):
        bits = _sv_expand_concat_str(s[1:-1], define_values, None, wires_2d)
        if bits is None:
            return None
        if len(bits) != width:
            return None
        return bits
    bits = _sv_expand_piece(s, define_values, None, wires_2d)
    if bits is not None:
        if len(bits) == width:
            return bits
        if width > 1 and len(bits) == 1 and re.match(r"^[A-Za-z_]\w*$", s):
            name = s
            # 2D wire used whole: emit [outer][inner] bits to match port-side
            # expansion (otherwise port pins use [i][j] but connection uses
            # [k], breaking pin↔net mapping).
            inner_w = (wires_2d or {}).get(name)
            if inner_w and width % inner_w == 0:
                outer_w = width // inner_w
                return [
                    f"{name}[{i}][{j}]"
                    for i in range(outer_w - 1, -1, -1)
                    for j in range(inner_w - 1, -1, -1)
                ]
            return [f"{name}[{i}]" for i in range(width - 1, -1, -1)]
        return None
    return None


def _sv_find_begin_end(text: str, begin_pos: int) -> int:
    """Find matching 'end' for a 'begin' at begin_pos. Returns index past 'end'."""
    depth = 1
    i = begin_pos
    n = len(text)
    while i < n and depth > 0:
        m = _RE_BEGIN_END.search(text, i)
        if not m:
            break
        if m.group(1) == "begin":
            depth += 1
        else:
            depth -= 1
        i = m.end()
    return i


def _sv_eval_condition(
    cond_expr: str, define_values: Optional[dict[str, int]] = None
) -> Optional[bool]:
    """
    Evaluate a simple generate-if condition expression.

    Supports comparison operators: ==, !=, <, <=, >, >= and logical operators: &&, ||.
    If condition cannot be fully resolved, returns None to preserve block verbatim.

    Inputs:
        cond_expr: The condition expression (e.g., "WIDTH > 0", "MODE == 2")
        define_values: Dict of resolved parameter values (optional, defaults to {})

    Outputs:
        bool if condition evaluates to a definite truth value, None if unresolvable
    """
    if define_values is None:
        define_values = {}
    expr = cond_expr.strip()
    # Try to resolve all identifiers
    for match in re.finditer(r"\b([A-Za-z_]\w*)\b", expr):
        name = match.group(1)
        if name not in define_values and not name.isdigit():
            # Unresolvable identifier
            return None
    # Perform substitution
    resolved = expr
    for name, val in sorted(define_values.items(), key=lambda x: -len(x[0])):
        resolved = re.sub(r"\b" + re.escape(name) + r"\b", str(val), resolved)
    # Evaluate simple comparison expressions
    try:
        # Replace logical operators with Python equivalents
        resolved = re.sub(r"&&", " and ", resolved)
        resolved = re.sub(r"\|\|", " or ", resolved)
        # Safe evaluation (Python handles >=, <=, >, <, ==, != natively)
        return bool(eval(resolved))  # noqa: S307
    except Exception:
        return None


def _sv_parse_step(incr_expr: str, define_values: Optional[dict[str, int]] = None) -> Optional[int]:
    """Parse generate-for loop step expression."""
    if define_values is None:
        define_values = {}
    incr = incr_expr.strip()
    if "++" in incr:
        return 1
    m = re.match(r"\w+\s*\+=\s*(.+)", incr)
    if m:
        return _sv_resolve_bound(m.group(1), define_values or {})
    m = re.match(r"\w+\s*=\s*\w+\s*\+\s*(.+)", incr)
    if m:
        return _sv_resolve_bound(m.group(1), define_values or {})
    return None


def _sv_extract_wires_2d(body: str, define_values: Optional[dict[str, int]] = None) -> dict:
    """Scan a module body for 2D packed array declarations.
    Returns {wire_name: inner_width}."""
    if define_values is None:
        define_values = {}
    out = {}
    dvs = define_values or {}
    for m in _RE_BUS_DECL_2D.finditer(body):
        inner_hi = _sv_resolve_bound(m.group(3), dvs)
        inner_lo = _sv_resolve_bound(m.group(4), dvs)
        name = m.group(5)
        if inner_hi is None or inner_lo is None:
            continue
        width = abs(inner_hi - inner_lo) + 1
        if width > 0:
            out[name] = width
    for m in _RE_BUS_DECL_UNPACKED.finditer(body):
        p_hi = _sv_resolve_bound(m.group(1), dvs)
        p_lo = _sv_resolve_bound(m.group(2), dvs)
        name = m.group(3)
        if p_hi is None or p_lo is None:
            continue
        width = abs(p_hi - p_lo) + 1
        if width > 0 and name not in out:
            out[name] = width
    return out


def _sv_extract_wire_widths_1d(body: str, define_values: Optional[dict[str, int]] = None) -> dict:
    """Return {wire_name: width} for plain 1D bus declarations.
    Handles multi-name declarations and expression bounds."""
    if define_values is None:
        define_values = {}
    out = {}
    dvs = define_values or {}
    for m in _RE_BUS_DECL_MULTI.finditer(body):
        hi = _sv_resolve_bound(m.group(1), dvs)
        lo = _sv_resolve_bound(m.group(2), dvs)
        if hi is None or lo is None:
            continue
        width = abs(hi - lo) + 1
        if width <= 0:
            continue
        for name in re.split(r"\s*,\s*", m.group(3).strip()):
            if name and name not in out:
                out[name] = width
    return out


def _sv_expand_assign_side(
    s: str,
    define_values: Optional[dict[str, int]],
    wire_widths_1d: dict[str, int],
    wires_2d: dict[str, int],
) -> Optional[list]:
    """Expand one side of an `assign LHS = RHS;` into MSB-first bit names."""
    if define_values is None:
        define_values = {}
    s = s.strip()
    if not s:
        return None
    if s.startswith("{") and s.endswith("}"):
        return _sv_expand_concat_str(s[1:-1], define_values, None, wires_2d)
    if re.match(r"^[\.A-Za-z_]\w*$", s):
        w = (wire_widths_1d or {}).get(s)
        if w is not None and w > 1:
            return [f"{s}[{i}]" for i in range(w - 1, -1, -1)]
        return [s]
    return _sv_expand_piece(s, define_values, None, wires_2d)


def _sv_unroll_generate_for_blocks_once(
    body: str, define_values: Optional[dict[str, int]] = None
) -> str:
    """
    Single-pass unroll of generate-for blocks.

    For each generate-for block matched by _RE_GENFOR, substitute the block
    with N copies of its inner body where the loop variable has been replaced
    by the corresponding integer literal. Mirrors the in-place expansion already
    done inside _sv_extract_instances at lines 666-697, but emits the result
    as a single string suitable for downstream consumers (alias extraction).

    Inputs:
        body: Raw module body text
        define_values: Resolved parameter/macro values for bound resolution (optional, defaults to {})

    Outputs:
        Transformed body string with one level of generate-for blocks unrolled.
        If a generate-for block has unresolvable bounds (start/stop/step is None),
        it is preserved verbatim.

    Notes:
        Uses the same regex (_RE_GENFOR), helpers (_sv_find_begin_end, _sv_resolve_bound,
        _sv_parse_step), and loop-variable substitution pattern as _sv_extract_instances.
    """
    if define_values is None:
        define_values = {}

    result = []
    last = 0

    for m in _RE_GENFOR.finditer(body):
        if m.start() < last:
            continue
        # Append text before the match
        result.append(body[last : m.start()])

        var = m.group(1)
        start_expr = m.group(2)
        cmp_op = m.group(3)
        bound_expr = m.group(4)
        incr_expr = m.group(5)
        _label = m.group(6)  # noqa: F841

        start = _sv_resolve_bound(start_expr, define_values)
        stop = _sv_resolve_bound(bound_expr, define_values)
        step = _sv_parse_step(incr_expr, define_values)

        # If bounds are unresolvable, preserve block verbatim
        if start is None or stop is None or step is None:
            begin_end = m.end()
            block_end = _sv_find_begin_end(body, begin_end)
            result.append(body[m.start() : block_end])
            last = block_end
            continue

        if cmp_op == "<=":
            stop += 1

        begin_end = m.end()
        block_end = _sv_find_begin_end(body, begin_end)
        end_kw = body.rfind("end", begin_end, block_end)
        block_body = body[begin_end:end_kw]

        # Expand the loop: N copies with loop variable replaced
        var_re = re.compile(r"\b" + re.escape(var) + r"\b")
        for i in range(start, stop, step):
            expanded = var_re.sub(str(i), block_body)
            result.append(expanded)

        last = block_end

    # Append remaining text after last match
    result.append(body[last:])
    return "".join(result)


def _sv_unroll_generate_for_blocks(
    body: str, define_values: Optional[dict[str, int]] = None
) -> str:
    """
    Pre-processing helper: scan body for generate-for blocks and unroll them to fixed-point.

    Iterates unrolling until no more generate-for blocks remain (fixed-point), or until
    max depth (8 iterations) is reached. Handles nested generate-for blocks by repeated
    single-pass unrolls.

    For each generate-for block matched by _RE_GENFOR, substitute the block
    with N copies of its inner body where the loop variable has been replaced
    by the corresponding integer literal. Mirrors the in-place expansion already
    done inside _sv_extract_instances at lines 666-697, but emits the result
    as a single string suitable for downstream consumers (alias extraction).

    Inputs:
        body: Raw module body text
        define_values: Resolved parameter/macro values for bound resolution (optional, defaults to {})

    Outputs:
        Transformed body string with all nested generate-for blocks unrolled to concrete indices.
        If a generate-for block has unresolvable bounds (start/stop/step is None),
        it is preserved verbatim. If max depth (8) is reached, returns partial result
        with warning logged.

    Notes:
        Uses the same regex (_RE_GENFOR), helpers (_sv_find_begin_end, _sv_resolve_bound,
        _sv_parse_step), and loop-variable substitution pattern as _sv_extract_instances.
        Fixed-point iteration: outer loops produce inner-loop bodies repeated N times;
        inner loops are then expanded in subsequent iterations.
    """
    if define_values is None:
        define_values = {}

    max_depth = 8
    prev_body = None
    current_body = body
    iteration = 0

    while iteration < max_depth:
        # Check if there are any remaining generate-for blocks
        if not _RE_GENFOR.search(current_body):
            # No more blocks: reached fixed-point
            break

        # Single-pass unroll
        prev_body = current_body
        current_body = _sv_unroll_generate_for_blocks_once(current_body, define_values)
        iteration += 1

        # Reached fixed-point (no change)
        if current_body == prev_body:
            break

    if iteration >= max_depth and _RE_GENFOR.search(current_body):
        _logger.warning(
            f"WARNING: generate-for unrolling reached max depth ({max_depth}) with unresolved blocks remaining. "
            "Returning partial result. Check for infinite or pathological loop structures."
        )

    return current_body


def _sv_unroll_generate_if_case_blocks_to_fixed_point(
    body: str, define_values: Optional[dict[str, int]] = None
) -> str:
    """
    Unroll generate-if and generate-case blocks to fixed point.

    Companion to _sv_unroll_generate_blocks_to_fixed_point but EXCLUDES generate-for
    unrolling. Generate-for blocks must be handled by _sv_extract_instances's labeled
    extraction (which preserves the `<label>[<i>].` instance-name prefix); pre-unrolling
    them with the string-level unroller loses the block label.

    Inputs:
        body: Raw module body text
        define_values: Resolved parameter/macro values for bound resolution (optional, defaults to {})

    Outputs:
        Transformed body string with generate-if and generate-case blocks unrolled to fixed-point.
    """
    if define_values is None:
        define_values = {}
    max_depth = 8
    prev_body = None
    current_body = body
    iteration = 0
    while iteration < max_depth:
        prev_body = current_body
        current_body = _sv_unroll_generate_if_blocks_once(current_body, define_values)
        current_body = _sv_unroll_generate_case_blocks_once(current_body, define_values)
        iteration += 1
        if current_body == prev_body:
            break
    return current_body


def _sv_unroll_generate_blocks_to_fixed_point(
    body: str, define_values: Optional[dict[str, int]] = None
) -> str:
    """
    Unroll all generate blocks (for, if, case) to fixed-point.

    Iterates unrolling until no more generate blocks remain (fixed-point), or until
    max depth (8 iterations) is reached. Handles generate-for, generate-if, and
    generate-case blocks by repeated single-pass unrolls.

    Inputs:
        body: Raw module body text
        define_values: Resolved parameter/macro values for bound resolution (optional, defaults to {})

    Outputs:
        Transformed body string with all generate blocks unrolled to concrete indices.
        If a block has unresolvable conditions, it is preserved verbatim.
        If max depth (8) is reached, returns partial result with warning logged.
    """
    if define_values is None:
        define_values = {}

    max_depth = 8
    prev_body = None
    current_body = body
    iteration = 0

    while iteration < max_depth:
        prev_body = current_body
        # Single-pass unroll attempt
        current_body = _sv_unroll_generate_for_blocks_once(current_body, define_values)
        # Also handle generate-if blocks
        current_body = _sv_unroll_generate_if_blocks_once(current_body, define_values)
        # Also handle generate-case blocks
        current_body = _sv_unroll_generate_case_blocks_once(current_body, define_values)
        iteration += 1

        # Reached fixed-point (no change in this iteration)
        if current_body == prev_body:
            break

    has_remaining_blocks = bool(
        _RE_GENFOR.search(current_body)
        or _RE_GENIF.search(current_body)
        or _RE_GENCASE.search(current_body)
    )
    if iteration >= max_depth and has_remaining_blocks:
        _logger.warning(
            f"WARNING: generate-block unrolling reached max depth ({max_depth}) with unresolved blocks remaining. "
            "Returning partial result. Check for infinite or pathological loop structures."
        )

    return current_body


def _sv_unroll_generate_if_blocks_once(
    body: str, define_values: Optional[dict[str, int]] = None
) -> str:
    """
    Single-pass unroll of generate-if blocks.

    For each generate-if block, evaluate the condition. If true, keep the if-branch body.
    If false, keep the else-branch body (if present). If unresolvable, preserve block verbatim.

    Inputs:
        body: Raw module body text
        define_values: Resolved parameter/macro values for bound resolution (optional, defaults to {})

    Outputs:
        Transformed body string with one level of generate-if blocks unrolled.
    """
    if define_values is None:
        define_values = {}

    result = []
    last = 0

    for m in _RE_GENIF.finditer(body):
        if m.start() < last:
            continue
        result.append(body[last : m.start()])

        cond_expr = m.group(1).strip()
        _if_label = m.group(2)  # noqa: F841

        cond_val = _sv_eval_condition(cond_expr, define_values)

        # If condition is unresolvable, preserve block verbatim
        if cond_val is None:
            # Find the matching endgenerate
            begin_pos = m.end()
            block_end = _sv_find_generate_end(body, begin_pos)
            result.append(body[m.start() : block_end])
            last = block_end
            continue

        # Condition is resolvable; extract the if and optional else branches
        begin_pos = m.end()
        # Find the matching "end" for this "begin"
        if_end = _sv_find_begin_end(body, begin_pos)
        if_body_start = begin_pos
        if_body_end = body.rfind("end", begin_pos, if_end)

        if_body = body[if_body_start:if_body_end].strip()

        # Look for else clause
        else_body = ""
        search_pos = if_body_end
        else_match = re.match(r"\s*end\s+else\s+begin", body[search_pos:])
        if else_match:
            else_start = search_pos + else_match.end()
            else_end = _sv_find_begin_end(body, else_start)
            else_end_kw = body.rfind("end", else_start, else_end)
            else_body = body[else_start:else_end_kw].strip()
            block_end = _sv_find_generate_end(body, else_end)
        else:
            block_end = _sv_find_generate_end(body, if_end)

        # Emit the appropriate branch
        if cond_val:
            result.append(if_body)
        else:
            result.append(else_body)

        last = block_end

    result.append(body[last:])
    return "".join(result)


def _sv_unroll_generate_case_blocks_once(
    body: str, define_values: Optional[dict[str, int]] = None
) -> str:
    """
    Single-pass unroll of generate-case blocks.

    For each generate-case block, evaluate the expression. Pick the matching case arm,
    or default if no match. If expression is unresolvable, preserve block verbatim.

    Inputs:
        body: Raw module body text
        define_values: Resolved parameter/macro values for bound resolution (optional, defaults to {})

    Outputs:
        Transformed body string with one level of generate-case blocks unrolled.
    """
    if define_values is None:
        define_values = {}

    result = []
    last = 0

    for m in _RE_GENCASE.finditer(body):
        if m.start() < last:
            continue
        result.append(body[last : m.start()])

        expr = m.group(1).strip()

        # Try to evaluate the case expression
        expr_val = _sv_resolve_bound(expr, define_values)
        if expr_val is None:
            # Unresolvable; preserve block verbatim
            block_end = _sv_find_generate_end(body, m.end())
            result.append(body[m.start() : block_end])
            last = block_end
            continue

        # Expression is resolvable; find and parse case items
        case_start = m.end()
        block_end = _sv_find_generate_end(body, case_start)

        # Extract case body (between case(...) and endcase endgenerate)
        endcase_pos = body.rfind("endcase", case_start, block_end)
        if endcase_pos < 0:
            endcase_pos = block_end
        case_body = body[case_start:endcase_pos]

        # Parse case items: look for "VAL: begin : label ... end" or "default: begin : label ... end"
        picked_body = None
        default_body = None

        # Match individual case items
        for item_match in re.finditer(r"(\d+|default)\s*:\s*begin\s*(?::(\w+))?", case_body):
            item_label = item_match.group(1)
            item_start = item_match.end()
            item_end = _sv_find_begin_end(case_body, item_start)
            item_end_kw = case_body.rfind("end", item_start, item_end)
            if item_end_kw < 0:
                continue
            item_body = case_body[item_start:item_end_kw].strip()

            if item_label == "default":
                default_body = item_body
            else:
                try:
                    item_val = int(item_label)
                    if item_val == expr_val:
                        picked_body = item_body
                except ValueError:
                    pass

        # Emit picked body or default
        if picked_body is not None:
            result.append(picked_body)
        elif default_body is not None:
            result.append(default_body)

        last = block_end

    result.append(body[last:])
    return "".join(result)


def _sv_find_generate_end(text: str, search_start: int) -> int:
    """
    Find the index past 'endgenerate' starting from search_start.

    Looks for the closing 'endgenerate' keyword; returns position past it.
    """
    m = re.search(r"\bendgenerate\b", text[search_start:])
    if m:
        return search_start + m.end()
    return len(text)


def _sv_extract_alias_pairs(
    body: str,
    define_values: Optional[dict[str, int]],
    wire_widths_1d: dict[str, int],
    wires_2d: dict[str, int],
) -> list:
    """Extract per-bit alias pairs from `assign LHS = RHS;` statements.

    This function unrolls all generate blocks (for, if, case) in the body before extracting aliases,
    so that alias pairs reflect concrete per-iteration indices (e.g. out[0]->in[0])
    rather than literal loop-variable forms (e.g. out[i]->in[i]).
    """
    if define_values is None:
        define_values = {}
    body = _sv_unroll_generate_blocks_to_fixed_point(body, define_values)
    pairs = []
    for m in _RE_ASSIGN.finditer(body):
        lhs = m.group(1).strip()
        rhs = m.group(2).strip()
        rhs_outside_braces = re.sub(r"\{[^{}]*\}", "", rhs)
        if re.search(r"[?&|^!~+\-*/<>]", rhs_outside_braces):
            continue
        lhs_bits = _sv_expand_assign_side(lhs, define_values, wire_widths_1d, wires_2d)
        rhs_bits = _sv_expand_assign_side(rhs, define_values, wire_widths_1d, wires_2d)
        if lhs_bits is None or rhs_bits is None:
            continue
        if len(lhs_bits) != len(rhs_bits):
            continue
        for lhs_bit, rhs_bit in zip(lhs_bits, rhs_bits):
            if lhs_bit and rhs_bit and lhs_bit != rhs_bit:
                pairs.append((lhs_bit, rhs_bit))
    return pairs


def _sv_match_paren(text: str, start: int) -> int:
    """Index of matching ')' starting just after the '('. Returns -1 if unmatched."""
    depth = 1
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _sv_extract_instances_flat(body: str, prefix: str = "") -> list:
    """Extract instances from body text. Prefix is prepended to instance names."""
    overrides_at = {}
    pieces = []
    last = 0
    for pm in _RE_PARAM_BLOCK.finditer(body):
        pieces.append(body[last : pm.start()])
        inner = body[pm.start() + 2 : pm.end() - 1]
        # Parse param overrides from #(...) block
        overrides = {}
        for pin_m in _RE_PIN.finditer(inner):
            pname = pin_m.group(1)
            net_start = pin_m.end()
            net_close = _sv_match_paren(inner, net_start)
            if net_close < 0:
                continue
            overrides[pname] = re.sub(r"\s+", " ", inner[net_start:net_close].strip())
        overrides_at[pm.end()] = overrides
        pieces.append(" " * (pm.end() - pm.start()))
        last = pm.end()
    pieces.append(body[last:])
    body = "".join(pieces)
    instances = []
    for m in _RE_INST.finditer(body):
        cell = m.group(1)
        inst = m.group(2)
        if cell in _KEYWORDS:
            continue
        open_pos = m.end() - 1
        close = _sv_match_paren(body, open_pos + 1)
        if close < 0:
            continue
        inner = body[open_pos + 1 : close]
        if prefix:
            inst = prefix + inst
        overrides = {}
        cell_end = m.start(1) + len(cell)
        inst_start = m.start(2)
        override_keys = sorted(overrides_at.keys())
        i = bisect.bisect_left(override_keys, cell_end)
        j = bisect.bisect_right(override_keys, inst_start)
        if i < j:
            overrides = overrides_at[override_keys[i]]
        if cell in _PRIMITIVES:
            nets = [n.strip() for n in inner.split(",") if n.strip()]
            arity = len(nets)
            pin_names = _primitive_pin_names(cell, arity)
            pmap = {pin_names[i]: nets[i] for i in range(min(len(pin_names), len(nets)))}
            instances.append((inst, cell, pmap, overrides))
            continue
        if "." not in inner:
            continue
        pmap = {}
        for pm2 in _RE_PIN.finditer(inner):
            pin = pm2.group(1)
            net_start = pm2.end()
            net_close = _sv_match_paren(inner, net_start)
            if net_close < 0:
                continue
            pmap[pin] = re.sub(r"\s+", " ", inner[net_start:net_close].strip())
        if pmap:
            instances.append((inst, cell, pmap, overrides))
    return instances


def _sv_extract_instances(
    body: str, define_values: Optional[dict[str, int]] = None, prefix: str = ""
) -> list:
    """Extract instances, expanding generate-for blocks recursively."""
    if define_values is None:
        define_values = {}
    else:
        define_values = dict(define_values)
    instances = []
    last = 0

    # Parse local parameters
    for lm in _RE_LOCALPARAM.finditer(body):
        name = lm.group(1)
        val = _sv_resolve_bound(lm.group(2), define_values)
        if val is not None:
            define_values[name] = val

    # Handle $size() macro
    sizes = {}
    for bm in _RE_BUS_DECL.finditer(body):
        high, low, sig = int(bm.group(1)), int(bm.group(2)), bm.group(3)
        sizes[sig] = abs(high - low) + 1
    if sizes:

        def _repl_size(m):
            sig = m.group(1)
            return str(sizes[sig]) if sig in sizes else m.group(0)

        body = _RE_DOLLAR_SIZE.sub(_repl_size, body)

    # Unroll generate-if and generate-case blocks to fixed point
    body = _sv_unroll_generate_if_case_blocks_to_fixed_point(body, define_values)

    # Find and expand generate-for loops
    for m in _RE_GENFOR.finditer(body):
        if m.start() < last:
            continue
        instances.extend(_sv_extract_instances_flat(body[last : m.start()], prefix))
        var = m.group(1)
        start_expr = m.group(2)
        cmp_op = m.group(3)
        bound_expr = m.group(4)
        incr_expr = m.group(5)
        label = m.group(6)
        start = _sv_resolve_bound(start_expr, define_values)
        stop = _sv_resolve_bound(bound_expr, define_values)
        step = _sv_parse_step(incr_expr, define_values)
        if start is None or stop is None or step is None:
            last = m.end()
            continue
        if cmp_op == "<=":
            stop += 1
        begin_end = m.end()
        block_end = _sv_find_begin_end(body, begin_end)
        end_kw = body.rfind("end", begin_end, block_end)
        block_body = body[begin_end:end_kw]
        var_re = re.compile(r"\b" + re.escape(var) + r"\b")
        for i in range(start, stop, step):
            expanded = var_re.sub(str(i), block_body)
            iter_prefix = f"{prefix}{label}[{i}]."
            instances.extend(_sv_extract_instances(expanded, define_values, iter_prefix))
        last = block_end
    instances.extend(_sv_extract_instances_flat(body[last:], prefix))
    return instances


def _extract_modport(intrfc_nm: str, mport_txt: str, intrfc_pins: list[dict]) -> dict:
    """
    Parse one modport block and return a module-dict record for it.

    Inputs:
        intrfc_nm: Parent interface name
        mport_txt: The matched modport text: 'modport <name> (<direction-tagged signal list>)'
        intrfc_pins: Full port entry list of parent interface (used to expand bus signals to bit-level keys)

    Outputs:
        Module-dict with name='<interface>__mp_<modport>', pins (subset of interface_pins in modport order),
        params['_kind']='modport', params['_parent_interface']=<interface>, params['_pin_directions']={pin: direction}
    """
    # Parse modport name and body: modport name (direction sig1, sig2, ...)
    m = re.match(r"modport\s+(\w+)\s*\((.*)\)", mport_txt, re.DOTALL)
    if not m:
        return None

    mport_nm = m.group(1)
    drctn_blk = m.group(2).strip()

    # Build a map from base signal name to port entry for quick lookup
    pin_map = {p["name"]: p for p in intrfc_pins}

    # Parse direction-tagged signal list
    mport_pins = []
    pin_drctn = {}

    # Split by direction keywords; track current direction
    crrnt_drctn = None

    # Split on direction keywords while tracking scope
    parts = re.split(r"\b(input|output|inout|clocking|import|export)\b", drctn_blk)

    i = 0
    while i < len(parts):
        part = parts[i].strip()
        if not part:
            i += 1
            continue

        if part in ("input", "output", "inout"):
            crrnt_drctn = part
            i += 1
        elif part in ("clocking", "import", "export"):
            # Skip these constructs entirely
            crrnt_drctn = None
            i += 1
        else:
            # Parse signal list under current direction
            if crrnt_drctn:
                # Split by comma and semicolon
                sgnls = re.split(r"[,;]", part)
                for sgn in sgnls:
                    sgn = sgn.strip()
                    if sgn and not re.search(r"\b(task|function)\b", sgn):
                        # Remove array notation and extract base signal name
                        base_nm = re.sub(r"\[[^\]]*\]", "", sgn).strip()
                        if base_nm and re.match(r"^[A-Za-z_]\w*$", base_nm):
                            # Expand signal: if it refers to a bus in parent interface, use bit-level keys
                            if base_nm in pin_map:
                                port_ent = pin_map[base_nm]
                                # Use bits field if available (contains expanded names like data[7], data[0])
                                if "bits" in port_ent and port_ent["bits"]:
                                    for bit_nm in port_ent["bits"]:
                                        mport_pins.append(bit_nm)
                                        pin_drctn[bit_nm] = crrnt_drctn
                                else:
                                    # Scalar signal: use name as-is
                                    mport_pins.append(base_nm)
                                    pin_drctn[base_nm] = crrnt_drctn
                            else:
                                # Signal not in parent interface (unusual but tolerate)
                                mport_pins.append(sgn)
                                pin_drctn[sgn] = crrnt_drctn
            i += 1

    # Create modport module-dict
    mport_dict_nm = f"{intrfc_nm}__mp_{mport_nm}"
    return {
        "name": mport_dict_nm,
        "ports": [{"name": p, "bits": [p], "hi": None, "lo": None} for p in mport_pins],
        "insts": [],
        "body": "",
        "param_names": [],
        "wires_2d": {},
        "aliases": [],
        "params": {
            "_kind": "modport",
            "_parent_interface": intrfc_nm,
            "_pin_directions": pin_drctn,
        },
    }


def _parse_interface_definition(
    nm: str, hdr: str, bdy: str, dfns: set[str], dfn_vals: dict[str, int]
) -> list[dict]:
    """
    Parse one interface block and return a list of module-dict records: one for the interface
    itself, plus one per modport.

    Inputs:
        nm: Interface name (already extracted from opener)
        hdr: Text from 'interface name' through the opening ';' (contains optional #(params) and (port_list))
        bdy: Text between header and 'endinterface'
        dfns: Active preprocessor defines
        dfn_vals: Numeric values for defines

    Outputs:
        List of module-dict records. First entry is the interface itself; subsequent entries are modports.
    """
    # Parse interface header similarly to module header
    # Extract parameters if present: interface name #(...) (...)
    # We need to find the LAST parenthesis pair which contains the ports
    intrfc_pts = []

    # Find all parenthesis pairs
    paren_opn_idx = -1
    paren_cls_idx = -1
    depth = 0
    last_paren_opn = -1
    last_paren_cls = -1

    for i, ch in enumerate(hdr):
        if ch == "(":
            if depth == 0:
                last_paren_opn = i
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                last_paren_cls = i
                # Save the outermost pair that closes
                paren_opn_idx = last_paren_opn
                paren_cls_idx = last_paren_cls

    if paren_opn_idx >= 0 and paren_cls_idx >= 0 and paren_cls_idx > paren_opn_idx:
        intrfc_pts = _sv_parse_ports(hdr[paren_opn_idx + 1 : paren_cls_idx], dfn_vals)

    # Extract port declarations from body as interface pins
    bdy_wires = _sv_extract_wire_widths_1d(bdy, dfn_vals)

    # Collect all signals as pins (ports + declarations in body)
    all_pins = intrfc_pts.copy()

    # Add signals from body declarations (buses)
    for sgn_nm, wdth in bdy_wires.items():
        # Check if already in ports
        if not any(p["name"] == sgn_nm for p in all_pins):
            if wdth > 1:
                all_pins.append(_sv_make_port_entry(sgn_nm, wdth - 1, 0))
            else:
                all_pins.append(_sv_make_port_entry(sgn_nm))

    # Extract scalar signal declarations (logic, wire, reg without explicit bus notation)
    scalar_re = re.compile(
        r"\b(?:logic|wire|reg|bit|bit_vector|parameter|localparam)\s+(?:\w+\s+)?(\w+)(?:\s*[,;=]|(?:\s*\[\s*[^\]]*\]\s*)*\s*[,;=])"
    )
    found_scalars = set(bdy_wires.keys()) | {p["name"] for p in all_pins}
    for m in scalar_re.finditer(bdy):
        sgn_nm = m.group(1)
        if sgn_nm not in found_scalars and not re.match(r"^[A-Z_]\w*$", sgn_nm):
            # Don't add parameters or macro names
            if re.match(r"^[a-z_]\w*$", sgn_nm):
                all_pins.append(_sv_make_port_entry(sgn_nm))
                found_scalars.add(sgn_nm)

    # Extract modports from body
    mports = []
    mport_nms = []
    for mport_m in re.finditer(r"modport\s+(\w+)\s*\([^)]*\)", bdy, re.DOTALL):
        mport_txt = mport_m.group(0)
        mport_nm_match = re.match(r"modport\s+(\w+)", mport_txt)
        if mport_nm_match:
            mport_nm = mport_nm_match.group(1)
            mport_nms.append(mport_nm)
            mport_dct = _extract_modport(nm, mport_txt, all_pins)
            if mport_dct:
                mports.append(mport_dct)

    # Build interface module-dict
    intrfc_dct = {
        "name": nm,
        "ports": all_pins,
        "insts": [],
        "body": "",
        "param_names": [],
        "wires_2d": {},
        "aliases": [],
        "params": {"_kind": "interface", "_modports": mport_nms},
    }

    return [intrfc_dct] + mports
