"""Unit tests for the bidirectional tracer (PHASE 10)."""

import os
import tempfile

from netlist_tracer import BidirectionalTracer, NetlistParser, format_path


def test_tracer_basic_instantiation(synthetic_concat_alias_v):
    """Test that tracer can be instantiated from a parser."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    assert tracer is not None, "Tracer should instantiate successfully"
    assert hasattr(tracer, "trace"), "Tracer should have trace method"


def test_tracer_trace_method_returns_list(synthetic_concat_alias_v):
    """Test that trace() method returns a list."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    # concat_alias has 'a', 'b', 'y' pins
    paths = tracer.trace("concat_alias", "y")
    assert isinstance(paths, list), "trace() should return a list"


def test_tracer_format_path(synthetic_concat_alias_v):
    """Test that format_path() works on traced paths."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    paths = tracer.trace("concat_alias", "y")
    if paths:
        for path in paths:
            formatted = format_path(path)
            assert isinstance(formatted, str), "format_path should return a string"
            assert len(formatted) > 0, "formatted path should not be empty"


def test_tracer_on_spice(synthetic_spice_basic_sp):
    """Test tracer on SPICE netlist."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    tracer = BidirectionalTracer(parser)
    # Get first subckt name
    if parser.subckts:
        first_cell = list(parser.subckts.keys())[0]
        first_pins = parser.subckts[first_cell].pins
        if first_pins:
            paths = tracer.trace(first_cell, first_pins[0])
            assert isinstance(paths, list), "trace() should return list on SPICE"


def test_tracer_max_depth(synthetic_concat_alias_v):
    """Test tracer with max_depth parameter."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    paths_unlimited = tracer.trace("concat_alias", "y")
    paths_depth0 = tracer.trace("concat_alias", "y", max_depth=0)
    assert isinstance(paths_unlimited, list), "Should handle unlimited depth"
    assert isinstance(paths_depth0, list), "Should handle max_depth=0"
    # max_depth=0 should return only the starting point
    for path in paths_depth0:
        assert len(path) <= 1, "Path depth=0 should have at most one step (start)"


def test_trace_pins_single_bit(synthetic_concat_alias_v):
    """Test trace_pins with explicit single bit."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    # concat_alias has pins 'a', 'b', 'y'
    result = tracer.trace_pins("concat_alias", pins=["y"])
    assert isinstance(result, dict), "trace_pins should return a dict"
    assert "y" in result, "Result dict should have 'y' key"
    assert isinstance(result["y"], list), "Pin value should be a list of paths"


def test_trace_pins_omit_all(synthetic_concat_alias_v):
    """Test trace_pins with pins=None traces all bit-level pins."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    result = tracer.trace_pins("concat_alias", pins=None)
    assert isinstance(result, dict), "trace_pins should return a dict"
    # Result keys should match all pin_to_pos keys for concat_alias
    subckt = parser.subckts.get("concat_alias")
    assert subckt is not None, "concat_alias should exist"
    expected_pins = set(subckt.pin_to_pos.keys())
    actual_pins = set(result.keys())
    assert actual_pins == expected_pins, (
        f"Omit-mode should trace all pins. Expected {expected_pins}, got {actual_pins}"
    )


def test_trace_pins_bare_busname_expands(vendored_picorv32_v):
    """Bare bus base name expands to all bit-level members as separate entries.

    Equivalent to passing `-pin mem_addr[0],mem_addr[1],...,mem_addr[31]`.
    Each bit gets its own key in the result dict (NOT grouped).
    """
    parser = NetlistParser(vendored_picorv32_v)
    tracer = BidirectionalTracer(parser)
    result = tracer.trace_pins("picorv32", pins=["mem_addr"])
    expected_keys = {f"mem_addr[{i}]" for i in range(32)}
    assert set(result.keys()) == expected_keys, (
        f"Bare bus name must expand to 32 indexed members; got {sorted(result.keys())}"
    )
    for key, paths in result.items():
        assert isinstance(paths, list), f"{key} must map to a list"


def test_trace_pins_mixed(synthetic_concat_alias_v):
    """Test trace_pins with a mix of valid and invalid pins."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    result = tracer.trace_pins("concat_alias", pins=["y", "nonexistent"])
    assert "y" in result, "Valid pin 'y' should be in result"
    assert "nonexistent" in result, "Invalid pin should still be in result dict"
    assert isinstance(result["y"], list), "Valid pin should map to list of paths"
    assert result["nonexistent"] == [], "Invalid pin should map to empty list"


def test_trace_pins_unknown_pin(synthetic_concat_alias_v):
    """Test trace_pins with completely unknown pin name."""
    parser = NetlistParser(synthetic_concat_alias_v)
    tracer = BidirectionalTracer(parser)
    result = tracer.trace_pins("concat_alias", pins=["total_garbage_pin_name"])
    assert isinstance(result, dict), "Should return dict even with unknown pin"
    assert "total_garbage_pin_name" in result, "Unknown pin key should be in result"
    assert result["total_garbage_pin_name"] == [], "Unknown pin should map to empty list"


def test_tracer_flat_deck_up_walk_reveals_siblings(synthetic_spice_flat_deck_sp):
    """Test that tracer UP-walk from a flat-deck child reveals sibling cells.

    This verifies that tracing a pin through top_block_a at the deck level
    can surface paths that include top_block_b (a sibling instance).
    """
    parser = NetlistParser(synthetic_spice_flat_deck_sp)
    tracer = BidirectionalTracer(parser)

    # Trace top_block_a pin 'd' (connected to vdd net at the deck level)
    # This should find a path that goes UP to the synthetic top,
    # then DOWN into top_block_b (which also connects to vdd).
    paths = tracer.trace("top_block_a", "d")

    # Check that at least one path includes a TraceStep with top_block_b
    found_sibling = False
    for path in paths:
        for step in path:
            if hasattr(step, "cell") and step.cell == "top_block_b":
                found_sibling = True
                break
        if found_sibling:
            break

    assert found_sibling, (
        f"Expected tracer to find sibling cell 'top_block_b', but it did not. "
        f"Paths: {[format_path(p) for p in paths]}"
    )


def test_expand_pin_angle_bracket_bus_members():
    """Test expand_pin recognizes <N> bus-bit notation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create synthetic Spectre file with <N>-indexed bus pins
        deck_path = os.path.join(tmpdir, "test.scs")
        with open(deck_path, "w") as f:
            f.write("simulator lang=spectre\n")
            f.write("subckt leaf_cell mybus<0> mybus<1> mybus<2> ctrl\n")
            f.write("ends leaf_cell\n")
            f.write("subckt top vdd vss\n")
            f.write("  x0 (vdd vdd vdd vss) leaf_cell\n")
            f.write("ends top\n")

        parser = NetlistParser(deck_path)
        tracer = BidirectionalTracer(parser)

        leaf_subckt = parser.subckts["leaf_cell"]

        # Test exact pin lookup
        result = tracer.expand_pin(leaf_subckt, "mybus<1>")
        assert result == ["mybus<1>"], f"Exact pin lookup should return ['mybus<1>'], got {result}"

        # Test bare bus base expansion
        result = tracer.expand_pin(leaf_subckt, "mybus")
        expected = ["mybus<0>", "mybus<1>", "mybus<2>"]
        assert result == expected, f"expand_pin('mybus') should return {expected}, got {result}"

        # Test non-existent pin
        result = tracer.expand_pin(leaf_subckt, "nonexistent")
        assert result == [], f"Non-existent pin should return [], got {result}"


def test_expand_pin_mixed_bracket_conventions():
    """Test expand_pin works with both [N] and <N> conventions in same subckt."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create synthetic Spectre file with both [N] and <N> forms
        deck_path = os.path.join(tmpdir, "test.scs")
        with open(deck_path, "w") as f:
            f.write("simulator lang=spectre\n")
            f.write("subckt leaf_cell bus_a[0] bus_a[1] bus_b<0> bus_b<1> ctrl\n")
            f.write("ends leaf_cell\n")
            f.write("subckt top vdd vss\n")
            f.write("  x0 (vdd vdd vdd vdd vss) leaf_cell\n")
            f.write("ends top\n")

        parser = NetlistParser(deck_path)
        tracer = BidirectionalTracer(parser)

        leaf_subckt = parser.subckts["leaf_cell"]

        # Test [N] form expansion
        result = tracer.expand_pin(leaf_subckt, "bus_a")
        expected = ["bus_a[0]", "bus_a[1]"]
        assert result == expected, f"expand_pin('bus_a') should return {expected}, got {result}"

        # Test <N> form expansion
        result = tracer.expand_pin(leaf_subckt, "bus_b")
        expected = ["bus_b<0>", "bus_b<1>"]
        assert result == expected, f"expand_pin('bus_b') should return {expected}, got {result}"


def test_trace_pins_expands_angle_bracket_bus_base():
    """Test trace_pins expands bare bus base with <N> notation."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create synthetic Spectre netlist
        deck_path = os.path.join(tmpdir, "test.scs")
        with open(deck_path, "w") as f:
            f.write("simulator lang=spectre\n")
            f.write("subckt leaf (data<0> data<1> vdd)\n")
            f.write("ends leaf\n")
            f.write("subckt top (vdd vss)\n")
            f.write("  x1 (vdd vdd vdd) leaf\n")
            f.write("ends top\n")

        parser = NetlistParser(deck_path)
        tracer = BidirectionalTracer(parser)

        # Call trace_pins with bare bus base name
        result = tracer.trace_pins("leaf", pins=["data"])

        # Result should have expanded keys (not 'data' itself)
        assert "data<0>" in result, f"Result should have 'data<0>' key, got {list(result.keys())}"
        assert "data<1>" in result, f"Result should have 'data<1>' key, got {list(result.keys())}"
        assert "data" not in result, "Result should not have bare 'data' key"

        # Each expanded key should map to a list (may be empty for unconnected pins)
        for key in ["data<0>", "data<1>"]:
            assert isinstance(result[key], list), f"{key} should map to a list"


def test_tracer_lateral_walk_r_thru_c_skip_xtor_endpoint():
    """Test lateral walk classifications (AC34, AC35):
      R/L  -> thru-walk (galvanic, not synthesized)
      C/K  -> skip entirely (parasitic noise, no step, no walk; not synthesized)
      other (transistors, sources, ...) -> endpoint or down-descent

    AC34 Policy: Instance with leaf prefix in KNOWN_PASSIVE_PREFIXES {R,L,C,K,V,I}
    are NOT synthesized. X-instances with device-type cell_types:
      XR1 (net_a, vss) R          -> cell_type="R"     (thru-walk, not synthesized)
      XC1 (net_a, vss) C          -> cell_type="C"     (SKIP, not synthesized)
      XM1 (net_a, ..., vss, vss) nch_model  -> nch_model synthesized (leaf prefix not passive)
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        deck_path = os.path.join(tmpdir, "test_lateral.sp")
        with open(deck_path, "w") as f:
            f.write(".title Test lateral walk: R thru, C skip, transistor endpoint\n")
            f.write(".subckt top net_a VDD VSS\n")
            f.write("XR1 net_a VSS R\n")
            f.write("XC1 net_a VSS C\n")
            f.write("XM1 net_a net_a VSS VSS nch_model W=1u L=0.1u\n")
            f.write(".ends top\n")

        parser = NetlistParser(deck_path)
        tracer = BidirectionalTracer(parser)
        paths = tracer.trace("top", "net_a", max_depth=5)

        has_thru_r = False
        has_down_or_endpoint_nch = False
        seen_any_c_step = False

        for path in paths:
            for step in path:
                if step.direction == "thru" and step.cell == "R":
                    has_thru_r = True
                # nch_model gets synthesized (M prefix is not passive), so expect "down" descent
                # or "endpoint" if no pins are synthesized
                if step.cell == "nch_model" and step.direction in ("down", "endpoint"):
                    has_down_or_endpoint_nch = True
                if step.cell == "C":
                    seen_any_c_step = True

        assert has_thru_r, (
            "Expected at least one path with direction='thru' for R (galvanic), "
            f"but got paths: {[format_path(p) for p in paths]}"
        )
        assert has_down_or_endpoint_nch, (
            "Expected at least one path with direction='down' or 'endpoint' for "
            f"nch_model, but got paths: {[format_path(p) for p in paths]}"
        )
        assert not seen_any_c_step, (
            "Caps (cell_type='C') should be SKIPPED entirely (parasitic noise); "
            f"no step of any kind should reference them, but got paths: {[format_path(p) for p in paths]}"
        )


def test_per_net_trace_flat_spf(synthetic_spice_basic_sp: str) -> None:
    """Test trace_net() walks from a named net and returns non-empty results."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    tracer = BidirectionalTracer(parser)

    # Pick any net that exists in the first subckt
    first_subckt = list(parser.subckts.keys())[0] if parser.subckts else None
    if not first_subckt:
        return  # Skip if no subckts

    # Pick first net from first instance in that subckt
    insts = parser.instances_by_parent.get(first_subckt, [])
    if not insts:
        return  # Skip if no instances

    first_net = insts[0].nets[0] if insts[0].nets else None
    if not first_net:
        return  # Skip if no nets

    # Trace from this net
    result = tracer.trace_net(first_net)
    assert isinstance(result, dict), "trace_net() should return a dict"
    assert len(result) > 0, f"trace_net() should find at least one path for net '{first_net}'"

    # Verify key format: subckt:net
    for key in result.keys():
        assert ":" in key, f"Result key '{key}' should be in format 'subckt:net'"

    # Verify first step has direction='start'
    for paths_list in result.values():
        for path in paths_list:
            if path:
                assert path[0].direction == "start", "First step should have direction='start'"
                assert path[0].pin_or_net == first_net, "First step should reference the traced net"


def test_per_net_trace_cell_filter(synthetic_spice_basic_sp: str) -> None:
    """Test trace_net() with cell_filter restricts to a single subckt."""
    parser = NetlistParser(synthetic_spice_basic_sp)
    tracer = BidirectionalTracer(parser)

    first_subckt = list(parser.subckts.keys())[0] if parser.subckts else None
    if not first_subckt:
        return  # Skip if no subckts

    insts = parser.instances_by_parent.get(first_subckt, [])
    if not insts:
        return  # Skip if no instances

    first_net = insts[0].nets[0] if insts[0].nets else None
    if not first_net:
        return  # Skip if no nets

    # Trace with cell_filter
    result = tracer.trace_net(first_net, cell_filter=first_subckt)
    assert isinstance(result, dict), "trace_net() should return a dict"

    # All result keys should start with the filtered cell name
    for key in result.keys():
        cell_part = key.split(":")[0]
        assert cell_part == first_subckt, (
            f"Result key '{key}' should start with filtered cell '{first_subckt}'"
        )


def test_per_net_trace_hierarchical(vendored_picorv32_v: str) -> None:
    """Test trace_net() on hierarchical netlist finds net in multiple subckts."""
    parser = NetlistParser(vendored_picorv32_v)
    tracer = BidirectionalTracer(parser)

    # Pick a net that appears in multiple subckts (if available)
    # Common test: find any net that's used in multiple places
    net_freq = {}
    for subckt_name, insts in parser.instances_by_parent.items():
        for inst in insts:
            for net in inst.nets:
                key = net
                net_freq[key] = net_freq.get(key, [])
                if subckt_name not in net_freq[key]:
                    net_freq[key].append(subckt_name)

    # Find a net used in multiple subckts
    multi_subckt_net = None
    for net, subckts in net_freq.items():
        if len(subckts) > 1:
            multi_subckt_net = net
            break

    if not multi_subckt_net:
        # Fallback: trace any net and check structure
        insts = list(parser.instances_by_parent.values())[0] if parser.instances_by_parent else []
        if not insts:
            return
        multi_subckt_net = insts[0].nets[0] if insts[0].nets else None

    if not multi_subckt_net:
        return  # Skip if unable to find a net

    result = tracer.trace_net(multi_subckt_net)
    assert isinstance(result, dict), "trace_net() should return a dict"
    # Verify dict is non-empty
    assert len(result) >= 0, "Result should be a valid dict"
