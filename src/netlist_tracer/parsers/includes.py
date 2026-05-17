"""Shared include-file expansion and path resolution for SPICE/Spectre dialects."""

from __future__ import annotations

import os
import re
from typing import Optional, Union

from netlist_tracer._logging import get_logger
from netlist_tracer.exceptions import IncludePathNotFoundError, NetlistParseError
from netlist_tracer.parsers.detect import detect_format

_logger = get_logger(__name__)


def expand_includes(
    top_file: str, dialect: str, include_paths: Optional[list[str]] = None
) -> tuple[list[tuple[str, str, int]], list[str], list[str]]:
    """Recursively expand include statements and return flattened line stream.

    Args:
        top_file: Absolute path to the top netlist file.
        dialect: 'spice' (handles .include/.inc/.lib) or 'spectre'.
        include_paths: Optional list of additional search directories.

    Returns:
        Tuple of (expanded_lines, ahdl_include_paths, spf_include_paths) where:
        - expanded_lines: List of (line_text, source_file_path, source_line_no) tuples
          with provenance preserved for error reporting.
        - ahdl_include_paths: List of resolved absolute paths to .va files referenced
          via ahdl_include directives (empty list for SPICE dialect).
        - spf_include_paths: List of resolved absolute paths to SPF files referenced
          via dspf_include directives OR plain include directives with SPF-hint
          extensions (.spf, .spf.gz, .dspf, .dspf.gz, case-insensitive).

    Raises:
        NetlistParseError: On cycle detection or unresolvable path.
    """
    if include_paths is None:
        include_paths = []

    expanded_lines: list[tuple[str, str, int]] = []
    ahdl_include_paths: list[str] = []
    spf_include_paths: list[str] = []
    # Stack entries are (abs_path, section_filter) tuples. section_filter is either None
    # (whole-file include) or (kind, section_name) tuple. Cycle detection compares the
    # full tuple, so same file with different sections is NOT a cycle.
    include_stack: list[tuple[str, Optional[tuple[str, str]]]] = []

    def _expand_recursive(
        filename: str,
        include_stack: list[tuple[str, Optional[tuple[str, str]]]],
        current_lang: str = "spice",
        section_filter: Optional[tuple[str, str]] = None,
    ) -> bool:
        """Recursively expand a file, tracking include stack for cycle detection.

        Args:
            filename: Absolute path to the file to expand.
            include_stack: List of (abs_path, section_filter) tuples representing
                the current include chain. section_filter is None for whole-file
                includes or (kind, section_name) tuple for section-aware loading.
                Cycle detection compares the full (path, section_filter) tuple,
                so the same file with different sections is NOT a cycle.
            current_lang: 'spice' or 'spectre' (for language mode tracking).
            section_filter: Optional (kind, section_name) tuple for section-aware loading.
                When None, emit all lines (current behavior).
                When ('spice', section_name), scan for .lib SECTION ... .endl [SECTION] markers
                and emit only lines between them.
                When ('spectre', section_name), scan for library NAME ... endlibrary [NAME] markers
                and emit only lines between them.

        Returns:
            bool: True if section_filter is None (always-emit mode) or the requested section
            was found and emitted. False if section_filter was set but the section name was
            not found in the file. This allows the caller to emit a 'section not found' WARNING.

        Known Limitation:
            Nested section blocks (a section opener inside another section's block) are
            handled via a flat-scan state machine that logs INFO when a nested opener is
            detected. This may emit inner-section content as part of the outer section in
            rare edge cases; proper depth tracking is deferred to v0.3.2. For v0.3.1,
            nested sections are uncommon in PDK .lib files.
        """
        abs_path = os.path.abspath(filename)
        cycle_key = (abs_path, section_filter)

        if cycle_key in include_stack:
            # Format the chain for readable error message
            chain_strs = []
            for stk_path, stk_sect in include_stack:
                if stk_sect:
                    chain_strs.append(f"{stk_path}[section={stk_sect[1]}]")
                else:
                    chain_strs.append(stk_path)
            # Add current cycle point
            if section_filter:
                chain_strs.append(f"{abs_path}[section={section_filter[1]}]")
            else:
                chain_strs.append(abs_path)
            chain_str = " -> ".join(chain_strs)
            raise NetlistParseError(
                f"Include cycle detected: {chain_str} (cycle closes at {abs_path})"
            )

        include_stack.append(cycle_key)

        try:
            with open(abs_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
        except FileNotFoundError as e:
            raise NetlistParseError(f"Include file not found: {abs_path}") from e

        # Section-aware loading state machine
        emitting = True  # default: always emit
        section_found = True  # default: pretend section was found (for return value)
        if section_filter is not None:
            emitting = False
            section_found = False

        line_no = 1
        i = 0
        while i < len(lines):
            line = lines[i]
            # Join backslash-continued lines before processing
            while i + 1 < len(lines) and line.rstrip().endswith("\\"):
                line = line.rstrip()[:-1] + " " + lines[i + 1].lstrip()
                i += 1
            stripped = line.strip()

            # Section marker scanning (when section_filter is active)
            if section_filter is not None:
                kind, requested_name = section_filter
                if kind == "spice":
                    # Match .lib SECTION_NAME (opener)
                    opener = re.match(r"^\.lib\s+(\S+)\s*$", stripped, re.IGNORECASE)
                    # Match .endl [SECTION_NAME] (closer)
                    closer = re.match(r"^\.endl\b(?:\s+(\S+))?\s*$", stripped, re.IGNORECASE)
                else:  # spectre
                    # Match library NAME or section NAME (opener, case-sensitive)
                    opener = re.match(r"^(?:library|section)\s+(\S+)\s*$", stripped)
                    # Match endlibrary [NAME] or endsection [NAME] (closer, case-sensitive)
                    closer = re.match(r"^(?:endlibrary|endsection)\b(?:\s+(\S+))?\s*$", stripped)

                if opener:
                    opener_name = opener.group(1)
                    if not emitting and opener_name == requested_name:
                        # Start of requested section — skip the opener line itself
                        emitting = True
                        section_found = True
                        line_no += 1
                        i += 1
                        continue
                    elif emitting:
                        # Nested section opener while already emitting — log and continue
                        _logger.info(
                            f"Nested or duplicate section opener '{opener_name}' "
                            f"inside '{requested_name}'; flat-scan, ignoring"
                        )
                        line_no += 1
                        i += 1
                        continue
                    else:
                        # Different section, skip
                        line_no += 1
                        i += 1
                        continue

                if closer and emitting:
                    # End of current section — skip the closer line itself
                    emitting = False
                    line_no += 1
                    i += 1
                    continue

                if not emitting:
                    # Outside requested section, skip this line entirely
                    line_no += 1
                    i += 1
                    continue

            # Track language mode for Spectre
            if dialect == "spectre" and stripped.startswith("simulator lang="):
                if "spice" in stripped.lower():
                    current_lang = "spice"
                elif "spectre" in stripped.lower():
                    current_lang = "spectre"

            # Parse ahdl_include directive (Spectre-only, side-channel collection)
            if dialect == "spectre" and current_lang == "spectre":
                ahdl_path = _parse_spectre_ahdl_include_directive(stripped)
                if ahdl_path:
                    try:
                        resolved_ahdl = _resolve_include_path(ahdl_path, abs_path, include_paths)
                        ahdl_include_paths.append(resolved_ahdl)
                        _logger.info(f"Include: {resolved_ahdl} (type=veriloga, section=-)")
                    except IncludePathNotFoundError:
                        _logger.warning(f"Skipping ahdl_include '{ahdl_path}' — path unresolvable")
                    line_no += 1
                    i += 1
                    continue

            # Parse dspf_include directive (Spectre-only, always SPF parser)
            if dialect == "spectre" and current_lang == "spectre":
                dspf_path = _parse_spectre_dspf_include_directive(stripped)
                if dspf_path:
                    try:
                        resolved_spf = _resolve_include_path(dspf_path, abs_path, include_paths)
                        spf_include_paths.append(resolved_spf)
                        _logger.info(f"Include: {resolved_spf} (type=spf, section=-)")
                    except IncludePathNotFoundError:
                        _logger.warning(f"Skipping dspf_include '{dspf_path}' — path unresolvable")
                    line_no += 1
                    i += 1
                    continue

            # Parse include directives based on current language
            include_info: Union[tuple[str, str], str, None] = None
            if dialect == "spectre" and current_lang == "spectre":
                include_info = _parse_spectre_include_directive(stripped)
            elif dialect in ("spice", "spectre") and current_lang == "spice":
                include_info = _parse_spice_include_directive(stripped)

            if include_info:
                raw_path: str
                section: str = ""
                directive_label: str  # '.lib' or '.include'/'.inc' or 'include' for log text
                is_lib_directive: bool = (
                    False  # True only for SPICE .lib (both bare and named-section)
                )
                if dialect == "spice" or (dialect == "spectre" and current_lang == "spice"):
                    # SPICE directives: include_info is tuple for .lib, string for .include/.inc
                    if isinstance(include_info, tuple):
                        directive_label = ".lib"
                        is_lib_directive = True  # All SPICE .lib forms use try-and-degrade
                        raw_path, section = include_info
                    else:
                        # .include or .inc — strict behavior
                        directive_label = ".include"
                        is_lib_directive = False
                        raw_path = include_info
                else:
                    # Pure Spectre: include_info is always (path, section) tuple after G1.
                    directive_label = "include"
                    is_lib_directive = False  # Spectre include stays strict
                    assert isinstance(include_info, tuple)
                    raw_path, section = include_info

                if section:
                    # Try-and-degrade with section-aware loading: attempt to resolve and
                    # inline, scanning only the requested section block. If the path can't
                    # be resolved or the section name is not found, warn and skip rather
                    # than raising — preserves parse progress on decks with
                    # environment-dependent PDK paths. Applies to SPICE `.lib path section`
                    # and Spectre `include "path" section=NAME`.
                    # NOTE: Only IncludePathNotFoundError is swallowed here; other
                    # NetlistParseError subclasses (e.g. cycle detection) propagate.
                    try:
                        resolved_path = _resolve_include_path(raw_path, abs_path, include_paths)
                    except IncludePathNotFoundError:
                        _logger.warning(
                            f"Skipping {directive_label} '{raw_path}' (section '{section}') "
                            f"— path unresolvable"
                        )
                        line_no += 1
                        i += 1
                        continue
                    section_kind = "spice" if directive_label == ".lib" else "spectre"
                    _logger.info(
                        f"Include: {resolved_path} (type={section_kind}, section={section})"
                    )
                    found = _expand_recursive(
                        resolved_path,
                        include_stack,
                        current_lang,
                        section_filter=(section_kind, section),
                    )
                    if not found:
                        _logger.warning(
                            f"Skipping {directive_label} '{raw_path}' (section '{section}') "
                            f"— section not found in {resolved_path}"
                        )
                    line_no += 1
                    i += 1
                    continue

                # Bare include directive (no section name)
                if is_lib_directive:
                    # Bare .lib: try-and-degrade (warn+skip if unresolvable)
                    # NOTE: Only IncludePathNotFoundError is swallowed here; other
                    # NetlistParseError subclasses (e.g. cycle detection) propagate.
                    try:
                        resolved_path = _resolve_include_path(raw_path, abs_path, include_paths)
                    except IncludePathNotFoundError:
                        _logger.warning(
                            f"Skipping {directive_label} '{raw_path}' — path unresolvable"
                        )
                        line_no += 1
                        i += 1
                        continue
                    _logger.info(f"Include: {resolved_path} (type=spectre, section=-)")
                    _expand_recursive(resolved_path, include_stack, current_lang)
                else:
                    # .include/.inc (and Spectre bare include): detect format via content
                    try:
                        resolved_path = _resolve_include_path(raw_path, abs_path, include_paths)
                    except NetlistParseError:
                        raise

                    # AC33: Use content-based detect_format instead of extension hint
                    detected_fmt = detect_format([resolved_path])
                    if detected_fmt == "spf":
                        spf_include_paths.append(resolved_path)
                        _logger.info(f"Include: {resolved_path} (type=spf, section=-)")
                    elif detected_fmt in ("spectre", "spice", "cdl", "edif", "verilog"):
                        # Route to inline expansion (Spectre/SPICE/CDL/EDIF/Verilog)
                        _logger.info(f"Include: {resolved_path} (type={detected_fmt}, section=-)")
                        _expand_recursive(resolved_path, include_stack, current_lang)
                    else:
                        # Fallback to spectre if detection is ambiguous
                        _logger.info(f"Include: {resolved_path} (type=spectre, section=-)")
                        _expand_recursive(resolved_path, include_stack, current_lang)
            else:
                # Regular line — add to output with provenance
                expanded_lines.append((line.rstrip("\n\r"), abs_path, line_no))

            line_no += 1
            i += 1

        include_stack.pop()
        return section_found

    _expand_recursive(top_file, include_stack, current_lang=dialect)
    return expanded_lines, ahdl_include_paths, spf_include_paths


def _resolve_include_path(raw_path: str, including_file: str, include_paths: list[str]) -> str:
    """Resolve include path using tilde expansion and search paths.

    Args:
        raw_path: Raw include path from directive.
        including_file: Absolute path of the file doing the including.
        include_paths: List of additional search directories.

    Returns:
        Absolute resolved path.

    Raises:
        IncludePathNotFoundError: If path cannot be resolved.
            This is a subclass of NetlistParseError; try-and-degrade callers
            catch IncludePathNotFoundError to allow graceful degradation.
    """
    # Guard against empty or whitespace-only paths
    if not raw_path or not raw_path.strip():
        raise IncludePathNotFoundError(f"Empty include path at {including_file}")

    # Tilde and environment variable expansion. Env vars commonly appear
    # in PDK paths (e.g. `.lib '$PDK_ROOT/models.lib' typ_section`). Unset
    # vars are left as literal `$VAR` per os.path.expandvars semantics; the
    # subsequent isfile checks will then fail and surface a normal
    # "include path not found" error (which try-and-degrade callers
    # downgrade to a WARNING).
    expanded = os.path.expandvars(os.path.expanduser(raw_path))

    # Absolute path — use as-is
    if os.path.isabs(expanded):
        if os.path.isfile(expanded):
            return expanded
        else:
            search_list = [expanded]
            raise IncludePathNotFoundError(
                f"Include path not found: {raw_path}\nSearched: {', '.join(search_list)}"
            )

    # Search relative to including file's directory
    includer_dir = os.path.dirname(including_file)
    candidate = os.path.join(includer_dir, expanded)
    if os.path.isfile(candidate):
        return os.path.abspath(candidate)

    # Search user-provided include paths
    search_list_items: list[str] = [candidate]
    for search_dir in include_paths:
        candidate = os.path.join(search_dir, expanded)
        if os.path.isfile(candidate):
            return os.path.abspath(candidate)
        search_list_items.append(candidate)

    raise IncludePathNotFoundError(
        f"Include path not found: {raw_path}\nSearched: {', '.join(search_list_items)}"
    )


def _parse_spice_include_directive(line: str) -> Optional[tuple[str, str] | str]:
    """Match SPICE include directive. Return (path, section) for .lib, path for .include/.inc.

    The third token of `.lib path token` is the section name selecting a
    `.lib SECTION ... .endl SECTION` block within the resolved file. It is
    NOT a library name. Section selection itself is not honored by the
    parser (try-and-degrade inlines the whole file).

    Args:
        line: Stripped line text.

    Returns:
        (path, section) tuple for .lib, path string for .include/.inc, or None.
    """
    # .include "path" or .include 'path' or .include path
    match = re.match(r"^\.include\s+['\"]?([^'\"]+)['\"]?\s*$", line, re.IGNORECASE)
    if match:
        path = match.group(1).strip()
        return path

    # .inc "path" or .inc 'path' or .inc path (alias for .include)
    match = re.match(r"^\.inc\s+['\"]?([^'\"]+)['\"]?\s*$", line, re.IGNORECASE)
    if match:
        path = match.group(1).strip()
        return path

    # .lib "path" section_name or .lib path section_name
    match = re.match(r"^\.lib\s+['\"]?([^'\"]+)['\"]?\s+(\S+)\s*$", line, re.IGNORECASE)
    if match:
        path = match.group(1).strip()
        section = match.group(2).strip()
        return (path, section)

    # .lib "path" (no section name — include entire file)
    match = re.match(r"^\.lib\s+['\"]?([^'\"]+)['\"]?\s*$", line, re.IGNORECASE)
    if match:
        path = match.group(1).strip()
        return (path, "")

    return None


def _parse_spectre_include_directive(line: str) -> Optional[tuple[str, str]]:
    """Match Spectre include directive. Return (path, section) or None.

    Spectre supports a `section=NAME` keyword to select a named section from
    a .scs/.slib file (analogous to SPICE `.lib path section_name`). When the
    section keyword is absent, the second element of the returned tuple is an
    empty string. Section selection itself is not honored by the parser
    (try-and-degrade inlines the whole file).

    Args:
        line: Stripped line text (case-sensitive).

    Returns:
        (path, section) tuple where section is '' if no `section=` keyword,
        or None if the line is not a Spectre include directive.
    """
    # Spectre: include "path" section=NAME (section= form)
    match = re.match(r'^include\s+"([^"]+)"\s+section=(\S+)\s*$', line)
    if match:
        return (match.group(1), match.group(2))

    # Spectre: include "path" (bare form, case-sensitive, quotes required per spec)
    match = re.match(r'^include\s+"([^"]+)"\s*$', line)
    if match:
        return (match.group(1), "")

    return None


def _parse_spectre_ahdl_include_directive(line: str) -> Optional[str]:
    """Match Spectre ahdl_include directive. Return the quoted path or None.

    Spectre directive: ahdl_include "path" (quoted form only).
    The path is typically a Verilog-A (.va) file containing module definitions
    that will be parsed and merged into the parent netlist's subckt library.

    Args:
        line: Stripped line text (case-sensitive).

    Returns:
        The quoted path string on match, or None if the line is not
        an ahdl_include directive.
    """
    # ahdl_include "path" (quoted form only)
    match = re.match(r'^ahdl_include\s+"([^"]+)"\s*$', line)
    if match:
        return match.group(1)
    return None


def _parse_spectre_dspf_include_directive(line: str) -> Optional[str]:
    """Match Spectre dspf_include directive. Return the quoted path or None.

    Spectre directive: dspf_include "path" [bus_delim="..." ...] (quoted form only).
    The path is dispatched to the SPF parser regardless of file extension.
    Trailing arguments like bus_delim="..." are ignored.

    Args:
        line: Stripped line text (case-sensitive).

    Returns:
        The quoted path string on match, or None if the line is not
        a dspf_include directive.
    """
    # dspf_include "path" [optional trailing args like bus_delim="..."]
    match = re.match(r'^dspf_include\s+"([^"]+)"', line)
    if match:
        return match.group(1)
    return None


