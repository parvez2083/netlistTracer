from __future__ import annotations

import glob
import json
import os
from collections import defaultdict
from multiprocessing import cpu_count
from typing import Optional

from netlist_tracer._logging import get_logger
from netlist_tracer.exceptions import NetlistParseError
from netlist_tracer.model import Instance, SubcktDef, merge_aliases_into_subckt
from netlist_tracer.parsers.detect import detect_format, detect_format_per_file
from netlist_tracer.parsers.edif import parse_edif
from netlist_tracer.parsers.peek import peek_pins as _pk_pns
from netlist_tracer.parsers.primitives import synthesize_primitive_subckts
from netlist_tracer.parsers.spectre import parse_spectre
from netlist_tracer.parsers.spf import parse_spf
from netlist_tracer.parsers.spice import parse_spice
from netlist_tracer.parsers.verilog.instances import _sv_parse_file
from netlist_tracer.parsers.verilog.preprocess import _sv_discover_headers, _sv_parse_defines
from netlist_tracer.parsers.verilog.specialize import _sv_assemble, _sv_specialize_modules

_logger = get_logger(__name__)

_CACHE_SCHEMA_VERSION = 3

# Format priority ranking for collision resolution (higher = wins on name conflict)
_FORMAT_PRIORITY = {
    "spectre": 5,
    "cdl": 4,
    "spice": 3,
    "verilog": 2,
    "edif": 1,
    "spf": 0,
}


class NetlistParser:
    """Parses CDL, SPICE, Spectre, and Verilog/SystemVerilog netlists."""

    @classmethod
    def peek_pins(cls, flpth: str, cell: str, fmt: str | None = None) -> list[str] | None:
        """Fast pre-scan to peek at a cell's pin list WITHOUT running full parse.

        Safe for large files (e.g., 600 MB DSPF.gz): completes in < 2 seconds
        vs. full parse taking 60+ seconds. Returns None if cell not found,
        enabling safe fall-through to full parse.

        Inputs:
            flpth: Path to netlist file or directory
            cell: Cell/module name to find
            fmt: Optional explicit format hint ('spice', 'cdl', 'spectre', 'spf',
                'verilog', 'edif', or None for auto-detect)

        Outputs:
            list[str] of pin names if cell found, None otherwise (NEVER raises
            on cell-not-found; raises only on bad inputs)
        """
        return _pk_pns(flpth, cell, fmt=fmt)

    def __init__(
        self,
        filename: str,
        tvars: Optional[dict[str, str]] = None,
        defines: Optional[set[str]] = None,
        define_values: Optional[dict[str, int]] = None,
        top: Optional[str] = None,
        workers: int = 0,
        include_paths: Optional[list[str]] = None,
        format: Optional[str] = None,
        bus_order: str = "msb_first",
    ) -> None:
        """Parse a netlist source.

        Args:
            filename: Path to netlist file, directory, or .json cache.
                - .json file: load pre-parsed cache (fast path)
                - directory: multi-file Verilog/SV with full elaboration
                - single file: format auto-detected from content
            tvars: Template variable substitutions for $key$ macros.
            defines: Set of preprocessor define names.
            define_values: Dict of {name: int} for define values.
            top: Optional top-cell name to limit hierarchy.
            workers: Parallel worker count (0 = auto).
            include_paths: Optional list of additional search directories for includes.
            format: Override auto-detection with explicit format string.
                Valid values: 'spice', 'cdl', 'spectre', 'spf', 'verilog', 'edif', None (auto-detect).
            bus_order: Bus bit ordering for EDIF port arrays ('msb_first' or 'lsb_first'). Ignored for non-EDIF formats.
        """
        # Validate format parameter
        valid_formats = {"spice", "cdl", "spectre", "spf", "verilog", "edif", None}
        if format is not None and format not in valid_formats:
            raise NetlistParseError(
                f"Invalid format '{format}': must be one of "
                f"{sorted(str(f) for f in valid_formats if f is not None)}"
            )

        self.filename = filename
        self.source_path = filename
        self.tvars = dict(tvars) if tvars else {}
        self.defines = set(defines) if defines is not None else set()
        self.define_values = dict(define_values) if define_values else None
        self.top = top
        self.workers = workers
        self.include_paths = include_paths
        self.subckts: dict[str, SubcktDef] = {}
        self.instances_by_parent: dict[str, list[Instance]] = defaultdict(list)
        self.instances_by_celltype: dict[str, list[Instance]] = defaultdict(list)
        self.instances_by_name: dict[str, list[Instance]] = defaultdict(list)
        self.format = "spice"
        self.files: list[str] = []
        self.global_nets: list[str] = []
        self._user_format = format
        self._bus_order = bus_order
        # Lazy SPF parsing: map cell_name -> spf_path for placeholders; set of materialized paths
        self.pndg_spf_fls: dict[str, str] = {}
        self.mtrl_spf_fls: set[str] = set()

        # JSON cache: load pre-parsed data directly
        if os.path.isfile(filename) and filename.endswith(".json"):
            self._load_json(filename)
            return

        # Directory path: detect all netlist files
        if os.path.isdir(filename):
            self.files = []
            # Glob all netlist formats to support mixed-format directories
            for ext in (
                "psv",
                "sv",
                "v",
                "va",
                "vams",
                "vha",
                "sp",
                "spi",
                "cir",
                "ckt",
                "scs",
                "cdl",
                "spf",
                "dspf",
                "edif",
                "edn",
                "edf",
            ):
                self.files.extend(
                    glob.glob(os.path.join(filename, "**", f"*.{ext}"), recursive=True)
                )
                self.files.extend(glob.glob(os.path.join(filename, f"*.{ext}")))
            self.files = sorted(set(self.files))
            if not self.files:
                raise NetlistParseError(f"No netlist files found in directory: {filename}")
            _logger.info(f"Parsing {len(self.files)} netlist files from: {filename}")
            # Directory paths default to verilog if only Verilog files present;
            # format kwarg is ignored for directories
            # Detection happens in _parse() based on actual file contents
            if not format:
                # Auto-detect format from files
                verilog_exts = {"psv", "sv", "v", "va", "vams", "vha"}
                has_verilog = any(
                    f.endswith(tuple(f".{ext}" for ext in verilog_exts)) for f in self.files
                )
                has_other = any(
                    not f.endswith(tuple(f".{ext}" for ext in verilog_exts)) for f in self.files
                )
                if has_verilog and not has_other:
                    # Verilog-only directory: use existing behavior
                    self.format = "verilog"
                    filtered_files = [
                        f
                        for f in self.files
                        if f.endswith(tuple(f".{ext}" for ext in verilog_exts))
                    ]
                    self.files = sorted(filtered_files)
                else:
                    # Mixed or non-Verilog directory: will detect per-file in _parse()
                    self.format = "mixed"
            else:
                # User override: format is already set
                self.format = format
            self.source_path = os.path.abspath(filename)
        else:
            self.files = [filename]
            _logger.info(f"Parsing netlist: {os.path.abspath(filename)}")
            # Use explicit format if provided; otherwise auto-detect
            if self._user_format:
                self.format = self._user_format
            else:
                self.format = self._detect_format()
        self._parse()
        # Synthesize virtual SubcktDefs for SPICE primitives (post-parse pass)
        synthesize_primitive_subckts(self)

    def _detect_format(self) -> str:
        """Detect netlist format from file content."""
        return detect_format(self.files)

    def _dispatch_single_format(
        self, format: str, files: list[str]
    ) -> tuple[dict[str, SubcktDef], list[Instance], list[str]]:
        """Dispatch to format-specific parser and return results without mutation.

        Routes to the appropriate parser for one format and returns
        (subckts, instances, global_nets) tuple. Verilog handles multiple
        files; SPICE/CDL/Spectre/SPF/EDIF expect exactly one file per group.

        Args:
            format: Format name ('verilog', 'spice', 'cdl', 'spectre', 'spf', 'edif')
            files: Files for this format

        Returns:
            Tuple of (subckts dict, instances list, global_nets list)

        Raises:
            NetlistParseError if SPICE/CDL/Spectre/SPF/EDIF group has >1 files
        """
        if format == "verilog":
            # Verilog parser handles multiple files via existing pipeline
            # Run the pipeline and extract results
            sbckts, insts = self._verilog_parse_group(files)
            return sbckts, insts, []

        elif format == "edif":
            if len(files) != 1:
                raise NetlistParseError(
                    f"edif parser expects exactly one file in group; got {len(files)} files: {files}"
                )
            sbckts, insts, gbl_nets = self._parse_edif(files[0])
            return sbckts, insts, gbl_nets

        elif format == "spectre":
            if len(files) != 1:
                raise NetlistParseError(
                    f"spectre parser expects exactly one file in group; got {len(files)} files: {files}"
                )
            sbckts, insts, gbl_nets = self._parse_spectre(files[0])
            return sbckts, insts, gbl_nets

        elif format == "spf":
            if len(files) != 1:
                raise NetlistParseError(
                    f"spf parser expects exactly one file in group; got {len(files)} files: {files}"
                )
            sbckts, insts, gbl_nets = self._parse_spf(files[0])
            return sbckts, insts, gbl_nets

        else:  # spice, cdl, or unknown defaults to spice
            if len(files) != 1:
                raise NetlistParseError(
                    f"spice parser expects exactly one file in group; got {len(files)} files: {files}"
                )
            sbckts, insts, gbl_nets = self._parse_spice(files[0])
            return sbckts, insts, gbl_nets

    def _verilog_parse_group(self, fls: list[str]) -> tuple[dict[str, SubcktDef], list[Instance]]:
        """Run Verilog elaboration pipeline on a group of files.

        Returns (subckts, instances) without mutating self. Used by
        _dispatch_single_format for per-format Verilog groups.

        Args:
            fls: List of Verilog files

        Returns:
            Tuple of (subckts dict, instances list)
        """
        # Discover headers if this is a directory scan
        if os.path.isdir(self.filename):
            header_files = _sv_discover_headers(self.filename)
            if header_files:
                disc_defs, disc_vals = _sv_parse_defines(header_files, self.tvars)
                defs = self.defines | disc_defs
                define_vals = dict(self.define_values) if self.define_values else {}
                for k, v in disc_vals.items():
                    define_vals.setdefault(k, v)
            else:
                defs = self.defines
                define_vals = dict(self.define_values) if self.define_values else {}
        else:
            defs = self.defines
            define_vals = dict(self.define_values) if self.define_values else {}

        # Parse files
        work = [(f, self.tvars, defs, define_vals) for f in fls]
        nw = self.workers or min(cpu_count(), len(fls), 16)
        if nw > 1 and len(fls) > 4:
            from multiprocessing import Pool

            with Pool(nw) as pool:
                results = pool.map(_sv_parse_file, work)
        else:
            results = [_sv_parse_file(w) for w in work]
        all_mods = [m for batch in results for m in batch]

        if not all_mods:
            raise NetlistParseError("No modules parsed from Verilog files")

        # Specialize and assemble
        n_spec = _sv_specialize_modules(all_mods, define_vals)
        if n_spec:
            _logger.info(f"Specialized: {n_spec} new subckt variants")

        sbckts, instances_dicts = _sv_assemble(all_mods, top=self.top, define_values=define_vals)

        # Convert instances from dicts to Instance objects
        insts: list[Instance] = []
        for inst_dict in instances_dicts:
            insts.append(
                Instance(
                    name=inst_dict["name"],
                    cell_type=inst_dict["cell_type"],
                    nets=inst_dict["nets"],
                    parent_cell=inst_dict["parent_cell"],
                )
            )

        return sbckts, insts

    def _merge_format_results(
        self, per_fmt_rslt: dict[str, tuple[dict[str, SubcktDef], list[Instance], list[str]]]
    ) -> None:
        """Merge per-format parser outputs into self, applying format-priority collision policy.

        For each subckt name collision, applies non-empty-wins logic:
        - If one definition is empty (has no instance children) and the other is non-empty,
          the non-empty one wins (back-annotation of empty Spectre shells)
        - If both empty or both non-empty, existing _FORMAT_PRIORITY rank applies

        Iterates formats in descending _FORMAT_PRIORITY rank order. Instances always merged.
        global_nets concatenated and deduplicated.

        Args:
            per_fmt_rslt: Dict mapping format to (subckts, instances, global_nets) tuple
        """
        mrgd_sbckts: dict[str, SubcktDef] = {}
        mrgd_subckt_fmt: dict[str, str] = {}  # name -> format for logging
        all_insts: list[Instance] = []
        all_gbl_nets: list[str] = []

        # Sort formats by priority (highest first)
        sorted_fmts = sorted(
            per_fmt_rslt.keys(), key=lambda f: _FORMAT_PRIORITY.get(f, -1), reverse=True
        )

        # Build instance count per subckt per format (pre-merge)
        inst_cnt_by_subckt: dict[tuple[str, str], int] = {}
        for fmt in sorted_fmts:
            _, insts, _ = per_fmt_rslt[fmt]
            for inst in insts:
                key = (fmt, inst.parent_cell)
                inst_cnt_by_subckt[key] = inst_cnt_by_subckt.get(key, 0) + 1

        for fmt in sorted_fmts:
            sbckts, insts, gbl_nets = per_fmt_rslt[fmt]

            # Merge subckts with collision detection and non-empty-wins logic
            for name, sub in sbckts.items():
                if name in mrgd_sbckts:
                    # Collision: apply non-empty-wins logic
                    ex_fmt = mrgd_subckt_fmt[name]
                    ex_inst_cnt = inst_cnt_by_subckt.get((ex_fmt, name), 0)
                    new_inst_cnt = inst_cnt_by_subckt.get((fmt, name), 0)
                    existing_empty = ex_inst_cnt == 0
                    new_empty = new_inst_cnt == 0

                    if existing_empty and not new_empty:
                        # New definition is non-empty, existing is empty: replace (back-annotation)
                        _logger.info(
                            f"Back-annotating '{name}': empty from {ex_fmt} "
                            f"-> populated from {fmt} ({new_inst_cnt} instance(s))"
                        )
                        mrgd_sbckts[name] = sub
                        mrgd_subckt_fmt[name] = fmt
                    elif not existing_empty and new_empty:
                        # New definition is empty, existing is non-empty: keep existing
                        pass
                    else:
                        # Both empty or both non-empty: format-priority wins
                        winner_fmt = None
                        for check_fmt in sorted_fmts:
                            if check_fmt == fmt:
                                break
                            if name in per_fmt_rslt[check_fmt][0]:
                                winner_fmt = check_fmt
                                break
                        if winner_fmt:
                            _logger.warning(
                                f"Subckt '{name}' defined in both {winner_fmt} and {fmt}; "
                                f"keeping {winner_fmt} definition (priority {_FORMAT_PRIORITY.get(winner_fmt, -1)} > {_FORMAT_PRIORITY.get(fmt, -1)})"
                            )
                else:
                    mrgd_sbckts[name] = sub
                    mrgd_subckt_fmt[name] = fmt

            # Always merge instances
            all_insts.extend(insts)

            # Collect global nets
            all_gbl_nets.extend(gbl_nets)

        # Deduplicate global nets while preserving order
        seen = set()
        dupe_gbl_nets = []
        for net in all_gbl_nets:
            if net not in seen:
                seen.add(net)
                dupe_gbl_nets.append(net)

        self.subckts = mrgd_sbckts
        self.global_nets = dupe_gbl_nets
        for inst in all_insts:
            self._add_instance(inst)

    def _parse(self) -> None:
        """Dispatch to format-specific parser(s).

        Decision tree (3 cases):
        1. If self.format == 'mixed' (mixed-format directory pre-detected in __init__): per-file dispatch + merge
        2. Elif format is pre-pinned (user override or directory Verilog): single format dispatch
        3. Elif single file: detect format + single dispatch

        Note: __init__ pre-pins directories as either "verilog" (verilog-only)
        or "mixed" (mixed/non-verilog), so only cases 1–3 are reachable.
        """
        # Case 1: Mixed-format directory (pre-detected in __init__)
        if self.format == "mixed":
            frmt_grps = detect_format_per_file(self.files)
            grps_str = ", ".join(f"{fmt}({len(fls)})" for fmt, fls in sorted(frmt_grps.items()))
            _logger.info(f"Detected mixed-format directory with groups: {grps_str}")

            per_fmt_rslt: dict[str, tuple[dict[str, SubcktDef], list[Instance], list[str]]] = {}
            for fmt, fls in frmt_grps.items():
                sbckts, insts, gbl_nets = self._dispatch_single_format(fmt, fls)
                per_fmt_rslt[fmt] = (sbckts, insts, gbl_nets)

            self._merge_format_results(per_fmt_rslt)
            return

        # Case 2 & 3: Single format (pre-pinned or single file)
        if (
            self._user_format
            or (len(self.files) > 1 and self.format == "verilog")
            or len(self.files) == 1
        ):
            if self.format == "verilog":
                self._parse_verilog()
            elif self.format == "edif":
                sbckts, insts, gbl_nets = self._dispatch_single_format("edif", self.files)
                self.subckts = sbckts
                self.global_nets = gbl_nets
                for inst in insts:
                    self._add_instance(inst)
            elif self.format == "spectre":
                sbckts, insts, spf_pths = self._dispatch_single_format("spectre", self.files)
                self.subckts = sbckts
                for inst in insts:
                    self._add_instance(inst)
                # Lazy SPF parsing: register placeholders instead of eager-parsing
                if spf_pths:
                    self._register_spf_plchldr(spf_pths)
            elif self.format == "spf":
                sbckts, insts, gbl_nets = self._dispatch_single_format("spf", self.files)
                self.subckts = sbckts
                self.global_nets = gbl_nets
                for inst in insts:
                    self._add_instance(inst)
            else:  # spice, cdl, unknown
                sbckts, insts, gbl_nets = self._dispatch_single_format("spice", self.files)
                self.subckts = sbckts
                self.global_nets = gbl_nets
                for inst in insts:
                    self._add_instance(inst)
            return

    def _add_instance(self, instance: Instance) -> None:
        """Register an instance in all lookup indices."""
        self.instances_by_parent[instance.parent_cell].append(instance)
        self.instances_by_celltype[instance.cell_type].append(instance)
        self.instances_by_name[instance.name].append(instance)

    ################################################################################
    # SECTION: Lazy SPF Parsing
    # Description: Placeholder registration, on-demand materialization, and eager flush
    ################################################################################

    def _register_spf_plchldr(self, spf_pths: list[str]) -> None:
        """Register SPF cells as placeholders without parsing bodies.

        For each SPF path, peek-scan its .SUBCKT declarations and register
        placeholder SubcktDefs (pins populated from peek, body empty,
        is_placeholder=True, placeholder_source=spf_path). Map cell_name
        to spf_path in self.pndg_spf_fls so the tracer can materialize
        on demand.

        Non-empty-wins integration: if a cell already exists in self.subckts
        with a non-empty body, skip placeholder registration (existing
        definition wins). If existing entry is empty (Spectre shell),
        register placeholder over it; materialization will trigger the
        back-annotation merge.

        Inputs:
            spf_pths: List of resolved absolute SPF paths

        Outputs:
            None — mutates self.subckts, self.pndg_spf_fls, and instance indices
        """
        from netlist_tracer.parsers.peek import peek_spf_subckts

        for spf_pth in spf_pths:
            try:
                sbckts_in_fl = peek_spf_subckts(spf_pth)
            except Exception as e:
                _logger.warning(f"peek failed for {spf_pth}: {e}; skipping placeholder")
                continue

            for cell_nm, pn_lst in sbckts_in_fl:
                # Non-empty-wins: skip if a populated definition already exists
                existing = self.subckts.get(cell_nm)
                if existing and (existing.aliases or self.instances_by_parent.get(cell_nm)):
                    continue

                # Register placeholder (or overwrite empty shell)
                plchldr = SubcktDef(
                    name=cell_nm,
                    pins=pn_lst,
                    aliases={},
                    is_placeholder=True,
                    placeholder_source=spf_pth,
                )
                self.subckts[cell_nm] = plchldr
                self.pndg_spf_fls[cell_nm] = spf_pth

    def mtrl_spf(self, spf_pth: str) -> int:
        """Full-parse one SPF file and merge into self. Idempotent on path.

        Inputs:
            spf_pth: Absolute SPF path to materialize

        Outputs:
            int — number of subckts materialized (0 if already materialized)
        """
        # Idempotent: check if already materialized
        if spf_pth in self.mtrl_spf_fls:
            return 0

        try:
            sbckts_spf, insts_spf, _ = parse_spf(spf_pth, include_paths=self.include_paths)
        except Exception as e:
            _logger.warning(f"Failed to parse SPF '{spf_pth}': {type(e).__name__}: {e}")
            return 0

        # Merge subckts: non-empty-wins (placeholder replaced by populated body)
        cnt = 0
        for sbckt_nm, sbckt_spf in sbckts_spf.items():
            if sbckt_nm not in self.subckts:
                # New subckt
                self.subckts[sbckt_nm] = sbckt_spf
                cnt += 1
            else:
                # Collision: check if existing is empty (Spectre shell)
                has_inst = any(i.parent_cell == sbckt_nm for i in self.instances_by_parent.get(sbckt_nm, []))
                if not has_inst:
                    # Empty: replace with populated SPF body
                    _logger.info(f"Back-annotating '{sbckt_nm}': empty shell -> populated SPF body")
                    self.subckts[sbckt_nm] = sbckt_spf
                    cnt += 1

        # Merge instances: append to all indices
        for inst in insts_spf:
            self._add_instance(inst)

        # Cleanup: remove cell_names from pending if their placeholder_source is this file
        for sbckt_nm in list(self.pndg_spf_fls.keys()):
            if self.pndg_spf_fls[sbckt_nm] == spf_pth:
                del self.pndg_spf_fls[sbckt_nm]

        # Mark as materialized
        self.mtrl_spf_fls.add(spf_pth)
        _logger.info(f"Materialized SPF: {spf_pth} ({cnt} subckts)")
        return cnt

    def mtrl_all_pndg(self) -> int:
        """Flush all pending SPF files. Returns total subckts materialized.

        Inputs:
            (none)

        Outputs:
            int — total subckts materialized across all files
        """
        # Guard: if fields don't exist (e.g., synthetic parser), no pending
        if not hasattr(self, 'pndg_spf_fls'):
            return 0

        ttl = 0
        pndg_cpy = dict(self.pndg_spf_fls)
        for _, spf_pth in pndg_cpy.items():
            # Deduplicate: each unique path is materialized once
            if spf_pth not in self.mtrl_spf_fls:
                cnt = self.mtrl_spf(spf_pth)
                ttl += cnt
        if ttl > 0:
            _logger.info(f"Materialized {ttl} subckts from pending SPF files")
        return ttl

    def _load_json(self, filepath: str) -> None:
        """Load pre-parsed netlist data from JSON cache.

        Supports v0 (legacy, no schema_version field), v1 (aliases as
        list of [lhs, rhs] pairs), v2 (aliases as dict, compact encoding),
        and v3 (includes instance.params and subckt_params dicts). Raises
        NetlistParseError if schema_version is newer than supported.
        """
        with open(filepath) as f:
            data = json.load(f)

        # Check schema version for forward compatibility
        schema_version = data.get("schema_version", 0)  # v0 if missing
        if schema_version > _CACHE_SCHEMA_VERSION:
            raise NetlistParseError(
                f"Cache schema version {schema_version} is newer than supported "
                f"version {_CACHE_SCHEMA_VERSION}; update netlist-tracer."
            )
        if schema_version == 0:
            _logger.info(f"Loading legacy v0 cache (no schema_version field): {filepath}")

        self.format = data.get("format", "verilog")
        self.source_path = data.get("source", filepath)
        _logger.info(f"Loading pre-parsed cache: {filepath}")
        _logger.info(f"Source: {self.source_path}")

        sbckt_prms_map = data.get("subckt_params", {})

        for name, entry in data["subckts"].items():
            if isinstance(entry, dict):
                pins = entry.get("pins", [])
                aliases = entry.get("aliases") or {}
                sub = SubcktDef(name=name, pins=pins)
                if aliases:
                    sub.aliases = dict(aliases)
                self.subckts[name] = sub
            else:
                self.subckts[name] = SubcktDef(name=name, pins=entry)
            # Assign params from subckt_params dict if present
            if name in sbckt_prms_map:
                self.subckts[name].params = dict(sbckt_prms_map[name])

        for cell, pairs in (data.get("aliases") or {}).items():
            subckt: SubcktDef | None = self.subckts.get(cell)
            if subckt is not None and pairs:
                # v2 stores aliases as dict {lhs: rhs}; v0/v1 as list of [lhs, rhs] pairs.
                items = pairs.items() if isinstance(pairs, dict) else pairs
                merge_aliases_into_subckt(subckt, items)

        for inst_data in data["instances"]:
            inst = Instance(
                name=inst_data["name"],
                cell_type=inst_data["cell_type"],
                nets=inst_data["nets"],
                parent_cell=inst_data["parent_cell"],
            )
            # Assign params from instance entry if present (v3+); default to empty dict
            if "params" in inst_data:
                inst.params = dict(inst_data["params"])
            self._add_instance(inst)

    def _parse_spice(self, filepath: str) -> tuple[dict[str, SubcktDef], list[Instance], list[str]]:
        """Parse SPICE/CDL netlist and return results without mutation.

        Args:
            filepath: Path to SPICE/CDL file

        Returns:
            Tuple of (subckts dict, instances list, global_nets list)
        """
        subckts, instances, global_nets = parse_spice(filepath, include_paths=self.include_paths)
        return subckts, instances, global_nets

    def _parse_edif(self, filepath: str) -> tuple[dict[str, SubcktDef], list[Instance], list[str]]:
        """Parse EDIF netlist and return results without mutation.

        Args:
            filepath: Path to EDIF file

        Returns:
            Tuple of (subckts dict, instances list, empty global_nets list)
        """
        subckts, instances = parse_edif(filepath, bs_rdr=self._bus_order)
        return subckts, instances, []

    def _parse_spectre(
        self, filepath: str
    ) -> tuple[dict[str, SubcktDef], list[Instance], list[str]]:
        """Parse Spectre netlist and return results without mutation.

        Args:
            filepath: Path to Spectre file

        Returns:
            Tuple of (subckts dict, instances list, spf_paths list for lazy registration)
        """
        subckts, instances, spf_paths = parse_spectre(filepath, include_paths=self.include_paths)
        return subckts, instances, spf_paths

    def _parse_spf(self, filepath: str) -> tuple[dict[str, SubcktDef], list[Instance], list[str]]:
        """Parse SPF netlist and return results without mutation.

        Args:
            filepath: Path to SPF/DSPF file

        Returns:
            Tuple of (subckts dict, instances list, global_nets list)
        """
        subckts, instances, global_nets = parse_spf(filepath, include_paths=self.include_paths)
        return subckts, instances, global_nets

    def _parse_verilog(self) -> None:
        """Full SV elaboration pipeline."""
        if os.path.isdir(self.filename):
            header_files = _sv_discover_headers(self.filename)
            if header_files:
                disc_defs, disc_vals = _sv_parse_defines(header_files, self.tvars)
                self.defines = self.defines | disc_defs
                if self.define_values is None:
                    self.define_values = {}
                for k, v in disc_vals.items():
                    self.define_values.setdefault(k, v)
                _logger.info(
                    f"Headers: {len(header_files)} scanned; "
                    f"{len(disc_defs)} defines, "
                    f"{len(disc_vals)} numeric values discovered"
                )
        if self.define_values is None:
            self.define_values = {}

        work = [(f, self.tvars, self.defines, self.define_values) for f in self.files]
        nw = self.workers or min(cpu_count(), len(self.files), 16)
        if nw > 1 and len(self.files) > 4:
            from multiprocessing import Pool

            with Pool(nw) as pool:
                results = pool.map(_sv_parse_file, work)
        else:
            results = [_sv_parse_file(w) for w in work]
        all_mods = [m for batch in results for m in batch]

        if not all_mods:
            raise NetlistParseError("No modules parsed from Verilog files")

        n_spec = _sv_specialize_modules(all_mods, self.define_values)
        if n_spec:
            _logger.info(f"Specialized: {n_spec} new subckt variants")

        subckts, instances_dicts = _sv_assemble(
            all_mods, top=self.top, define_values=self.define_values
        )
        self.subckts = subckts
        # Convert instances from dicts to Instance objects
        for inst_dict in instances_dicts:
            inst = Instance(
                name=inst_dict["name"],
                cell_type=inst_dict["cell_type"],
                nets=inst_dict["nets"],
                parent_cell=inst_dict["parent_cell"],
            )
            self._add_instance(inst)

    def validate_connections(self, verbose: bool = False) -> list[tuple[str, str, str, int, int]]:
        """Validate instance pin counts match cell definitions.

        Args:
            verbose: Print warnings for mismatches to stderr.

        Returns:
            List of mismatch tuples (parent_cell, inst_name, cell_type,
            n_connections, n_pins).
        """
        mismatches = []
        for celltype, insts in self.instances_by_celltype.items():
            sub = self.subckts.get(celltype)
            if sub is None:
                continue
            n_pin = len(sub.pins)
            for inst in insts:
                n_conn = len(inst.nets)
                if n_conn != n_pin:
                    mismatches.append((inst.parent_cell, inst.name, celltype, n_conn, n_pin))
        if verbose:
            for parent, name, ctype, nc, np_ in mismatches:
                _logger.warning(
                    f"WARNING: {parent}/{name} (cell={ctype}): {nc} connections but cell has {np_} pins"
                )
        return mismatches

    def dump_json(self, out_path: str) -> None:
        """Write parsed model to JSON cache file.

        Output is compact (no indentation) and machine-oriented. Use
        `python3 -m json.tool < cache.json` to inspect by eye.

        Schema version (v3) adds:
          - Instance entries now include 'params' field when non-empty (omitted when empty)
          - Top-level 'subckt_params' dict maps subckt name -> params dict for subckts with non-empty params

        Schema version (v2) differences vs older caches the loader still
        understands (v0/v1):
          - Aliases stored as dict {lhs: rhs} (was list of [lhs, rhs] pairs)
          - Compact JSON output (no indentation)
          - No defensive list copies on pin/net references

        Args:
            out_path: Output file path.
        """
        # Eager-flush all pending SPF files to ensure complete state in cache
        self.mtrl_all_pndg()

        subckts_out = {name: sub.pins for name, sub in self.subckts.items()}
        instances_out = [
            {
                "name": inst.name,
                "cell_type": inst.cell_type,
                "nets": inst.nets,
                "parent_cell": inst.parent_cell,
                **({"params": dict(inst.params)} if inst.params else {}),
            }
            for insts in self.instances_by_parent.values()
            for inst in insts
        ]
        aliases_out = {name: dict(sub.aliases) for name, sub in self.subckts.items() if sub.aliases}
        sbckt_prms = {name: dict(sub.params) for name, sub in self.subckts.items() if sub.params}
        output = {
            "schema_version": _CACHE_SCHEMA_VERSION,
            "format": self.format,
            "source": self.source_path,
            "subckts": subckts_out,
            "instances": instances_out,
            "aliases": aliases_out,
        }
        if sbckt_prms:
            output["subckt_params"] = sbckt_prms
        out_dir = os.path.dirname(os.path.abspath(out_path))
        if out_dir:
            os.makedirs(out_dir, exist_ok=True)
        with open(out_path, "w", buffering=65536) as fh:
            json.dump(output, fh, separators=(",", ":"))
        kb = os.path.getsize(out_path) / 1024
        _logger.info(f"Output: {out_path} ({kb:.0f} KB)")
