"""Tests for JSON cache schema versioning (backward compatibility with v0 caches)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from netlist_tracer import BidirectionalTracer, NetlistParser, format_path
from netlist_tracer.exceptions import NetlistParseError

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures", "vendored")


def test_dump_includes_schema_version(synthetic_spice_features_sp: str) -> None:
    """Verify that dump_json() includes a current schema_version field."""
    from netlist_tracer.parser import _CACHE_SCHEMA_VERSION

    with tempfile.TemporaryDirectory() as tmpdir:
        # Parse the consolidated spice_features.sp fixture
        parser = NetlistParser(synthetic_spice_features_sp)
        assert "nand2" in parser.subckts, "nand2 subckt should be in spice_features.sp"

        # Dump to JSON
        out_path = Path(tmpdir) / "cache.json"
        parser.dump_json(str(out_path))

        # Load JSON and verify schema_version field matches current version
        with open(out_path) as f:
            data = json.load(f)
        assert "schema_version" in data, "schema_version field missing from dumped JSON"
        assert data["schema_version"] == _CACHE_SCHEMA_VERSION, (
            f"Expected schema_version={_CACHE_SCHEMA_VERSION}, got {data['schema_version']}"
        )


def test_load_v0_cache() -> None:
    """Verify backward compatibility: v0 cache (without schema_version field) loads cleanly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a synthetic v0 JSON cache (no schema_version field)
        v0_cache = {
            "format": "spice",
            "source": "/tmp/test.sp",
            "subckts": {
                "nand2": ["A", "B", "Y", "VDD", "GND"],
                "inv": ["IN", "OUT", "VDD", "GND"],
            },
            "instances": [
                {
                    "name": "x_nand",
                    "cell_type": "nand2",
                    "nets": ["n1", "n2", "n3", "VDD", "GND"],
                    "parent_cell": "top",
                },
                {
                    "name": "x_inv",
                    "cell_type": "inv",
                    "nets": ["n3", "n4", "VDD", "GND"],
                    "parent_cell": "top",
                },
            ],
            "aliases": {},
        }
        cache_path = Path(tmpdir) / "v0_cache.json"
        with open(cache_path, "w") as f:
            json.dump(v0_cache, f)

        # Load the v0 cache — should succeed
        parser = NetlistParser(str(cache_path))

        # Verify the data was loaded correctly
        assert "nand2" in parser.subckts, "nand2 subckt not loaded"
        assert "inv" in parser.subckts, "inv subckt not loaded"
        assert parser.subckts["nand2"].pins == ["A", "B", "Y", "VDD", "GND"]
        assert parser.subckts["inv"].pins == ["IN", "OUT", "VDD", "GND"]
        assert len(parser.instances_by_parent["top"]) == 2, "Expected 2 instances under top"


def test_unsupported_schema_version_raises() -> None:
    """Verify that future schema versions raise NetlistParseError."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a cache with a future unsupported schema version
        future_cache = {
            "schema_version": 99,  # Much newer than supported v1
            "format": "spice",
            "source": "/tmp/test.sp",
            "subckts": {"dummy": ["a", "b"]},
            "instances": [],
            "aliases": {},
        }
        cache_path = Path(tmpdir) / "future_cache.json"
        with open(cache_path, "w") as f:
            json.dump(future_cache, f)

        # Attempt to load the future cache — should raise NetlistParseError
        with pytest.raises(NetlistParseError) as exc_info:
            NetlistParser(str(cache_path))
        assert "newer than supported" in str(exc_info.value).lower()
        assert "99" in str(exc_info.value), "Error message should mention version 99"
        assert "update netlist-tracer" in str(exc_info.value).lower()


def test_v3_instance_params_roundtrip() -> None:
    """Verify v3 serialization preserves instance.params through dump/load cycle."""
    from netlist_tracer.model import Instance, SubcktDef

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a parser with a synthetic Instance that has params
        parser = NetlistParser.__new__(NetlistParser)
        parser.filename = "synthetic"
        parser.source_path = "synthetic"
        parser.format = "spice"
        parser.subckts = {"cell_with_params": SubcktDef(name="cell_with_params", pins=["a", "b"])}
        parser.instances_by_parent = {
            "top": [
                Instance(
                    name="x_test",
                    cell_type="cell_with_params",
                    nets=["net1", "net2"],
                    parent_cell="top",
                    params={"_value": "1830", "_merged_from": ["R1", "R2", "R3"]},
                )
            ]
        }
        parser.instances_by_celltype = {"cell_with_params": parser.instances_by_parent["top"]}
        parser.instances_by_name = {"x_test": parser.instances_by_parent["top"]}
        parser.global_nets = []

        # Dump to JSON
        out_path = Path(tmpdir) / "cache_with_params.json"
        parser.dump_json(str(out_path))

        # Verify 'params' field is in the instance entry
        with open(out_path) as f:
            data = json.load(f)
        assert len(data["instances"]) == 1
        inst_entry = data["instances"][0]
        assert "params" in inst_entry, "Instance entry should have 'params' field"
        assert inst_entry["params"]["_value"] == "1830"
        assert inst_entry["params"]["_merged_from"] == ["R1", "R2", "R3"]

        # Load back and verify params survived
        reloaded = NetlistParser(str(out_path))
        reloaded_inst = reloaded.instances_by_parent["top"][0]
        assert reloaded_inst.params == {"_value": "1830", "_merged_from": ["R1", "R2", "R3"]}


def test_v3_subckt_params_roundtrip() -> None:
    """Verify v3 serialization preserves subckt.params through dump/load cycle."""
    from netlist_tracer.model import SubcktDef

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a parser with a SubcktDef that has params
        parser = NetlistParser.__new__(NetlistParser)
        parser.filename = "synthetic"
        parser.source_path = "synthetic"
        parser.format = "verilog"
        sub_with_params = SubcktDef(name="interface_mod", pins=["data", "clk", "rst"])
        sub_with_params.params = {
            "_kind": "interface",
            "_modports": ["m"],
            "_pin_directions": {"data": "output", "clk": "input"},
        }
        parser.subckts = {"interface_mod": sub_with_params}
        parser.instances_by_parent = {}
        parser.instances_by_celltype = {}
        parser.instances_by_name = {}
        parser.global_nets = []

        # Dump to JSON
        out_path = Path(tmpdir) / "cache_with_subckt_params.json"
        parser.dump_json(str(out_path))

        # Verify 'subckt_params' field is in top-level output
        with open(out_path) as f:
            data = json.load(f)
        assert "subckt_params" in data, "Top-level should have 'subckt_params' field"
        assert "interface_mod" in data["subckt_params"]
        assert data["subckt_params"]["interface_mod"]["_kind"] == "interface"
        assert data["subckt_params"]["interface_mod"]["_modports"] == ["m"]
        assert data["subckt_params"]["interface_mod"]["_pin_directions"] == {
            "data": "output",
            "clk": "input",
        }

        # Load back and verify params survived
        reloaded = NetlistParser(str(out_path))
        reloaded_sub = reloaded.subckts["interface_mod"]
        assert reloaded_sub.params["_kind"] == "interface"
        assert reloaded_sub.params["_modports"] == ["m"]
        assert reloaded_sub.params["_pin_directions"] == {"data": "output", "clk": "input"}


def test_v3_empty_params_omitted() -> None:
    """Verify that empty params are omitted from JSON to keep cache compact."""
    from netlist_tracer.model import Instance, SubcktDef

    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a parser with instances and subckts that have empty params
        parser = NetlistParser.__new__(NetlistParser)
        parser.filename = "synthetic"
        parser.source_path = "synthetic"
        parser.format = "verilog"
        parser.subckts = {
            "simple_cell": SubcktDef(name="simple_cell", pins=["a", "b"]),
            "another_cell": SubcktDef(name="another_cell", pins=["x", "y"]),
        }
        parser.instances_by_parent = {
            "top": [
                Instance(
                    name="x1",
                    cell_type="simple_cell",
                    nets=["n1", "n2"],
                    parent_cell="top",
                    params={},
                ),
                Instance(
                    name="x2",
                    cell_type="another_cell",
                    nets=["n3", "n4"],
                    parent_cell="top",
                    params={},
                ),
            ]
        }
        parser.instances_by_celltype = {
            "simple_cell": [parser.instances_by_parent["top"][0]],
            "another_cell": [parser.instances_by_parent["top"][1]],
        }
        parser.instances_by_name = {
            "x1": [parser.instances_by_parent["top"][0]],
            "x2": [parser.instances_by_parent["top"][1]],
        }
        parser.global_nets = []

        # Dump to JSON
        out_path = Path(tmpdir) / "cache_empty_params.json"
        parser.dump_json(str(out_path))

        # Verify 'params' field is NOT in instance entries (empty params omitted)
        with open(out_path) as f:
            data = json.load(f)
        for inst_entry in data["instances"]:
            assert "params" not in inst_entry, (
                f"Instance {inst_entry['name']} should not have 'params' field when empty"
            )

        # Verify 'subckt_params' is NOT in top-level when all subckts have empty params
        assert "subckt_params" not in data, (
            "Top-level should not have 'subckt_params' field when all subckts have empty params"
        )


def test_dump_byte_deterministic() -> None:
    """Verify that shuffled in-memory insertion order yields byte-identical JSON dumps.

    Constructs two parsers with identical logical models but different insertion
    orders (one ascending, one descending) for subckts and instances. Both should
    produce byte-identical dumps despite the in-memory order difference.
    """
    from netlist_tracer.model import Instance, SubcktDef

    with tempfile.TemporaryDirectory() as tmpdir:
        # Parser A: insert subckts and instances in ascending order
        parser_a = NetlistParser.__new__(NetlistParser)
        parser_a.filename = "synthetic"
        parser_a.source_path = "synthetic"
        parser_a.format = "spice"

        # Build the same logical model in ascending order
        parser_a.subckts = {
            "cell_a": SubcktDef(name="cell_a", pins=["p1", "p2"]),
            "cell_b": SubcktDef(name="cell_b", pins=["x", "y", "z"]),
            "cell_c": SubcktDef(name="cell_c", pins=["in", "out"]),
        }
        # Add params and aliases to some subckts
        parser_a.subckts["cell_b"].params = {"_kind": "special", "_version": "1"}
        parser_a.subckts["cell_c"].aliases = {"out_alias": "out", "in_alias": "in"}

        parser_a.instances_by_parent = {
            "top": [
                Instance(
                    name="inst_1",
                    cell_type="cell_a",
                    nets=["n1", "n2"],
                    parent_cell="top",
                    params={},
                ),
                Instance(
                    name="inst_2",
                    cell_type="cell_b",
                    nets=["n3", "n4", "n5"],
                    parent_cell="top",
                    params={"_value": "100", "_merged_from": ["R1", "R2"]},
                ),
                Instance(
                    name="inst_3",
                    cell_type="cell_c",
                    nets=["n6", "n7"],
                    parent_cell="top",
                    params={},
                ),
            ],
            "cell_b": [
                Instance(
                    name="sub_inst_1",
                    cell_type="cell_a",
                    nets=["na1", "na2"],
                    parent_cell="cell_b",
                    params={},
                ),
            ],
        }
        parser_a.instances_by_celltype = {
            "cell_a": [
                parser_a.instances_by_parent["top"][0],
                parser_a.instances_by_parent["cell_b"][0],
            ],
            "cell_b": [parser_a.instances_by_parent["top"][1]],
            "cell_c": [parser_a.instances_by_parent["top"][2]],
        }
        parser_a.instances_by_name = {
            "inst_1": parser_a.instances_by_parent["top"][0],
            "inst_2": parser_a.instances_by_parent["top"][1],
            "inst_3": parser_a.instances_by_parent["top"][2],
            "sub_inst_1": parser_a.instances_by_parent["cell_b"][0],
        }
        parser_a.global_nets = []

        # Parser B: insert subckts and instances in DESCENDING/different order
        parser_b = NetlistParser.__new__(NetlistParser)
        parser_b.filename = "synthetic"
        parser_b.source_path = "synthetic"
        parser_b.format = "spice"

        # Build the SAME logical model but in different insertion order (reverse dict order)
        parser_b.subckts = {
            "cell_c": SubcktDef(name="cell_c", pins=["in", "out"]),
            "cell_b": SubcktDef(name="cell_b", pins=["x", "y", "z"]),
            "cell_a": SubcktDef(name="cell_a", pins=["p1", "p2"]),
        }
        # Add same params and aliases
        parser_b.subckts["cell_b"].params = {"_kind": "special", "_version": "1"}
        parser_b.subckts["cell_c"].aliases = {"out_alias": "out", "in_alias": "in"}

        # Insert instances in different nested order (cell_b parent first, then top)
        parser_b.instances_by_parent = {
            "cell_b": [
                Instance(
                    name="sub_inst_1",
                    cell_type="cell_a",
                    nets=["na1", "na2"],
                    parent_cell="cell_b",
                    params={},
                ),
            ],
            "top": [
                Instance(
                    name="inst_3",
                    cell_type="cell_c",
                    nets=["n6", "n7"],
                    parent_cell="top",
                    params={},
                ),
                Instance(
                    name="inst_2",
                    cell_type="cell_b",
                    nets=["n3", "n4", "n5"],
                    parent_cell="top",
                    params={"_value": "100", "_merged_from": ["R1", "R2"]},
                ),
                Instance(
                    name="inst_1",
                    cell_type="cell_a",
                    nets=["n1", "n2"],
                    parent_cell="top",
                    params={},
                ),
            ],
        }
        parser_b.instances_by_celltype = {
            "cell_c": [parser_b.instances_by_parent["top"][0]],
            "cell_b": [parser_b.instances_by_parent["top"][1]],
            "cell_a": [
                parser_b.instances_by_parent["top"][2],
                parser_b.instances_by_parent["cell_b"][0],
            ],
        }
        parser_b.instances_by_name = {
            "inst_3": parser_b.instances_by_parent["top"][0],
            "inst_2": parser_b.instances_by_parent["top"][1],
            "inst_1": parser_b.instances_by_parent["top"][2],
            "sub_inst_1": parser_b.instances_by_parent["cell_b"][0],
        }
        parser_b.global_nets = []

        # Dump both to temp files
        dump_a = Path(tmpdir) / "dump_a.json"
        dump_b = Path(tmpdir) / "dump_b.json"
        parser_a.dump_json(str(dump_a))
        parser_b.dump_json(str(dump_b))

        # Read both as bytes and compare
        with open(dump_a, "rb") as f:
            bytes_a = f.read()
        with open(dump_b, "rb") as f:
            bytes_b = f.read()

        assert bytes_a == bytes_b, (
            "Dumps of identical logical models with different insertion orders "
            "should be byte-identical after sort canonicalization"
        )

        # Also verify that the JSON is still valid and contains expected structure
        with open(dump_a) as f:
            data_a = json.load(f)
        with open(dump_b) as f:
            data_b = json.load(f)

        # Check subckts
        assert set(data_a["subckts"].keys()) == {"cell_a", "cell_b", "cell_c"}
        assert set(data_b["subckts"].keys()) == {"cell_a", "cell_b", "cell_c"}

        # Check instances
        inst_names_a = {i["name"] for i in data_a["instances"]}
        inst_names_b = {i["name"] for i in data_b["instances"]}
        assert inst_names_a == {"inst_1", "inst_2", "inst_3", "sub_inst_1"}
        assert inst_names_b == {"inst_1", "inst_2", "inst_3", "sub_inst_1"}

        # Check that params and aliases are present
        assert "subckt_params" in data_a, "subckt_params should be present"
        assert data_a["subckt_params"]["cell_b"] == {"_kind": "special", "_version": "1"}
        assert "aliases" in data_a, "aliases should be present"
        assert data_a["aliases"]["cell_c"] == {"out_alias": "out", "in_alias": "in"}

        # Check that instances with params have params field
        inst_2_a = next(i for i in data_a["instances"] if i["name"] == "inst_2")
        assert "params" in inst_2_a, "inst_2 should have params"
        assert inst_2_a["params"]["_value"] == "100"
        assert inst_2_a["params"]["_merged_from"] == ["R1", "R2"]


def test_v2_cache_still_loads() -> None:
    """Verify backward compatibility: v2 cache (with schema_version=2) loads correctly."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create a v2 JSON cache (no instance params, no subckt_params)
        v2_cache = {
            "schema_version": 2,
            "format": "spice",
            "source": "/tmp/test.sp",
            "subckts": {
                "nand2": ["A", "B", "Y", "VDD", "GND"],
                "inv": ["IN", "OUT", "VDD", "GND"],
            },
            "instances": [
                {
                    "name": "x_nand",
                    "cell_type": "nand2",
                    "nets": ["n1", "n2", "n3", "VDD", "GND"],
                    "parent_cell": "top",
                },
                {
                    "name": "x_inv",
                    "cell_type": "inv",
                    "nets": ["n3", "n4", "VDD", "GND"],
                    "parent_cell": "top",
                },
            ],
            "aliases": {},
        }
        cache_path = Path(tmpdir) / "v2_cache.json"
        with open(cache_path, "w") as f:
            json.dump(v2_cache, f)

        # Load the v2 cache — should succeed
        parser = NetlistParser(str(cache_path))

        # Verify the data was loaded correctly and instances have empty params (default)
        assert "nand2" in parser.subckts
        assert "inv" in parser.subckts
        assert len(parser.instances_by_parent["top"]) == 2
        for inst in parser.instances_by_parent["top"]:
            assert inst.params == {}, f"Instance {inst.name} should have empty params as default"


def test_v3_spf_merged_R_roundtrip() -> None:
    """Integration test: parse a small DSPF, serialize v3, reload, verify params survive.

    This test is marked as local (environment-gated) to run only when
    NETLIST_TRACER_SPF_SAMPLE is set.
    """
    import os

    spf_sample = os.environ.get("NETLIST_TRACER_SPF_SAMPLE")
    if not spf_sample or not os.path.exists(spf_sample):
        pytest.skip("NETLIST_TRACER_SPF_SAMPLE not set or file does not exist")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Parse the SPF file (should populate instance.params with _value, _merged_from, etc.)
        parser = NetlistParser(spf_sample)

        # Find an instance with params before dump (snapshot original state)
        # Find the top-level cell (parent with most instances)
        top = max(
            parser.instances_by_parent.keys(), key=lambda k: len(parser.instances_by_parent[k])
        )
        # Prefer instances with both _value and _merged_from
        orig_merged = next(
            (
                i
                for i in parser.instances_by_parent.get(top, [])
                if i.params.get("_value") and i.params.get("_merged_from")
            ),
            None,
        )
        if orig_merged is None:
            # Fallback to any instance with _value
            orig_merged = next(
                (i for i in parser.instances_by_parent.get(top, []) if i.params.get("_value")),
                None,
            )
        if orig_merged is None:
            pytest.skip("No instances with _value found in SPF")

        orig_value = orig_merged.params.get("_value")
        orig_merged_from = orig_merged.params.get("_merged_from")
        orig_name = orig_merged.name

        # Dump to JSON (v3)
        cache_path = Path(tmpdir) / "spf_cache_v3.json"
        parser.dump_json(str(cache_path))

        # Verify v3 structure: check for instances with params
        with open(cache_path) as f:
            data = json.load(f)
        assert data["schema_version"] == 3
        instances_with_params = [i for i in data["instances"] if "params" in i]
        assert len(instances_with_params) > 0, (
            "Expected some instances with params from SPF parsing"
        )

        # Reload and verify params are intact and match originals
        reloaded = NetlistParser(str(cache_path))
        reloaded_merged = next(
            (i for i in reloaded.instances_by_parent.get(top, []) if i.name == orig_name),
            None,
        )
        assert reloaded_merged is not None, f"Reloaded instance {orig_name} not found"
        assert reloaded_merged.params.get("_value") == orig_value, (
            f"Reloaded _value {reloaded_merged.params.get('_value')} != original {orig_value}"
        )
        # Verify _merged_from is preserved if it was present in original
        if orig_merged_from is not None:
            assert reloaded_merged.params.get("_merged_from") == orig_merged_from, (
                f"Reloaded _merged_from {reloaded_merged.params.get('_merged_from')} != original {orig_merged_from}"
            )


class TestCacheRoundtrip:
    """Parametrized cache roundtrip validation across SPICE, Verilog, EDIF."""

    @pytest.mark.parametrize(
        "fixture_path,format_name,start_cell,start_pin",
        [
            (
                os.path.join(FIXTURES_DIR, "sky130_fd_sc_hd__inv_1.spice"),
                "spice",
                "sky130_fd_sc_hd__inv_1",
                "A",
            ),
            (
                os.path.join(FIXTURES_DIR, "picorv32.v"),
                "verilog",
                "picorv32",
                "clk",
            ),
            (
                os.path.join(FIXTURES_DIR, "AND_gate.edf"),
                "edif",
                "logic_gate",
                "a",
            ),
        ],
    )
    def test_cache_roundtrip_parity(
        self,
        fixture_path: str,
        format_name: str,
        start_cell: str,
        start_pin: str,
    ) -> None:
        """Direct parse and reload-from-cache parse must produce identical traces."""
        # Direct parse
        parser1 = NetlistParser(fixture_path)
        tracer1 = BidirectionalTracer(parser1)
        paths1 = tracer1.trace(start_cell, start_pin)
        formatted1 = sorted(set(format_path(p) for p in paths1))

        # Cache roundtrip
        with tempfile.TemporaryDirectory() as tmpdir:
            cache_file = os.path.join(tmpdir, "cache.json")

            # Dump to cache
            parser1.dump_json(cache_file)

            # Reload from cache
            parser2 = NetlistParser(cache_file)
            tracer2 = BidirectionalTracer(parser2)
            paths2 = tracer2.trace(start_cell, start_pin)
            formatted2 = sorted(set(format_path(p) for p in paths2))

        # Structural assertions: detect drift that trace-only roundtrip might miss
        assert sorted(parser1.subckts.keys()) == sorted(parser2.subckts.keys()), (
            f"Subcircuit mismatch for {format_name}"
        )

        for cell in parser1.subckts.keys():
            inst1_names = sorted(inst.name for inst in parser1.instances_by_parent[cell])
            inst2_names = sorted(inst.name for inst in parser2.instances_by_parent[cell])
            assert inst1_names == inst2_names, f"Instance mismatch in {cell} for {format_name}"

        for cell in parser1.subckts.keys():
            if hasattr(parser1.subckts[cell], "aliases"):
                assert parser1.subckts[cell].aliases == parser2.subckts[cell].aliases, (
                    f"Alias map mismatch in {cell} for {format_name}"
                )

        # Trace equality must still hold
        assert formatted2 == formatted1, f"Cache roundtrip mismatch for {format_name}"


# ---------------------------------------------------------------------------
# Real-cache smoke tests (env-gated via NETLIST_TRACER_LOCAL_CACHE)
# Folded in from former tests/test_local_smoke.py to mirror the
# real-DSPF smoke folding into test_parser_spf.py.
# ---------------------------------------------------------------------------


@pytest.mark.local
class TestRealCacheSmoke:
    """Smoke tests using a real-world JSON cache if NETLIST_TRACER_LOCAL_CACHE env var is set."""

    @pytest.fixture
    def local_cache_path(self) -> str:
        """Get NETLIST_TRACER_LOCAL_CACHE env var or skip test."""
        path = os.environ.get("NETLIST_TRACER_LOCAL_CACHE")
        if not path or not Path(path).exists():
            pytest.skip("NETLIST_TRACER_LOCAL_CACHE not set or file does not exist")
        return path

    def test_local_cache_loads(self, local_cache_path: str) -> None:
        """Local JSON cache loads without error and yields non-trivial state."""
        parser = NetlistParser(local_cache_path)
        assert parser.format in {"verilog", "spice", "cdl", "spectre", "spf", "edif"}
        assert len(parser.subckts) > 0, "cache should contain at least one subckt"
        total_instances = sum(len(v) for v in parser.instances_by_parent.values())
        assert total_instances > 0, "cache should contain at least one instance"

    def test_local_cache_has_pins(self, local_cache_path: str) -> None:
        """First subckt in cache should have at least one pin."""
        parser = NetlistParser(local_cache_path)
        first_cell = next(iter(parser.subckts.keys()))
        pins = parser.subckts[first_cell].pins
        assert len(pins) > 0, f"first subckt {first_cell!r} should have at least one pin"

    def test_local_cache_deterministic_trace(self, local_cache_path: str) -> None:
        """Trace from first subckt's first pin completes without raising."""
        parser = NetlistParser(local_cache_path)
        tracer = BidirectionalTracer(parser)
        first_cell = next(iter(parser.subckts.keys()))
        pins = parser.subckts[first_cell].pins
        if not pins:
            pytest.skip(f"first subckt {first_cell!r} has no pins")
        first_pin = pins[0]
        paths = tracer.trace(first_cell, first_pin)
        assert isinstance(paths, list), "trace() must return a list"

    def test_local_cache_schema_version(self, local_cache_path: str) -> None:
        """Cache loads successfully (any supported schema version)."""
        parser = NetlistParser(local_cache_path)
        assert len(parser.subckts) > 0, "parser loaded cache successfully"
