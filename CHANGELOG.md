# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-05-17

### Added

- **Per-net bidirectional trace** (`-net` flag): traces a single net across
  the design hierarchy, following instantiation chains. Supports Verilog/SPICE
  hierarchical designs and mixed-format netlists.
- **RTL UDP (user-defined primitive) support**: Verilog primitives with
  `primitive ... table ... endtable endprimitive` syntax now parse correctly;
  used in RTL designs for combinational/sequential logic modeling.
- **`+define+` and `+incdir+` CLI flags**: Verilog tool-style flags accepted
  for preprocessor defines and include paths (replaces the removed `-defines`/`-include`).
- **SPEF as first-class format**: SPEF (Standard Parasitic Exchange Format)
  recognized as a primary output format; can be dumped via `-output FILE.spef`.
- **Lazy SPF parsing with gzip support**: SPF/DSPF files and `.gz`-compressed
  variants glob correctly; parsing deferred until first subckt access.
- **Genvar block-label preservation**: `generate ... for (...) begin: label`
  block labels now correctly preserved in flat instance names (e.g.,
  `label[0].instance`, `label[1].instance`).
- **`-debug` CLI flag**: Enable DEBUG-level logging output for both
  `netlist-tracer` and `netlist-parser` commands.

### Changed

- **Unified include-file resolver**: resolves `.include`/`.inc` (Spectre,
  DSPF) and `dspf_include` recursively with shared path semantics; supports
  relative paths from file location. SPEF `.gz` variant auto-detected.
- **CLI flag removals and renames**: 
  - `-pin` merged into `-net` (unified net tracing)
  - `-format`, `-trace_format`, `-defines`, `-include` removed (auto-detection, `-output`, `+define+`/`+incdir+`)
  - `-top CELL` renamed to `-topcell CELL`
- **Fixture tree consolidation**: merged 50 synthetic test fixtures down to 22
  consolidated multi-subckt/multi-module fixtures to reduce test noise and
  improve maintainability.

### Fixed

- **Merge of `-pin` trace into `-net` trace**: single unified net-trace
  mechanism replaces separate pin/net walk logic.
- **Stream dump_json output**: JSON stream now correctly flushes per-record
  for live pipeline consumption (not end-buffered).
- **SPEF overlay mode deprecation**: SPEF-as-net-overlay no longer supported
  (now primary format via `-output`).

## [0.5.0] - 2026-05-16

### Added

- **Per-file format detection and per-file parser dispatch**: directories
  containing mixed-format files (e.g., SPICE + SPF + Verilog) now parse
  correctly. Each file detected independently, dispatched to its parser,
  results merged. Subckt-name collisions resolved by format priority
  (`spectre > cdl > spice > verilog > edif > spf`) with `WARNING`.
- **SPF/DSPF parser** (`parsers/spf.py`): standalone parser for the SPF
  family (DSPF, RSPF, CCSPF) of post-layout parasitic netlists.
  Recognizes `*|DSPF`/`*|RSPF`/`*|CCSPF` content markers and
  `.spf`/`.dspf` extensions; gzip-compressed `.gz` supported. Honors
  `*|DIVIDER` and `*|DELIMITER` for hierarchical net naming. Captures
  `*|GROUND_NET`, `*|NET` (with PF/F/N/U/M unit conversion), `*|I`
  (pin alias) directives to `SubcktDef.params`. Series-R reduction
  post-pass merges purely-series R chains into single equivalent R's
  named `<min_R>_to_<max_R>` with summed value (~60% R-count reduction
  on the test DSPF) — preserves true topology with minimal tree.
- **SystemVerilog `interface` parsing**: `interface ... endinterface`
  recognized as a structural unit. Each interface produces a
  `SubcktDef` with `_kind='interface'` and `_modports`; each modport
  produces an additional `SubcktDef` named `<interface>__mp_<modport>`
  with `_pin_directions` mapping each bit-expanded pin to
  input/output/inout.
- **EDIF `(rename safe "original")` preservation**: original names
  captured in `SubcktDef.params['_edif_original_name']` and
  `Instance.params['_edif_original_name']`.
- **EDIF property capture**: cell/instance `(property ...)` blocks
  collected into `params['_edif_properties']` with typed values
  (string, integer, real, boolean).
- **EDIF multi-library tracking**: `params['_edif_library']` records
  source library; same-named cells across libraries trigger `WARNING`
  (first-wins).
- **EDIF `bus_order` kwarg**: `parse_edif(file, bus_order='lsb_first')`
  and `NetlistParser(file, bus_order='lsb_first')` reverse port-array
  bit expansion for SpyDrNet compatibility. Default `'msb_first'`
  unchanged.
- **Tracer lateral walk on leaf primitives**: when a tracer step lands
  on an instance whose `cell_type` has no `SubcktDef`, the tracer now
  walks laterally — R/L emit `direction="thru"` with value-aware label
  (`<{value}ohm>`/`<{value}H>`); C/K are skipped entirely (parasitic
  noise, DC-open); other cell_types (transistors, sources) emit
  `direction="endpoint"` so users see real terminal connections (e.g.,
  pin D/G/S/B of a MOSFET). Hierarchical netlists unaffected.
- **`NetlistParser.peek_pins(file, cell, format)` classmethod**: cheap
  pre-scan of any supported format to return a cell's pin list without
  running the full parse. CLI uses it to validate `-pin` before parse;
  on miss prints suggestions and exits 1 in O(seconds) instead of
  waiting for a multi-minute full parse. Cell-not-found returns `None`
  (CLI falls through to full parse — no false negatives).
- **`-format` kwarg + CLI flag** to override format auto-detection.
- **UTF-8 tolerance**: format-detect, EDIF, and include resolvers now
  use `errors="replace"` so files with Latin-1 bytes in comments don't
  fail.
- **HSPICE `;` and `$` inline comments**, controlled sources B/E/F/G/H,
  K coupled-inductor, `.global` directive.
- **Verilog generate-if / generate-case** unrolling; built-in gate
  primitives; `defparam` resolution.
- **Verilog-A port shells** with `electrical` ports and `analog begin`
  body stripping.

### Changed

- **JSON cache schema bumped 2 → 3**: now serializes `Instance.params`
  and `SubcktDef.params`. Older v0/v1/v2 caches still load (params
  defaulted to empty dict). Existing applications continue to work;
  SPF merged R values, SV modport directions, EDIF metadata now
  survive roundtrip.
- **SPF subnode handling**: numeric subnodes (e.g. `clk_in:20`)
  preserved as distinct nets (not collapsed). Letter subnodes
  (`:D`/`:G`/`:S`/`:B`) always preserved. The series-R reduction
  handles tracer ergonomics.
- **`format_path` renders real instance names** for lateral-walk steps
  (e.g. `R|R5_22|<2208ohm>` instead of `R|<internal>|R:1`).
- **Format dispatch priority** for multi-format directory collisions:
  `spectre=5 > cdl=4 > spice=3 > verilog=2 > edif=1 > spf=0`.
- **Sky130 regression baseline refreshed** to reflect the new tracer
  behavior on flat-leaf-primitive cells (was 0 paths from pin A; now
  2 paths reaching nfet/pfet gates — semantic improvement).

### Fixed

- **SPF X-instance parsing**: `cell_type` was being set to StarRC's
  trailing `$angle=N` annotation; now correctly identifies the actual
  model (e.g. `nch_ulvt_mac`). Params (`L`, `nfin`, `$x`, `$y`, ...)
  captured.
- **Tracer dead-end heuristic** no longer emits spurious bare-prefix
  fragments when a lateral-walk endpoint already recorded a path.
- **Cache `.gz` detection** in `detect.py`: gzipped SPF/DSPF files no
  longer raise `UnicodeDecodeError` on format-detection.
- **`*|DSPF`/`*|RSPF`/`*|CCSPF` format-marker lines** silently
  consumed (no more spurious "Unknown SPF directive" warning per
  parse).
- **Generate-loop alias expansion** correctly handles nested cases.

### Internal

- **Test consolidation**: 26 test files merged into 14 by per-format
  grouping with class-based subdivision. Zero semantic test changes.
  Real-DSPF env-gated smoke tests folded into `test_parser_spf.py`;
  real-cache env-gated smoke tests folded into `test_cache.py`.
- Vowel-elimination naming convention adopted in new code.
- ruff + mypy clean across the codebase.

## [0.4.1] - 2026-05-11

### Added

- `format` parameter on `NetlistParser.__init__()` to override auto-detection. Allowed values: `spice`, `cdl`, `spectre`, `verilog`, `edif`, `auto` (default). Invalid values raise `NetlistParseError`.
- `-format` CLI flag on `netlist-tracer` and `netlist-parser` commands for explicit format specification (same allowed values).
- `parse_numerical` helper now lives at `netlist_tracer.parsers._numerics` (still re-exported from both `netlist_tracer` and `netlist_tracer.parsers` for backward compatibility).

### Changed

- Format auto-detection is now content-first. Previously file extensions (`.edif`, `.edn`, `.edf`, `.va`, `.vams`, `.vha`) short-circuited detection; now the first ~4 KB of each file is scored for known format markers. Highest-scoring format wins; extension consulted only as a tiebreaker. See `detect_format` docstring for marker scoring weights.
- Release workflow (`.github/workflows/release.yml`) trigger changed from `push: tags: ['v*']` to `workflow_dispatch:` (manual only). Pre-1.0 development tags no longer trigger PyPI publishing. Tag-based publishing will resume when PyPI credentials (trusted publishing or API token) are configured.

### Fixed

- (No user-facing bug fixes in v0.4.1; release focused on format detection refactor and explicit format control for forward compatibility)

## [0.4.0] - 2026-05-10

### Added

- HSPICE inline `;` and `$` end-of-line comments (quote-state-aware).
- Continuation lines with `+` now merge across intervening `*` comment lines.
- Controlled-source elements B/E/F/G/H recognized as instances with cell types `B_BSRC`, `E_VCVS`, `G_VCCS`, `F_CCCS`, and `H_CCVS`. Control source names stored in `params['_vctrl']` for F and H types.
- K (coupled-inductor) elements recognized; coupling coefficient stored in `params['_k_coeff']`.
- `.global` directive parsing; global net list accessible via `NetlistParser.global_nets`.
- Public helper function `parse_numerical(value_str)` for engineering-suffix-aware numeric parsing (supports T/G/MEG/K/M/U/μ/N/P/F/A); importable from `netlist_tracer` and `netlist_tracer.parsers`.
- Verilog/SystemVerilog `generate-if` and `generate-case` block unrolling for parameterized modules.
- Built-in Verilog gate primitives (and/or/nand/nor/xor/xnor/buf/not) recognized as instances with synthesized `SubcktDef` entries per (type, arity) pair.
- `defparam` resolution with Verilog LRM semantics: `defparam` values override inline `#()` parameter assignments.
- Cache roundtrip parity test deepened with structural assertions: subckt keys, instance names, and alias maps checked before trace-equality comparison.
- Performance regression harness: synthetic 1000-instance Verilog test marked `@pytest.mark.slow`, opt-in via `-m slow` flag.
- Verilog-A port shells: `electrical` ports recognized as structural pins; `analog begin ... end` and single-statement `analog ...;` blocks stripped to whitespace; file extensions `.va`, `.vams`, `.vha` auto-detected.
- Spectre `ahdl_include "path"` directive: routes referenced Verilog-A files to the Verilog parser, then merges resulting modules into the design. Spectre module definitions win on collision; malformed `.va` files emit WARNING and are skipped.
- Bus-bit notation `<N>` (Spectre/Cadence convention) recognized alongside existing `[N]` (Verilog) convention in `expand_pin` and bare-bus suggestion logic.

### Changed

- `parse_spice()` function signature now returns 3-tuple `(subckts, instances, global_nets)` to expose `.global` net list; existing callers updated. Downstream consumers using `NetlistParser` API see only the new `parser.global_nets` attribute.
- Spectre escape syntax extended: in addition to `\<` and `\>`, the line-cleanup pass now strips backslash escapes for `\(`, `\)`, `\[`, `\]`, `\,` to handle net names with these special characters.
- Internal table `_PRIMITIVE_PIN_NAMES` introduced for Verilog gate primitives to ensure consistent pin orderings in synthesized `SubcktDef` cells (e.g., `__prim_nand_3__`).

### Fixed

- (No fixes in v0.4.0 — all changes are feature additions)

## [0.3.1] - 2026-05-10

### Added

- Environment variable expansion in include path resolution: SPICE/Spectre include directives like `.include '$PDK_ROOT/models.lib'` or `include "$PDK_ROOT/..." section=NAME` now resolve via the current process environment (uses `os.path.expandvars`). Unset variables are left literal and produce the standard 'include path not found' error — which try-and-degrade callers downgrade to a WARNING.
- Spectre `include "path" section=NAME` form recognition with section-aware emission: previously only the bare `include "path"` form was matched; lines with the `section=` keyword were silently ignored. The directive now resolves the path, scans the inlined file for matching `library NAME ... endlibrary NAME` markers, and emits only the matched range. Path unresolvable or section name absent → WARNING + skip without raising.

### Changed

- CLI flag rename: `-I` / `--include-path` is now `-include` (single-dash long-form, matching the rest of the CLI: `-cell`, `-pin`, `-netlist`, `-max_depth`, `-trace_format`, `-defines`). This is a breaking change with no alias; update scripts that used `-I` or `--include-path`.
- `.lib path SECTION` and Spectre `include "path" section=NAME` are now SECTION-AWARE: the resolver scans the inlined file for matching `.lib SECTION ... .endl SECTION` (SPICE) or `library NAME ... endlibrary NAME` (Spectre) markers and emits only the lines between them. Path unresolvable → WARNING + skip. Section name absent in resolved file → WARNING + skip. Previously named-section `.lib` directives were always skipped with a warning, then briefly (v1.2 of this patch) inlined the whole file with the section name logged as ignored — both prior behaviors are superseded.
- Bare `.lib path` (no section name) directives now also use try-and-degrade: unresolvable paths emit a WARNING and are skipped instead of raising. This prevents HSPICE intra-file `.lib SECTION_NAME` opener markers — which are syntactically identical to bare-form `.lib path` includes — from aborting parse when they slip past the section-aware scanner. `.include` and `.inc` directives keep their strict raise-on-unresolvable behavior.
- Internal terminology: `libname` renamed to `section` in `parsers/includes.py` and in WARNING/INFO log message text. The third token of `.lib path token` is the section name selecting a `.lib SECTION ... .endl SECTION` block within the resolved file — not a library name. No public-API or behavioral change.

### Fixed

- Cycle detection no longer false-positives on multiple sections requested from the same library file. The cycle-detection stack now keys on `(path, section_filter)` tuples instead of path alone. Two `.lib path SECTION_A` and `.lib path SECTION_B` calls into the same file now correctly recognize that they are distinct logical include units and do not form a cycle.
- `.lib` try-and-degrade error swallowing was too broad: cycle-detection errors and other hard parse failures were incorrectly caught and suppressed as WARNING+skip. v0.3.1 now discriminates exception types: only `IncludePathNotFoundError` (unresolvable paths) triggers degradation; cycle detection errors raise `NetlistParseError` and propagate, resulting in exit code 1. This restores the correct behavior of treating cycles as hard failures instead of recoverable missing-path issues.
- CLI exit-code regression coverage: added subprocess-style tests that lock in `netlist-tracer` and `netlist-parser` returning exit code 1 on hard parse failures (e.g. `.include` with an unresolvable path) and exit code 0 on try-and-degrade WARN+skip outcomes. The CLI source already returned the correct exit codes; the tests guard against regression.
- README CLI options table: removed inaccurate "or comma-separated" phrase from the include-path row. The flag accepts repeated invocations only (`-include /a -include /b`), not comma-separated lists.

## [0.3.0] - 2026-05-10

### Added

- EDIF 2.0.0 and 3.0/4.0 parser with s-expression parsing, MSB-first bus expansion, and hierarchical cell/instance/net resolution.
- Recursive include-statement support for SPICE/CDL (`.include`, `.inc`, `.lib` directives) and Spectre (`include` and `simulator lang=spice` blocks) with cycle detection, tilde expansion, and absolute path support.
- New `-I <dir>` / `--include-path <dir>` CLI flag (repeatable on both `netlist-tracer` and `netlist-parser` commands); new `include_paths=[...]` library API kwarg on `NetlistParser.__init__()`.
- Vendored test fixtures: 3 EDIF designs from SpyDrNet (BSD-3 licensed) and 1 NGSpice analog SPICE example with include directives (GPL-2.0-or-later, separate work).
- Comprehensive test suite: EDIF parser and tracer regression tests with byte-identical golden baselines, NGSpice SPICE regression tests, cache roundtrip parity across all 3 formats, and 12+ include-statement tests covering cycle detection, search-path resolution, and Spectre-specific cases.
- CLI auto-detection for `.edif` and `.edn` file extensions.
- "Did you mean" suggestions extended to EDIF parser cell and pin lookup errors.
- SPICE/CDL flat-deck top synthesis: testbench files with instance lines at file scope (no enclosing `.subckt`) now have a synthetic top-level cell auto-generated with the name `__<filename_stem>__`, making those instances visible to the tracer. This enables UP-walk traces to surface sibling instances and shared nets at the testbench level. Hierarchical netlists with explicit `.subckt` wrappers are unaffected. Primitive devices (R, C, V, M) at file scope remain out of scope for hierarchy traversal.

### Changed

- `NetlistParser.__init__()` now accepts optional `include_paths: list[str] | None = None` kwarg for include search directories (backward-compatible).
- SPICE and Spectre parsers no longer raise on multi-file inputs when includes are involved; included files are inlined transparently.
- Spectre flat-deck top-level synthesis now uses the `__<filename_stem>__` naming convention to match SPICE behavior and reduce collision risk with real cell names. Previously, the synthetic top used the bare filename stem.
- JSON cache schema remains v2 (no migration required for existing caches).

### Fixed

- Include cycle detection now correctly distinguishes cycles from diamond-shaped dependencies.
- Ruff UP007 rule suppressed for Python 3.9 compatibility (Union[X, Y] syntax).

## [0.2.0] - 2026-05-09

### Added

- Multi-format netlist parsing with automatic format detection: CDL, SPICE, Spectre, Verilog, SystemVerilog.
- Bidirectional hierarchical signal tracing (`BidirectionalTracer`) with per-bit alias resolution.
- Two CLI commands:
  - `netlist-tracer` — trace signals through a netlist (file, directory, or JSON cache).
  - `netlist-parser` — parse a netlist and dump it to a JSON cache for fast re-loads.
- `BidirectionalTracer.trace_pins(start_name, pins=None, target_name=None, max_depth=None)` library API. Returns `{pin_name: [paths]}`. When `pins=None`, traces every bit-level pin of the cell.
- `expand_pin(subckt, name)` helper: returns `[name]` for an exact pin, all indexed members for a bare bus base name, or `[]` for an unknown name.
- `-pin` CLI flag accepts:
  - exact bit-level names (`-pin clk` or `-pin data[3]`)
  - comma-separated lists (`-pin clk,resetn`)
  - repeated flags (`-pin clk -pin resetn`)
  - bare bus base names (`-pin mem_addr` expands to all `mem_addr[0..N]` bits as separate output sections)
  - omitted entirely (traces every bit-level pin of the cell)
- `-trace_format json` produces machine-readable output. Schema:
  ```
  {tool, version, netlist, cell, target, max_depth,
   pins: {name: {paths: [{formatted, steps: [{cell, pin_or_net, direction, instance_name, inst_stack}]}]}}}
  ```
  Info-level logs are suppressed in this mode so stdout is pure JSON.
- JSON cache schema versioning. Loaders accept v0 (legacy, no field), v1 (aliases as list of pairs, indented), and v2 (aliases as dict, compact encoding) and raise `NetlistParseError` for unknown future versions. Existing caches load without modification.
- JSON cache write is now ~2-3× faster on large designs and produces ~25% smaller files (compact encoding, dict-form aliases, removed redundant defensive list copies). Sample workload (1,728-module SystemVerilog cache): write 1.8 s → 0.6 s, file 36 MB → 27 MB.
- Nested `generate-for` blocks: alias unrolling now iterates to fixed-point (max depth 8) so loop variables are correctly substituted in 2-level nested blocks.
- Custom exception hierarchy: `NetlistError`, `NetlistParseError`, `TraceError`.
- `"Did you mean: [...]"` suggestion list when a pin or cell name is not found, including bare-bus-name suggestions when an indexed bus exists.
- Parameter specialization for parameterized modules (mangled cell variants).
- Concat-form per-bit decomposition for multi-bit vector assignments.
- Supply-net and constant-tie detection.
- Test suite with 40 always-on tests plus 4 optional local-cache smoke tests gated by the `NETLIST_TRACER_LOCAL_CACHE` environment variable.
- Regression baselines for the vendored picorv32 fixture (ISC) and sky130 inverter fixture (Apache-2.0) covering both parser output and CLI output.
- Type hints on the public API; mypy strict on public modules with a permissive override for the SystemVerilog elaboration helpers.
- Ruff lint + format configuration; clean on initial release.
- GitHub Actions CI on push and PR: matrix lint + test on Python 3.9, 3.10, 3.11, 3.12, 3.13.
- PyPI release workflow that triggers on `v*` tag push. Supports trusted publishing (preferred) or `PYPI_API_TOKEN` fallback. The workflow is in place but does not run until a tag is pushed and PyPI publishing is configured.
- Issue and pull-request templates plus a `CODEOWNERS` file under `.github/`.
- README badges: CI status, license, supported Python versions, last commit.
- Four runnable example scripts under `examples/` covering parse + inspect, single-signal trace, JSON cache build/reload, and multi-pin tracing with bus expansion.
- README, CONTRIBUTING, LICENSE (MIT), and `.gitattributes`.

### Changed

- Error and warning messages use `ERROR:` / `WARNING:` (all caps) prefix and route to `stderr` so JSON output remains parseable when piped.
- The CLI exits with status 1 on resolution failure (bad cell, bad pin, bus name with no members) instead of 0 with a misleading "no paths found" message.
- Multi-pin output section headers display the deduplicated path count.
- `python_version = "3.10"` in mypy config (mypy 2.0+ requires 3.10+ as a target). The project still runs on Python 3.9 at runtime — every annotation file uses `from __future__ import annotations`, and the CI matrix runs the full test suite on 3.9.
- Suppressed ruff `UP045` (the `Optional[X]` → `X | None` suggestion) since the project supports Python 3.9, where the pipe syntax is invalid as a runtime annotation.

### Fixed

- Verilog parser: `assign` statements inside `generate-for` blocks now produce one alias per loop iteration with concrete integer indices (e.g. `out[0] -> in[0]`, `out[1] -> in[1]`), rather than a single literal-`i` alias (`out[i] -> in[i]`). The loop variable substitution now runs before alias extraction, mirroring what was already done for instance extraction.
