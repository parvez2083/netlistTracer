#!/usr/bin/env python3
################################################################################
# AI GENERATED CODE - Review and test before production use
# Author: AI Generated | Date: 2026-05-10
#
# Description: Cross-parser-shareable numeric value parsing helpers. Handles
# SPICE-style unit suffixes (T, G, MEG, K, M, U, μ, N, P, F, A) used by SPICE,
# Spectre, Verilog localparam evaluation, EDIF, and DSPF/SPEF numeric literals.
#
# Usage: from netlist_tracer.parsers._numerics import parse_numerical
#   Example: parse_numerical("1.5MEG") -> 1500000.0
#
# Changelog:
#   2026-05-10 - Renamed from spice_helpers.py; helpers are cross-parser-shareable
################################################################################

from typing import Optional

################################################################################
# SECTION: Numerical Parsing
# Description: Parse numeric values with unit suffixes (T, G, MEG, K, M, U, μ,
# N, P, F, A) using longest-suffix-match-first algorithm.
################################################################################

# Unit mapping: key is suffix (lowercase for consistency), value is multiplier
_UNIT_MAP = {
    "t": 1e12,
    "g": 1e9,
    "meg": 1e6,
    "k": 1e3,
    "m": 1e-3,  # HSPICE: M = milli, not mega
    "u": 1e-6,
    "μ": 1e-6,  # Unicode mu (micro)
    "n": 1e-9,
    "p": 1e-12,
    "f": 1e-15,
    "a": 1e-18,
}

# Sort by length descending (longest-suffix-match-first)
_SORTED_UNIT_KEYS = sorted(_UNIT_MAP.keys(), key=len, reverse=True)


def parse_numerical(value_str: str) -> Optional[float]:
    """
    Parse a numeric value string with optional unit suffix.

    Handles HSPICE explicit and conventional unit suffixes:
    - T=1e12, G=1e9, MEG=1e6, K=1e3, M=1e-3 (milli), U=1e-6, μ=1e-6,
      N=1e-9, P=1e-12, F=1e-15, A=1e-18

    Uses longest-suffix-match-first to correctly parse '1MEG' as 1e6 (not 1e-3).
    Falls back to float() for plain scientific notation.

    Inputs:
        value_str: String containing numeric value with optional suffix.
                   Examples: "1.5MEG", "2e-3", "10K", "1u", "3.14"

    Outputs:
        float - Parsed numerical value, or None if parsing fails
    """
    if not value_str or not isinstance(value_str, str):
        return None

    value_str = value_str.strip()
    if not value_str:
        return None

    # Try to match suffix (case-insensitive)
    # Use lowercase for comparison, but check original string too for unicode
    lower_str = value_str.lower()
    for suffix in _SORTED_UNIT_KEYS:
        if lower_str.endswith(suffix):
            numeric_part = value_str[: -len(suffix)].strip()
            try:
                base_value = float(numeric_part)
                return base_value * _UNIT_MAP[suffix]
            except ValueError:
                return None

    # No suffix matched; try plain float (scientific notation, etc.)
    try:
        return float(value_str)
    except ValueError:
        return None
