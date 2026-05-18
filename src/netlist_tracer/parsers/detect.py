from __future__ import annotations

import gzip
import re
from typing import Optional


def _recognize_va_module(content: str) -> bool:
    """Detect Verilog-A module based on structural markers.

    Checks for either 'electrical' port type or 'analog begin' block
    within the first 4 KB of content.

    Inputs:
        content: File content (typically first 4 KB)

    Outputs:
        True if Verilog-A markers detected, False otherwise
    """
    # Check for 'electrical' keyword (Verilog-A port type)
    if re.search(r"\belectrical\b", content, re.IGNORECASE):
        return True

    # Check for 'analog begin' block (Verilog-A behavioral section)
    if re.search(r"\banalog\s+begin\b", content, re.IGNORECASE):
        return True

    return False


def _score_content(content: str) -> dict[str, int]:
    """Scan content and accumulate per-format marker weights.

    Scores all formats by scanning for distinguishing markers.
    Weights reflect marker specificity: unambiguous markers (EDIF,
    explicit Spectre directives) have higher weights than ambiguous
    ones (.subckt could be both Spectre and SPICE-family).

    Inputs:
        content: File content (typically first 4 KB)

    Outputs:
        Dict mapping format name to accumulated score
    """
    scores: dict[str, int] = {
        "edif": 0,
        "verilog": 0,
        "spectre": 0,
        "cdl": 0,
        "spice": 0,
        "spf": 0,
        "spef": 0,
    }

    # EDIF: s-expression prefix is unmistakable
    if re.search(r"\(edif\b", content):
        scores["edif"] += 10

    # SPEF: *SPEF header marker (IEEE 1481 standard header)
    if re.search(r"\*SPEF\b", content):
        scores["spef"] += 10

    # SPEF fallback markers: *D_NET or *NAME_MAP (if no *SPEF header)
    if re.search(r"\*D_NET\b", content) or re.search(r"\*NAME_MAP\b", content):
        scores["spef"] += 5

    # SPF/DSPF: *|DSPF, *|RSPF, or *|CCSPF marker (highest SPICE-family priority)
    if re.search(r"\*\|(DSPF|RSPF|CCSPF)", content):
        scores["spf"] += 10

    # Verilog-A: electrical or analog begin block
    if _recognize_va_module(content):
        scores["verilog"] += 5

    # Verilog: module declaration
    if re.search(r"\bmodule\s+\w+", content):
        scores["verilog"] += 5

    # Verilog: endmodule keyword
    if re.search(r"\bendmodule\b", content):
        scores["verilog"] += 3

    # Spectre: explicit directive
    if re.search(r"simulator\s+lang=spectre", content):
        scores["spectre"] += 10

    # Spectre: bare subckt (no dot), case-sensitive
    if re.search(r"(?:^|\n)subckt\s+\w+", content, re.MULTILINE):
        scores["spectre"] += 5

    # CDL: auCdl-specific marker
    if re.search(r"\*\.PININFO", content):
        scores["cdl"] += 8

    # SPICE-family: .subckt directive (case-insensitive)
    if re.search(r"(?:^|\n)\.subckt\s+\w+", content, re.MULTILINE | re.IGNORECASE):
        scores["spice"] += 5

    # SPICE-family: .ends terminator
    if re.search(r"(?:^|\n)\.ends\b", content, re.MULTILINE | re.IGNORECASE):
        scores["spice"] += 2

    # SPICE-family: .global directive
    if re.search(r"(?:^|\n)\.global\b", content, re.MULTILINE | re.IGNORECASE):
        scores["spice"] += 2

    return scores


def _extension_hint(filepath: str) -> Optional[str]:
    """Return suggested format from file extension.

    Used only as tiebreaker when content scores are tied or zero.
    If path ends with .gz, strips it and re-checks the underlying extension.

    Inputs:
        filepath: File path

    Outputs:
        Format name string or None if extension not recognized
    """
    ext_map = {
        ".edif": "edif",
        ".edn": "edif",
        ".edf": "edif",
        ".va": "verilog",
        ".vams": "verilog",
        ".vha": "verilog",
        ".v": "verilog",
        ".sv": "verilog",
        ".psv": "verilog",
        ".scs": "spectre",
        ".cdl": "cdl",
        ".sp": "spice",
        ".spi": "spice",
        ".cir": "spice",
        ".ckt": "spice",
        ".spf": "spf",
        ".dspf": "spf",
        ".spef": "spef",
    }

    lower_path = filepath.lower()

    # Strip .gz suffix if present and re-check underlying extension
    if lower_path.endswith(".gz"):
        lower_path = lower_path[:-3]

    for ext, fmt in ext_map.items():
        if lower_path.endswith(ext):
            return fmt

    return None


def _pick_format(scores: dict[str, int], ext_hint: Optional[str]) -> str:
    """Apply tiebreaker logic to scores + extension hint.

    Implements priority rules:
    1. CDL wins if cdl score > 0 and spice score > 0 (CDL is superset)
    2. Verilog wins if verilog >= spice (module is unambiguous)
    3. Format with max score wins; ties use extension hint
    4. Zero scores fall back to extension hint
    5. Final fallback: 'spice' (legacy default)

    Inputs:
        scores: Dict from _score_content()
        ext_hint: Format name from _extension_hint() or None

    Outputs:
        Format name string ('spice', 'cdl', 'spectre', 'verilog', 'edif')
    """
    # Rule 1: CDL vs SPICE tiebreaker (both have markers, CDL is superset)
    if scores["cdl"] > 0 and scores["spice"] > 0:
        return "cdl"

    # Rule 2: Verilog vs SPICE tiebreaker (both present, module is unambiguous)
    if scores["verilog"] > 0 and scores["spice"] > 0 and scores["verilog"] >= scores["spice"]:
        return "verilog"

    # Find maximum score
    max_score = max(scores.values())

    # Rule 3: If max score is 0, use extension hint
    if max_score == 0:
        if ext_hint:
            return ext_hint
        return "spice"

    # Find all formats with max score (ties)
    tied_formats = [fmt for fmt, score in scores.items() if score == max_score]

    # Rule 4a: Single clear winner
    if len(tied_formats) == 1:
        return tied_formats[0]

    # Rule 4b: Tie -- use extension hint if it matches one of the tied formats
    if ext_hint and ext_hint in tied_formats:
        return ext_hint

    # Rule 4c: Tie with no matching extension hint -- pick by priority
    priority = ["edif", "spectre", "verilog", "cdl", "spef", "spf", "spice"]
    for fmt in priority:
        if fmt in tied_formats:
            return fmt

    return "spice"


def detect_format(filepaths: list[str]) -> str:
    """Detect netlist format from file content (content-first scoring).

    Reads first 4 KB of each file, accumulates per-format marker scores,
    and applies tiebreaker logic. Extension is used as tiebreaker hint
    only when content provides ambiguous or zero signal.

    Distinguishing markers (by specificity):
    - EDIF:     '(edif ' s-expression prefix (weight 10)
    - SPEF:     '*SPEF' header marker (weight 10)
                '*D_NET' or '*NAME_MAP' fallback markers (weight 5)
    - SPF:      '*|DSPF', '*|RSPF', '*|CCSPF' content marker (weight 10)
    - Spectre:  'simulator lang=spectre' directive (weight 10)
                bare 'subckt <name>' (weight 5)
    - Verilog:  'module <name>' keyword (weight 5)
                'electrical' or 'analog begin' for Verilog-A (weight 5)
                'endmodule' keyword (weight 3)
    - CDL:      '*.PININFO' comment line (weight 8)
    - SPICE:    '.subckt' directive (weight 5)
                '.ends' terminator (weight 2)
                '.global' directive (weight 2)

    Args:
        filepaths: List of file paths to scan for format markers.

    Returns:
        Format string: 'edif', 'verilog', 'spectre', 'cdl', 'spef', 'spf', or 'spice'.
    """
    if not filepaths:
        return "spice"

    all_scores: dict[str, int] = {
        "edif": 0,
        "verilog": 0,
        "spectre": 0,
        "cdl": 0,
        "spice": 0,
        "spf": 0,
        "spef": 0,
    }

    for filepath in filepaths:
        # Handle .gz files with gzip.open; use errors='replace' for corrupted bytes
        if filepath.lower().endswith(".gz"):
            with gzip.open(filepath, "rt", encoding="utf-8", errors="replace") as f:
                content = f.read(4096)
        else:
            with open(filepath, encoding="utf-8", errors="replace") as f:
                content = f.read(4096)

        scores = _score_content(content)
        for fmt in all_scores:
            all_scores[fmt] += scores[fmt]

    # Use extension hint from first file (typical case: homogeneous file list)
    ext_hint = _extension_hint(filepaths[0])

    return _pick_format(all_scores, ext_hint)


def detect_format_per_file(filepaths: list[str]) -> dict[str, list[str]]:
    """Detect format of each file individually and group by detected format.

    Runs detect_format() on each file individually, grouping results by
    detected format. Files within each group are sorted alphabetically for
    stable iteration order in downstream merge operations.

    Args:
        filepaths: List of file paths to detect. Must be non-empty.

    Returns:
        Dict mapping format string to sorted list of file paths:
        {'spice': [...], 'verilog': [...], ...}
    """
    frmt_grps: dict[str, list[str]] = {}

    for filepath in filepaths:
        frmt = detect_format([filepath])
        if frmt not in frmt_grps:
            frmt_grps[frmt] = []
        frmt_grps[frmt].append(filepath)

    # Sort file lists alphabetically within each format group
    for frmt in frmt_grps:
        frmt_grps[frmt].sort()

    return frmt_grps
