from __future__ import annotations

import glob
import os
import re
from typing import Optional

# Pre-compiled patterns
_RE_IFDEF = re.compile(r"^\s*`ifdef\s+(\w+)")
_RE_IFNDEF = re.compile(r"^\s*`ifndef\s+(\w+)")
_RE_ELSE = re.compile(r"^\s*`else\b")
_RE_ENDIF = re.compile(r"^\s*`endif\b")
_RE_INCLUDE = re.compile(r"^\s*`include\b")
_RE_PREPROC = re.compile(r"^\s*`(timescale|define|undef)\b")
_RE_DEFINE_VALUE = re.compile(r"^\s*`define\s+(\w+)\s+(.+)")
_RE_DEFINE_BARE = re.compile(r"^\s*`define\s+(\w+)\s*(?://.*)?$")
_RE_BRACKET_EXPR = re.compile(r"\[([^\[\]]+)\]")
_RE_SAFE_ARITH = re.compile(r"^[\d\s+\-*/()]+$")


def _sv_substitute_vars(content: str, tvars: dict[str, str]) -> str:
    """Replace $key$ template variables with values from tvars."""
    for k, v in tvars.items():
        content = content.replace("$" + k + "$", v)
    return content


def _sv_strip_comments(content: str) -> str:
    """Remove both // and /* */ comments in a single pass."""
    out = []
    i = 0
    n = len(content)
    while i < n:
        if content[i] == "/" and i + 1 < n:
            if content[i + 1] == "/":
                j = content.find("\n", i)
                if j < 0:
                    break
                i = j
                continue
            elif content[i + 1] == "*":
                j = content.find("*/", i + 2)
                if j < 0:
                    break
                i = j + 2
                continue
        out.append(content[i])
        i += 1
    return "".join(out)


def _sv_resolve_inline_ifdefs(line: str, defines: set[str]) -> str:
    """Resolve `ifdef/`ifndef/`else/`endif appearing mid-line."""

    def _rep_ifdef_else(m):
        return m.group(2) if m.group(1) in defines else m.group(3)

    line = re.sub(r"`ifdef\s+(\w+)\s+(.*?)\s*`else\s+(.*?)\s*`endif", _rep_ifdef_else, line)

    def _rep_ifdef(m):
        return m.group(2) if m.group(1) in defines else ""

    line = re.sub(r"`ifdef\s+(\w+)\s+(.*?)\s*`endif", _rep_ifdef, line)

    def _rep_ifndef_else(m):
        return m.group(2) if m.group(1) not in defines else m.group(3)

    line = re.sub(r"`ifndef\s+(\w+)\s+(.*?)\s*`else\s+(.*?)\s*`endif", _rep_ifndef_else, line)

    def _rep_ifndef(m):
        return m.group(2) if m.group(1) not in defines else ""

    line = re.sub(r"`ifndef\s+(\w+)\s+(.*?)\s*`endif", _rep_ifndef, line)
    return line


def _sv_preprocess(content: str, defines: set[str]) -> str:
    """Resolve `ifdef/`ifndef/`else/`endif with configurable define set."""
    lines = content.split("\n")
    result = []
    stack = []
    for line in lines:
        s = line.lstrip()
        if ("`ifdef" in s or "`ifndef" in s) and "`endif" in s:
            if (not stack) or stack[-1][0]:
                result.append(_sv_resolve_inline_ifdefs(line, defines))
            continue
        m = _RE_IFDEF.match(s)
        if m:
            parent = (not stack) or stack[-1][0]
            met = m.group(1) in defines
            stack.append((parent and met, parent and not met))
            continue
        m = _RE_IFNDEF.match(s)
        if m:
            parent = (not stack) or stack[-1][0]
            met = m.group(1) not in defines
            stack.append((parent and met, parent and not met))
            continue
        if _RE_ELSE.match(s):
            if stack:
                _, can = stack[-1]
                stack[-1] = (can, False)
            continue
        if _RE_ENDIF.match(s):
            if stack:
                stack.pop()
            continue
        if _RE_INCLUDE.match(s) or _RE_PREPROC.match(s):
            continue
        if (not stack) or stack[-1][0]:
            result.append(line)
    return "\n".join(result)


def _sv_discover_headers(rtl_dir: str) -> list:
    """Find Verilog header files under rtl_dir for define discovery.
    Returns sorted list of paths to .vh / .svh / .d files."""
    files = []
    for ext in ("vh", "svh", "d"):
        files.extend(glob.glob(os.path.join(rtl_dir, "**", f"*.{ext}"), recursive=True))
        files.extend(glob.glob(os.path.join(rtl_dir, f"*.{ext}")))
    return sorted(set(files))


def _sv_parse_defines(
    filepaths: list, tvars: Optional[dict[str, str]] = None
) -> tuple[set[str], dict[str, int]]:
    """Parse header files and return (defines_set, define_values_dict).

    Args:
        filepaths: List of header file paths to scan.
        tvars: Optional template variable dict for substitution.

    Returns:
        Tuple of (defines_set, define_values_dict) where:
        - defines_set: every macro name appearing in `define statements.
        - define_values_dict: {name: int} for `define NAME <numeric expr>.
    """
    defines: set[str] = set()
    raw_defs: dict[str, str] = {}
    for fp in filepaths:
        try:
            with open(fp, errors="replace") as fh:
                content = fh.read()
        except OSError:
            continue
        if tvars:
            content = _sv_substitute_vars(content, tvars)
        for line in content.split("\n"):
            mv = _RE_DEFINE_VALUE.match(line)
            if mv:
                name = mv.group(1)
                val = mv.group(2).strip()
                ci = val.find("//")
                if ci >= 0:
                    val = val[:ci].strip()
                defines.add(name)
                if val:
                    raw_defs[name] = val
                continue
            mb = _RE_DEFINE_BARE.match(line)
            if mb:
                defines.add(mb.group(1))
    resolved: dict[str, int] = {}
    for _ in range(5):
        progress = False
        for name, expr in raw_defs.items():
            if name in resolved:
                continue

            def _repl(m):
                ref = m.group(1)
                if ref in resolved:
                    return str(resolved[ref])
                return m.group(0)

            val_str = re.sub(r"`(\w+)", _repl, expr)
            if re.match(r"^[\d\s+\-*/()]+$", val_str):
                try:
                    resolved[name] = int(eval(val_str))
                    progress = True
                except Exception:
                    pass
        if not progress:
            break
    return defines, resolved


def _sv_parse_define_values(
    filepaths: list, tvars: Optional[dict[str, str]] = None
) -> dict[str, int]:
    """Legacy entry point that returns ONLY the numeric values dict."""
    _, resolved = _sv_parse_defines(filepaths, tvars)
    return resolved


def _sv_extract_defines_from_text(
    content: str,
) -> dict[str, int]:
    """Extract and resolve `define macros from text content.

    Args:
        content: Text content to scan for `define statements.

    Returns:
        Dict of {name: int} for resolved numeric defines.
    """
    raw_defs: dict[str, str] = {}
    for line in content.split("\n"):
        mv = _RE_DEFINE_VALUE.match(line)
        if mv:
            name = mv.group(1)
            val = mv.group(2).strip()
            ci = val.find("//")
            if ci >= 0:
                val = val[:ci].strip()
            if val:
                raw_defs[name] = val
            continue
        mb = _RE_DEFINE_BARE.match(line)
        if mb:
            pass
    resolved: dict[str, int] = {}
    for _ in range(5):
        progress = False
        for name, expr in raw_defs.items():
            if name in resolved:
                continue

            def _repl(m):
                ref = m.group(1)
                if ref in resolved:
                    return str(resolved[ref])
                return m.group(0)

            val_str = re.sub(r"`(\w+)", _repl, expr)
            if re.match(r"^[\d\s+\-*/()]+$", val_str):
                try:
                    resolved[name] = int(eval(val_str))
                    progress = True
                except Exception:
                    pass
        if not progress:
            break
    return resolved


def _sv_resolve_bound(expr: str, define_values: Optional[dict[str, int]] = None) -> Optional[int]:
    """Resolve a genfor loop bound expression to an integer."""
    if define_values is None:
        define_values = {}
    expr = expr.strip()
    if expr.isdigit():
        return int(expr)

    def _repl_define(m):
        name = m.group(1)
        val = define_values.get(name)
        return str(val) if val is not None else m.group(0)

    resolved = re.sub(r"`(\w+)", _repl_define, expr)

    def _repl_ident(m):
        name = m.group(0)
        val = define_values.get(name)
        return str(val) if val is not None else name

    resolved = re.sub(r"\b[A-Za-z_]\w*\b", _repl_ident, resolved)
    try:
        if re.match(r"^[\d\s+\-*/()]+$", resolved):
            return int(eval(resolved))
    except Exception:
        pass
    return None


def _sv_resolve_width_expr(
    expr: Optional[str], define_values: Optional[dict[str, int]] = None
) -> Optional[int]:
    """Resolve a width expression to an integer."""
    if expr is None:
        return None
    return _sv_resolve_bound(expr, define_values or {})
