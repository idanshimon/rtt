"""meetalign: align an automatic transcript with a meeting app's transcript.

Pure library (stdlib only, no RTT imports) so any recorder, backend or app can use it:

    from meetalign import align, parse_meeting
    result = align(my_lines, teams_text, refine=True, resolver=None)
    for index, new_line in result.changes.items(): ...

Pieces, each replaceable:
  sources   meeting-app transcript text  -> [Turn]          (register_source for new formats)
  lines     your transcript line strings <-> [Line]         (register_line_format for new layouts)
  names     who said each line (time + words, label votes)
  refine    the app's wording for the part both transcripts share; learnable misheard words
  ai        optional resolver for lines words can't decide  (any callable prompt -> text)
  bursts    accidental status-line bursts (tidy)
  api       align(): the whole pipeline, returns an AlignResult (no side effects)
CLI: `rtt-align` (meetalign/cli.py).
"""
from .model import AlignResult, Line, LineMatch, TextFix, Turn
from .sources import parse_meeting, register_source, SOURCES
from .lines import LineFormat, RegexLineFormat, RTT_FORMAT, get_line_format, register_line_format, LINE_FORMATS
from .names import match_names
from .refine import learnable, refine_lines
from .ai import Resolver, copilot_resolver, resolve_unnamed, get_resolver, RESOLVERS, register_resolver
from .bursts import find_bursts, RTT_MARKER_RE
from .api import align

__all__ = ["align", "AlignResult", "Line", "LineMatch", "TextFix", "Turn", "parse_meeting", "register_source",
           "SOURCES", "LineFormat", "RegexLineFormat", "RTT_FORMAT", "get_line_format", "register_line_format",
           "LINE_FORMATS", "match_names", "refine_lines", "learnable", "Resolver", "copilot_resolver",
           "resolve_unnamed", "get_resolver", "RESOLVERS", "register_resolver", "find_bursts", "RTT_MARKER_RE"]
