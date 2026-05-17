"""Multi-format netlist parser and bidirectional signal tracer."""

from importlib.metadata import PackageNotFoundError, version

from netlist_tracer.exceptions import NetlistError, NetlistParseError, TraceError
from netlist_tracer.model import Instance, SubcktDef, merge_aliases_into_subckt
from netlist_tracer.parser import NetlistParser
from netlist_tracer.parsers._numerics import parse_numerical
from netlist_tracer.tracer import BidirectionalTracer, TraceStep, format_path

try:
    __version__ = version("netlist-tracer")
except PackageNotFoundError:
    __version__ = "0.0.0+unknown"

__all__ = [
    "NetlistParser",
    "SubcktDef",
    "Instance",
    "merge_aliases_into_subckt",
    "BidirectionalTracer",
    "TraceStep",
    "format_path",
    "parse_numerical",
    "NetlistError",
    "NetlistParseError",
    "TraceError",
]
