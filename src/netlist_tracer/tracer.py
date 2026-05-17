from __future__ import annotations

import re
import sys
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional, cast

from netlist_tracer.parser import NetlistParser

################################################################################
# Module Constants: Lateral Walk Configuration
################################################################################
_LATERAL_WALK_THRU_TYPES = frozenset({"R", "L"})
"""Cell types that allow galvanic lateral walk (thru): resistors, inductors."""

_LATERAL_SKIP_TYPES = frozenset({"C", "K"})
"""Cell types skipped entirely during lateral walk: caps and coupling.
Treated as parasitic noise irrelevant for connectivity tracing."""


def _is_primitive_shell(parser: NetlistParser, subckt: Any) -> bool:
    """Check if a subckt is a synthesized primitive shell with no body.

    A subckt is a synthesized primitive shell if:
    - It has no aliases (internal net mappings)
    - It has no instances (no body)
    - Its pins match the synthesized-primitive naming convention (short single
      uppercase letters/numbers like D, G, S, B, A, K, or numeric 1, 2, ...)

    This distinguishes synthesized SPICE primitives (e.g., MOSFET model
    cell_types with D/G/S/B pin shells) from normal Verilog leaf modules
    (which have arbitrary port names like clk, resetn, data[31:0]). The
    tracer treats primitive shells as leaf
    primitives for traversal — pin labels are still used for endpoint
    emission and pin validation, but descent/walk-up logic is bypassed so
    we fall through to lateral-walk endpoint emission.

    Inputs:
        parser: NetlistParser (for accessing instances_by_parent)
        subckt: SubcktDef instance

    Outputs:
        bool — True if the subckt matches synthesized-primitive characteristics
    """
    if subckt.aliases or parser.instances_by_parent.get(subckt.name, []):
        return False

    # Check pin names: synthesized primitives have short single-letter or
    # numeric pins. Real Verilog modules have longer names with buses, e.g.
    # 'clk', 'resetn', 'data[31:0]', 'waddr[5:0]'.
    for pin in subckt.pins:
        # Strip bus brackets if present
        base_pin = re.sub(r"\[\d+(?::\d+)?\]$", "", pin)
        # Synthesized pins are 1-2 chars of uppercase letters/digits
        # Real Verilog port names are longer or contain lowercase
        if not (1 <= len(base_pin) <= 2 and base_pin.isupper() and base_pin.replace("_", "").isalnum()):
            return False

    return True


def _spf_pin_from_net(net: str) -> str | None:
    """Extract pin name from SPF instance-terminal form '<instance>:<pin>'.

    SPF parasitic netlists encode instance pin references as
    '<instance_name_without_X_prefix>:<pin_name>', e.g.
    'inst/M1:G' for the gate of MOSFET inst/M1. The suffix after
    the last ':' is the pin name (D/G/S/B for MOSFETs; other letters or
    multi-character names for other devices).

    Returns the pin name when the suffix is a non-empty alphabetic token,
    None when the net is a flat name (no ':') or the suffix is numeric
    (parasitic subnodes are already collapsed earlier; any residual ':N'
    form is not a real pin).
    """
    idx = net.rfind(":")
    if idx < 0:
        return None
    suffix = net[idx + 1 :]
    if not suffix or suffix.isdigit():
        return None
    return suffix


_BUS_BRKT_RE = re.compile(r"(?:\[\d+\]|<\d+>)$")


def suggest_pins(query: str, pins: list[str]) -> list[str]:
    """Return ordered, deduplicated suggestion list for a not-found pin query.

    Strategy:
      1. Strip bus-bracket suffixes ([N], <N>) from each pin to get base names.
      2. Case-insensitive substring containment: pins whose base contains the
         query are listed first (catches short queries inside long pin names,
         e.g. 'vref' inside 'bg_vrefOut_VDDA_ana').
      3. Difflib fuzzy matches (cutoff 0.6) appended for typo handling, skipping
         duplicates of the substring hits.

    Used by both the tracer's pin-not-found path and the CLI's peek pre-validation
    path so suggestion quality stays consistent across entry points.
    """
    import difflib

    bases = list(dict.fromkeys(_BUS_BRKT_RE.sub("", p) for p in pins))
    q = query.lower()
    seen: set[str] = set()
    sgg: list[str] = []
    for b in bases:
        if q in b.lower() and b not in seen:
            seen.add(b)
            sgg.append(b)
    for s in difflib.get_close_matches(query, bases, n=10, cutoff=0.6):
        if s not in seen:
            seen.add(s)
            sgg.append(s)
    return sgg


@dataclass
class TraceStep:
    """Represents one step in a trace path."""

    cell: str
    pin_or_net: str
    direction: str  # 'start', 'down', 'up', 'alias', 'thru', or 'endpoint'
    instance_name: Optional[str] = None
    inst_stack: tuple[tuple[str, str], ...] = ()


class BidirectionalTracer:
    """Traces signals bidirectionally through netlist hierarchy."""

    def __init__(self, parser: NetlistParser) -> None:
        """Initialize tracer with a parsed netlist.

        Args:
            parser: NetlistParser instance containing parsed netlist data.
        """
        self.parser = parser
        self._equiv_cache: dict[str, dict[str, list[str]]] = {}

    def _mtrl_if_pndg(self, cell_nm: str) -> bool:
        """Check if cell_name has pending SPF; if so, materialize it.

        Used at descent points in the BFS to trigger lazy materialization
        when needed.

        Inputs:
            cell_nm: Cell type the BFS is about to descend into

        Outputs:
            bool — True if materialization occurred (caller may need to refresh
            child_subckt reference)
        """
        # Check if this cell is a pending placeholder
        if cell_nm not in self.parser.pndg_spf_fls:
            return False

        spf_pth = self.parser.pndg_spf_fls[cell_nm]
        cnt = self.parser.mtrl_spf(spf_pth)
        return cnt > 0

    def _equivalence_class(self, cell: str, net: str) -> list[str]:
        """Return all nets equivalent to `net` in `cell` via aliases."""
        sub = self.parser.subckts.get(cell)
        if not sub or not sub.aliases:
            return [net]
        canon_index = self._equiv_cache.get(cell)
        if canon_index is None:
            canon_index = {}
            for n, c in sub.aliases.items():
                canon_index.setdefault(c, []).append(n)
            for _c, lst in canon_index.items():
                lst.sort()
            self._equiv_cache[cell] = canon_index
        canon = sub.aliases.get(net, net)
        equiv = list(canon_index.get(canon, ()))
        if canon not in equiv:
            equiv.insert(0, canon)
        if net not in equiv:
            equiv.insert(0, net)
        return equiv

    def _split_path(self, path: str) -> list[str]:
        """Split hierarchical path, respecting [...] brackets."""
        segments = []
        buf = []
        depth = 0
        for ch in path:
            if ch == "[":
                depth += 1
                buf.append(ch)
            elif ch == "]":
                depth = max(0, depth - 1)
                buf.append(ch)
            elif (ch == "." or ch == "/") and depth == 0:
                if buf:
                    segments.append("".join(buf))
                    buf = []
            else:
                buf.append(ch)
        if buf:
            segments.append("".join(buf))
        return segments

    def _resolve_hierarchical(
        self, segments: list[str]
    ) -> list[tuple[str, tuple[tuple[str, str], ...]]]:
        """Resolve hierarchical instance path."""
        n = len(segments)
        results: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        stack: list[tuple[int, str | None, tuple[tuple[str, str], ...]]] = [(0, None, ())]
        while stack:
            i, parent, chain = stack.pop()
            if i >= n:
                if chain and parent is not None:
                    results.append((parent, chain))
                continue
            for j in range(n, i, -1):
                candidate = ".".join(segments[i:j])
                if parent is None:
                    insts = self.parser.instances_by_name.get(candidate, [])
                else:
                    insts = [
                        inst
                        for inst in self.parser.instances_by_parent.get(parent, [])
                        if inst.name == candidate
                    ]
                for inst in insts:
                    new_chain = chain + ((inst.name, inst.parent_cell),)
                    stack.append((j, inst.cell_type, new_chain))
        return results

    def resolve_name(self, name: str) -> list[tuple[str, tuple[tuple[str, str], ...] | None]]:
        """Resolve a name to (cell_type, inst_chain).

        Args:
            name: Cell type name or instance name (flat or hierarchical).

        Returns:
            List of (cell_type, inst_chain) tuples.
        """
        if name in self.parser.subckts:
            return [(name, None)]

        matches: list[tuple[str, tuple[tuple[str, str], ...] | None]] = [
            (inst.cell_type, ((inst.name, inst.parent_cell),))
            for inst in self.parser.instances_by_name.get(name, [])
        ]

        if not matches:
            corrected_name = None
            if self.parser.format in ("cdl", "spice") and not name.startswith("X"):
                corrected_name = "X" + name
            elif self.parser.format in ("verilog", "spectre") and name.startswith("X"):
                corrected_name = name[1:]

            if corrected_name:
                matches = [
                    (inst.cell_type, ((inst.name, inst.parent_cell),))
                    for inst in self.parser.instances_by_name.get(corrected_name, [])
                ]
                if matches:
                    print(f"  (auto-corrected '{name}' to '{corrected_name}')")

        if not matches:
            segments = self._split_path(name)
            if len(segments) >= 2:
                # cast: _resolve_hierarchical returns non-None chains; None is only possible via other resolve_name branches
                matches = cast(
                    list[tuple[str, tuple[tuple[str, str], ...] | None]],
                    self._resolve_hierarchical(segments),
                )

        return matches

    def _enumerate_ancestor_chains(
        self, cell_type: str, _seen: Optional[set[str]] = None
    ) -> list[tuple[tuple[str, str], ...]]:
        """Enumerate all ancestor chains for a cell type."""
        if _seen is None:
            _seen = set()
        if cell_type in _seen:
            return []
        insts = self.parser.instances_by_celltype.get(cell_type, [])
        if not insts:
            return [()]
        _seen = _seen | {cell_type}
        chains: list[tuple[tuple[str, str], ...]] = []
        for inst in insts:
            parent_chains = self._enumerate_ancestor_chains(inst.parent_cell, _seen)
            for pc in parent_chains:
                chains.append(pc + ((inst.name, inst.parent_cell),))
        return chains

    def trace(
        self,
        start_name: str,
        start_pin: str,
        target_name: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> list[list[TraceStep]]:
        """Trace signal paths from start to target.

        Args:
            start_name: Starting cell or instance name.
            start_pin: Starting pin name.
            target_name: Optional target cell or instance name.
            max_depth: Optional maximum path depth.

        Returns:
            List of trace paths (each path is a list of TraceStep objects).
        """
        start_matches = self.resolve_name(start_name)
        if not start_matches:
            print(f"ERROR: '{start_name}' not found as cell type or instance name", file=sys.stderr)
            # Suggest similar cell names using fuzzy matching
            import difflib

            all_cells = list(self.parser.subckts.keys())
            suggestions = difflib.get_close_matches(start_name, all_cells, n=10, cutoff=0.6)
            if suggestions:
                print(f"Did you mean: {suggestions}", file=sys.stderr)
            return []

        if max_depth is None and re.match(r"^(VDD|VSS)", start_pin):
            max_depth = 1

        target_set = set()
        if target_name:
            target_matches = self.resolve_name(target_name)
            if not target_matches:
                print(
                    f"ERROR: '{target_name}' not found as cell type or instance name",
                    file=sys.stderr,
                )
                # Suggest similar cell names using fuzzy matching
                import difflib

                all_cells = list(self.parser.subckts.keys())
                suggestions = difflib.get_close_matches(target_name, all_cells, n=10, cutoff=0.6)
                if suggestions:
                    print(f"Did you mean: {suggestions}", file=sys.stderr)
                return []
            for cell_type, inst_chain in target_matches:
                leaf_ctx = inst_chain[-1] if inst_chain else None
                target_set.add((cell_type, leaf_ctx))

        seeds: list[tuple[str, tuple[tuple[str, str], ...]]] = []
        for start_cell, start_inst_chain in start_matches:
            subckt = self.parser.subckts.get(start_cell)
            if not subckt or start_pin not in subckt.pin_to_pos:
                print(f"ERROR: Pin '{start_pin}' not found in cell '{start_cell}'", file=sys.stderr)
                if subckt:
                    # NOTE: trace_pins() expands bare bus names via
                    # expand_pin() before reaching this point. This branch
                    # only fires when trace() is called directly (library
                    # API) with a bus-base name. In that case, suggest the
                    # actual indexed members rather than echoing the base.
                    bus_members = [
                        p
                        for p in subckt.pins
                        if p != start_pin
                        and re.search(r"(?:\[\d+\]|<\d+>)$", p)
                        and _BUS_BRKT_RE.sub("", p) == start_pin
                    ]
                    if bus_members:
                        print(f"Did you mean: {bus_members[:10]}")
                    else:
                        suggestions = suggest_pins(start_pin, subckt.pins)
                        if suggestions:
                            print(f"Did you mean: {suggestions[:10]}")
                        else:
                            print(
                                f"Available pins ({len(subckt.pins)} total): {subckt.pins[:10]}..."
                            )
                continue
            if start_inst_chain is None:
                chains = self._enumerate_ancestor_chains(start_cell)
                if not chains:
                    seeds.append((start_cell, ()))
                else:
                    for chain in chains:
                        seeds.append((start_cell, chain))
            elif len(start_inst_chain) == 1:
                inst_name, parent_cell = start_inst_chain[0]
                parent_chains = self._enumerate_ancestor_chains(parent_cell)
                if not parent_chains:
                    seeds.append((start_cell, start_inst_chain))
                else:
                    for pc in parent_chains:
                        seeds.append((start_cell, pc + start_inst_chain))
            else:
                seeds.append((start_cell, start_inst_chain))

        all_paths = []

        for start_cell, initial_stack in seeds:
            # Use the shared BFS helper with the initial stack context
            current_inst_name: Optional[str] = initial_stack[-1][0] if initial_stack else None
            paths = self._bfs_from_seed(
                start_cell,
                start_pin,
                target_name=target_name,
                max_depth=max_depth,
                initial_stack=initial_stack,
                initial_inst_name=current_inst_name,
            )
            all_paths.extend(paths)

        return all_paths

    def expand_pin(self, subckt: Any, name: str) -> list[str]:
        """Expand a pin name to its bit-level members.

        - If `name` exists exactly in subckt.pin_to_pos: returns [name].
        - If `name` is a bare bus base (e.g. 'data' when only 'data[0]'..
          'data[N]' exist as pins): returns all indexed members.
        - Otherwise: returns [] (unknown pin).

        Used by `trace_pins` to support both bit-level and bus-name forms.
        """
        if name in subckt.pin_to_pos:
            return [name]
        return [
            p
            for p in subckt.pins
            if p != name
            and re.search(r"(?:\[\d+\]|<\d+>)$", p)
            and re.sub(r"(?:\[\d+\]|<\d+>)$", "", p) == name
        ]

    def _bfs_from_seed(
        self,
        start_cell: str,
        start_net: str,
        target_name: Optional[str] = None,
        max_depth: Optional[int] = None,
        initial_stack: tuple[tuple[str, str], ...] | None = None,
        initial_inst_name: Optional[str] = None,
    ) -> list[list[TraceStep]]:
        """
        Core BFS traversal logic: start from a seed cell and net, explore paths.

        This is a private helper shared by trace() and trace_net() to avoid duplication.

        Inputs:
            start_cell: Starting subckt cell type
            start_net: Starting net/pin name within start_cell
            target_name: Optional target cell/instance name
            max_depth: Optional path depth cap
            initial_stack: Optional initial instance stack (hierarchical context)
            initial_inst_name: Optional initial instance name for the TraceStep

        Outputs:
            List of trace paths (each path is a list of TraceStep objects)
        """
        if initial_stack is None:
            initial_stack = ()

        initial_step = TraceStep(
            cell=start_cell,
            pin_or_net=start_net,
            direction="start",
            instance_name=initial_inst_name,
            inst_stack=initial_stack,
        )

        queue: deque[tuple[str, str, tuple[tuple[str, str], ...], list[TraceStep]]] = deque(
            [(start_cell, start_net, initial_stack, [initial_step])]
        )
        visited = set()
        all_paths = []

        # Resolve target once if provided
        target_set = set()
        if target_name:
            target_matches = self.resolve_name(target_name)
            for cell_type, inst_chain in target_matches:
                leaf_ctx = inst_chain[-1] if inst_chain else None
                target_set.add((cell_type, leaf_ctx))

        while queue:
            curr_cell, curr_net, inst_stack, path = queue.popleft()

            state_key = (curr_cell, curr_net, inst_stack)
            if state_key in visited:
                continue
            visited.add(state_key)

            if curr_net == "":
                continue

            if re.match(r"^\d+'[bdohBDOH][0-9a-fA-FxXzZ_?]+$", curr_net):
                if len(path) > 1:
                    all_paths.append(path)
                continue

            if re.match(r"^(VDD|VSS)", curr_net) and len(path) > 1:
                all_paths.append(path)
                continue

            if max_depth is not None and len(path) > max_depth:
                all_paths.append(path)
                continue

            if target_name:
                curr_ctx = inst_stack[-1] if inst_stack else None
                if (curr_cell, curr_ctx) in target_set or (curr_cell, None) in target_set:
                    all_paths.append(path)
                    continue

            queue_len_before = len(queue)
            all_paths_len_before = len(all_paths)
            path_cells = {(s.cell, s.pin_or_net) for s in path}

            for equiv_net in self._equivalence_class(curr_cell, curr_net):
                if equiv_net == curr_net:
                    continue
                if (curr_cell, equiv_net) in path_cells:
                    continue
                alias_step = TraceStep(
                    cell=curr_cell,
                    pin_or_net=equiv_net,
                    direction="alias",
                    instance_name=None,
                    inst_stack=inst_stack,
                )
                queue.append((curr_cell, equiv_net, inst_stack, path + [alias_step]))

            # Lazy SPF: materialize pending curr_cell so instances_by_parent is fresh
            self._mtrl_if_pndg(curr_cell)

            for inst in self.parser.instances_by_parent.get(curr_cell, []):
                if curr_net not in inst.nets:
                    continue
                net_pos = inst.nets.index(curr_net)

                # Lazy SPF: materialize child before lookup
                self._mtrl_if_pndg(inst.cell_type)
                child_subckt = self.parser.subckts.get(inst.cell_type)

                # Hierarchical descent: if child_subckt exists, has a body, and pin index is valid
                if child_subckt and not _is_primitive_shell(self.parser, child_subckt) and net_pos < len(child_subckt.pins):
                    child_pin = child_subckt.pins[net_pos]
                    if (inst.cell_type, child_pin) not in path_cells:
                        new_stack_down = inst_stack + ((inst.name, curr_cell),)
                        new_step = TraceStep(
                            cell=inst.cell_type,
                            pin_or_net=child_pin,
                            direction="down",
                            instance_name=inst.name,
                            inst_stack=new_stack_down,
                        )
                        queue.append(
                            (inst.cell_type, child_pin, new_stack_down, path + [new_step])
                        )

                # Lateral walk: leaf primitive (no SubcktDef) or empty primitive shell
                # Only R and L allow galvanic thru-walk; others are endpoints
                elif not child_subckt or _is_primitive_shell(self.parser, child_subckt):
                    # SPF convention: an instance terminal connection appears as
                    # '<instance>:<pin>' (e.g. 'inst/M1:G'). When present,
                    # extract the trailing pin name (D/G/S/B for MOSFETs, ...)
                    # so the trace shows real pin names instead of cell:position.
                    curr_pin_name = _spf_pin_from_net(curr_net)
                    if inst.cell_type.upper() in _LATERAL_WALK_THRU_TYPES:
                        # Galvanic thru-walk: emit "thru" and continue tracing.
                        # Label prefers the device value (e.g. <1.5ohm> for R,
                        # <2nH> for L) so the user sees physical magnitude;
                        # falls back to the SPF entry-pin name or
                        # cell_type:position when value is unavailable.
                        value = inst.params.get("_value") if hasattr(inst, "params") else None
                        if value:
                            unit = "ohm" if inst.cell_type.upper() == "R" else "H"
                            thru_label = f"<{value}{unit}>"
                        else:
                            thru_label = curr_pin_name or f"{inst.cell_type}:{net_pos}"
                        for alt_pos, alt_net in enumerate(inst.nets):
                            if alt_pos == net_pos:
                                continue  # Skip the current position
                            if (curr_cell, alt_net) in path_cells:
                                continue  # Skip if already visited

                            thru_step = TraceStep(
                                cell=inst.cell_type,
                                pin_or_net=thru_label,
                                direction="thru",
                                instance_name=inst.name,
                                inst_stack=inst_stack,
                            )
                            queue.append((curr_cell, alt_net, inst_stack, path + [thru_step]))
                    elif inst.cell_type.upper() in _LATERAL_SKIP_TYPES:
                        # Caps and coupling: parasitic noise, skip entirely
                        # (no step emitted, no walk)
                        continue
                    else:
                        # Real circuit element with non-galvanic terminals:
                        # transistors (X with model name), sources (V/I/B/E/F/G/H),
                        # or any other leaf primitive. Emit endpoint so the user
                        # sees where the trace terminated; do not walk further.
                        # Prefer synthesized pin label if child_subckt is a primitive shell.
                        if child_subckt and _is_primitive_shell(self.parser, child_subckt):
                            pin_label = child_subckt.pins[net_pos] if net_pos < len(child_subckt.pins) else curr_pin_name or f"{inst.cell_type}:{net_pos}"
                        else:
                            pin_label = curr_pin_name or f"{inst.cell_type}:{net_pos}"
                        endpoint_step = TraceStep(
                            cell=inst.cell_type,
                            pin_or_net=pin_label,
                            direction="endpoint",
                            instance_name=inst.name,
                            inst_stack=inst_stack,
                        )
                        path_with_endpoint = path + [endpoint_step]
                        if len(path_with_endpoint) > 1:
                            all_paths.append(path_with_endpoint)

            subckt = self.parser.subckts.get(curr_cell)
            if subckt and curr_net in subckt.pin_to_pos and inst_stack:
                pin_pos = subckt.pin_to_pos[curr_net]
                inst_name, parent_cell = inst_stack[-1]
                # Lazy SPF: materialize parent cell so it's accessible for walk-up
                self._mtrl_if_pndg(parent_cell)
                instances = [
                    i
                    for i in self.parser.instances_by_celltype.get(curr_cell, [])
                    if i.name == inst_name and i.parent_cell == parent_cell
                ]
                new_stack = inst_stack[:-1]

                for inst in instances:
                    if pin_pos >= len(inst.nets):
                        continue
                    parent_net = inst.nets[pin_pos]
                    if (inst.parent_cell, parent_net) in path_cells:
                        continue

                    new_step = TraceStep(
                        cell=inst.parent_cell,
                        pin_or_net=parent_net,
                        direction="up",
                        instance_name=inst.name,
                        inst_stack=new_stack,
                    )
                    queue.append((inst.parent_cell, parent_net, new_stack, path + [new_step]))

            # Dead-end detection: only fires when this iteration neither queued
            # a new state nor recorded a path via an endpoint emission. The extra
            # all_paths-grew guard prevents the fragment-duplication seen when a
            # lateral-walk endpoint records [start, thru(R), endpoint(X)] and the
            # dead-end heuristic would then also record the prefix [start, thru(R)].
            if (
                not target_name
                and len(queue) == queue_len_before
                and len(all_paths) == all_paths_len_before
                and len(path) > 1
            ):
                all_paths.append(path)

        return all_paths

    def trace_pins(
        self,
        start_name: str,
        pins: Optional[list[str]] = None,
        target_name: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> dict[str, list[list[TraceStep]]]:
        """Trace multiple pins at once.

        Returns a dictionary mapping pin names to their trace paths.
        If `pins` is None, traces all bit-level entries in the cell's pin_to_pos.
        Bare bus base names (e.g. 'data' when only 'data[0]'..'data[N]' exist
        as pins) are expanded to all indexed members. Unknown pin names are
        passed through to `trace()` which reports an error.

        Args:
            start_name: Starting cell or instance name.
            pins: List of pin names to trace. If None, traces all bit-level pins.
            target_name: Optional target cell or instance name.
            max_depth: Optional maximum path depth.

        Returns:
            Dictionary mapping pin_name -> list of trace paths (each path is a list of TraceStep objects).
        """
        # Resolve start_name to cell_type
        start_matches = self.resolve_name(start_name)
        if not start_matches:
            print(f"ERROR: '{start_name}' not found as cell type or instance name", file=sys.stderr)
            return {}

        start_cell = start_matches[0][0]
        subckt = self.parser.subckts.get(start_cell)
        if not subckt:
            print(f"ERROR: Cell '{start_cell}' has no subcircuit definition", file=sys.stderr)
            return {}

        # Determine pins to trace
        if pins is None:
            # Omit-mode: trace all bit-level pins in pin_to_pos
            pins_to_trace = list(subckt.pin_to_pos.keys())
        else:
            # Expand any bare bus base names to their indexed members.
            # Unknown names are passed through; trace() will report an error.
            pins_to_trace = []
            for p in pins:
                expanded = self.expand_pin(subckt, p)
                pins_to_trace.extend(expanded if expanded else [p])

        # Trace each pin and collect results
        results: dict[str, list[list[TraceStep]]] = {}
        for pin in pins_to_trace:
            paths = self.trace(start_name, pin, target_name, max_depth)
            results[pin] = paths

        return results

    def trace_net(
        self,
        net_name: str,
        cell_filter: Optional[str] = None,
        target_name: Optional[str] = None,
        max_depth: Optional[int] = None,
    ) -> dict[str, list[list[TraceStep]]]:
        """
        Trace paths starting from a NET (not a pin).

        Finds all (subckt, net) pairs where net_name appears on any instance
        terminal in that subckt; seeds the existing BFS with each match;
        returns a dict keyed by `<subckt_name>:<net_name>` to disambiguate when
        the same net name exists in multiple subckts.

        Inputs:
            net_name: The net to start from
            cell_filter: Optional subckt name; restricts search to that one subckt
            target_name: Optional target cell/instance
            max_depth: Optional path depth cap

        Outputs:
            dict mapping `<subckt>:<net>` -> list of TraceStep paths; empty dict
            if net not found anywhere
        """
        results: dict[str, list[list[TraceStep]]] = {}

        # Enumerate subckts to search
        subckts_to_search = (
            [cell_filter] if cell_filter else list(self.parser.subckts.keys())
        )

        for sbckt_nm in subckts_to_search:
            if sbckt_nm not in self.parser.subckts:
                continue

            # Look for net_name in instances of this subckt
            insts = self.parser.instances_by_parent.get(sbckt_nm, [])
            found = False
            for inst in insts:
                if net_name not in inst.nets:
                    continue

                # Found the net; seed the trace from this (subckt, net) pair
                found = True
                break

            if not found:
                continue

            # Use the shared BFS helper seeded from the net (not a pin)
            # initial_stack is empty; initial_inst_name is None (net-mode doesn't start from a specific instance)
            all_paths = self._bfs_from_seed(
                sbckt_nm,
                net_name,
                target_name=target_name,
                max_depth=max_depth,
                initial_stack=(),
                initial_inst_name=None,
            )

            # Store results keyed by <subckt>:<net>
            key = f"{sbckt_nm}:{net_name}"
            results[key] = all_paths

        return results


def format_path(path: list[TraceStep]) -> str:
    """Format a trace path as a single line.

    Args:
        path: List of TraceStep objects representing a path.

    Returns:
        Formatted path string.
    """
    if not path:
        return ""

    global_min = min(len(step.inst_stack) for step in path)

    is_local_peak = [False] * len(path)
    depths = [len(s.inst_stack) for s in path]
    n = len(path)
    i = 0
    while i < n:
        d = depths[i]
        j = i
        while j + 1 < n and depths[j + 1] == d:
            j += 1
        left_higher = (i == 0) or (depths[i - 1] > d)
        right_higher = (j == n - 1) or (depths[j + 1] > d)
        if left_higher and right_higher:
            for k in range(i, j + 1):
                is_local_peak[k] = True
        i = j + 1

    parts = []
    for i, step in enumerate(path):
        # Lateral-walk steps (thru, endpoint) share inst_stack with the parent,
        # so the stack-derived label would render as <internal> and lose the
        # actual leaf-instance identity. Prefer step.instance_name for these.
        if step.direction in ("thru", "endpoint") and step.instance_name:
            inst = step.instance_name
        else:
            rel = step.inst_stack[global_min:]
            if is_local_peak[i] or not rel:
                inst = "<internal>"
            else:
                inst = "/".join(s[0] for s in rel)

        step_str = f"{step.cell}|{inst}|{step.pin_or_net}"

        parts.append(step_str)
    return " -- ".join(parts)
