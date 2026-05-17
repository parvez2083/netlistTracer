"""Data models for netlist_tracer: SubcktDef, Instance, and alias merging."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field


@dataclass
class SubcktDef:
    """Represents a .SUBCKT/module definition"""

    name: str
    pins: list[str]
    pin_to_pos: dict[str, int] = field(default_factory=dict)
    # Map of net -> canonical-net for SystemVerilog `assign A = B;` aliases.
    # Two nets that share a canonical are connected by a continuous-assign.
    aliases: dict[str, str] = field(default_factory=dict)
    # Arbitrary metadata attached by format-specific parsers (SPF caps, ground nets, pin aliases, etc.)
    params: dict = field(default_factory=dict)
    # Lazy SPF parsing: True when SubcktDef was registered from a peek-only scan;
    # pins populated but body (instances/aliases) deferred. Runtime-only, NOT serialized.
    is_placeholder: bool = False
    # Path to SPF file that will populate body on materialization. None when not a placeholder.
    # Runtime-only, NOT serialized.
    placeholder_source: str | None = None

    def __post_init__(self) -> None:
        self.pin_to_pos = {pin: i for i, pin in enumerate(self.pins)}


@dataclass
class Instance:
    """Represents an instance within a subckt/module"""

    name: str
    cell_type: str
    nets: list[str]
    parent_cell: str
    params: dict = field(default_factory=dict)  # format-specific metadata (EDIF properties, etc.)


def merge_aliases_into_subckt(sub: SubcktDef, pairs: Iterable[tuple[str, str]]) -> None:
    """Merge a list of (lhs, rhs) `assign` pairs into sub.aliases using
    union-find. Port names (members of sub.pins) are preferred as roots so
    that going DOWN from a parent into the cell still resolves to the
    port-named net. Existing aliases on sub are kept and unioned with the
    new pairs.

    Args:
        sub: The SubcktDef to merge aliases into.
        pairs: List of (lhs, rhs) tuples representing alias pairs.
    """
    pins_set = set(sub.pins)
    parent: dict[str, str] = {}

    # Seed union-find with existing aliases
    for n, c in sub.aliases.items():
        parent.setdefault(n, n)
        parent.setdefault(c, c)

    # Pre-seed parent dict with all pair endpoints
    for lhs, rhs in pairs:
        if lhs != rhs:
            if lhs not in parent:
                parent[lhs] = lhs
            if rhs not in parent:
                parent[rhs] = rhs

    def find(x: str) -> str:
        # Iterative path-compression
        root = x
        while parent[root] != root:
            root = parent[root]
        while parent[x] != root:
            parent[x], x = root, parent[x]
        return root

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra == rb:
            return
        a_is_port = ra in pins_set
        b_is_port = rb in pins_set
        if a_is_port and not b_is_port:
            parent[rb] = ra
        elif b_is_port and not a_is_port:
            parent[ra] = rb
        else:
            # Deterministic tie-break
            if ra < rb:
                parent[rb] = ra
            else:
                parent[ra] = rb

    # Replay existing alias edges first
    for n, c in sub.aliases.items():
        union(n, c)
    for lhs, rhs in pairs:
        if lhs == rhs:
            continue
        union(lhs, rhs)

    # Build canonical map: full path compression + dict comprehension
    for net in list(parent.keys()):
        find(net)  # full path compression
    new_aliases = {net: root for net, root in parent.items() if root != net}
    sub.aliases = new_aliases
