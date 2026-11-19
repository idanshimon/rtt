"""Render the split-view TUI with a synthetic meeting into docs/assets/readme/tui.svg.

Run: .venv/bin/python scripts/render_readme_screenshot.py
All names and lines are invented; nothing here comes from a real meeting.
"""
import sys
import tempfile
import time
from pathlib import Path

import pytest
from rich.console import Console

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.test_session import make_session  # noqa: E402
from transcribe import TranscriptBuffer  # noqa: E402
from tui import SplitView  # noqa: E402

LINES = [
    ("Maya", "Okay, let's start. Goal for today is the rollout plan for the new intake form."),
    ("Daniel", "We finished the pilot with two clinics. Average time per form dropped from nine minutes to four."),
    ("Maya", "Nice. Any problems with the FHIR export?"),
    ("Daniel", "One. Dates came through in local time, we fixed it on Tuesday."),
    ("Priya", "From the security side we still need the data flow diagram before go-live."),
    ("Maya", "Can you have that by Friday, Daniel?"),
    ("Daniel", "Yes, Friday works. I'll share it in the channel."),
    ("Priya", "Then I can sign off early next week."),
    ("Maya", "Great. Next topic: training for the front desk staff."),
]


W, H = 150, 22


class FakeSummary:
    """Stands in for the live summarizer so the screenshot needs no network."""
    model = "gpt-5-mini"
    sections = {
        "Summary": ["Pilot of the new intake form at two clinics cut time per form from 9 to 4 minutes.",
                    "FHIR export date bug (local time) was fixed on Tuesday."],
        "Decisions": ["Rollout waits for the security sign-off."],
        "Action items": ["Daniel: data flow diagram by Friday.", "Priya: security sign-off early next week."],
        "Open questions": ["Front desk training plan."],
    }

    def status(self):
        return {"error": None, "busy": False, "last_update": time.strftime("%H:%M:%S")}


def main() -> None:
    mp = pytest.MonkeyPatch()
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        s, _ = make_session(tmp, mp)
        buf = TranscriptBuffer(tmp / "t.txt")
        s.buffer_ref = buf
        start = time.time() - 60 * len(LINES)
        for i, (who, text) in enumerate(LINES):
            t = start + i * 41
            buf.add(f"[{time.strftime('%H:%M:%S', time.localtime(t))}] Speaker {who}: {text}")
            buf._times[-1] = t
        ui = SplitView(s, buf, FakeSummary())
        console = Console(record=True, force_terminal=True, color_system="truecolor", width=W, height=H)
        ui.console = console
        console.print(ui._render(), height=H)
        out = ROOT / "docs/assets/readme/tui.svg"
        console.save_svg(str(out), title="rtt — live meeting transcript")
        print(out)
    mp.undo()


if __name__ == "__main__":
    main()
