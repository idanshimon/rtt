#!/usr/bin/env python3
"""Watch for Teams/Zoom/Webex calls and offer to start/stop RTT.

Detection: a meeting app is capturing the microphone (CoreAudio process objects,
mic_activity.py). No screen/accessibility permission needed.

  call starts (mic in use ≥ START_AFTER s, RTT not running) → dialog "Start RTT?"
      Start → opens a Terminal window running RTT_WATCH_CMD (default run-openai.sh)
      Not now / no answer → no more prompts until this call ends
  call ends (mic released ≥ STOP_AFTER s) and RTT was started by this watcher
      → dialog "Stop RTT?" (default Stop after 30 s) → rtt-ctl stop

Run:  meeting_watch.py            (foreground)
      meeting_watch.py --dry-run  (print decisions, no dialogs/launch)
Installed as a LaunchAgent by install-meeting-watch.sh.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Iterable, List, Optional, Tuple

REPO = Path(__file__).resolve().parent
MEETING_APPS = tuple(os.environ.get(
    "RTT_WATCH_APPS", "com.microsoft.teams2,com.microsoft.teams,us.zoom.xos,com.cisco.webexmeetingsapp"
).split(","))
START_AFTER = float(os.environ.get("RTT_WATCH_START_AFTER", "10"))
STOP_AFTER = float(os.environ.get("RTT_WATCH_STOP_AFTER", "30"))
POLL = 3.0


def rtt_running() -> bool:
    sys.path.insert(0, str(REPO))
    from session import send_command
    try:
        return bool(send_command({"cmd": "status"}, timeout=1).get("ok"))
    except Exception:
        return False


def dialog(text: str, buttons: Tuple[str, str], default: str, give_up: int) -> Optional[str]:
    script = (f'display dialog "{text}" with title "RTT" buttons {{"{buttons[0]}", "{buttons[1]}"}} '
              f'default button "{default}" giving up after {give_up}')
    out = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if "gave up:true" in out.stdout:
        return None
    for b in buttons:
        if f"button returned:{b}" in out.stdout:
            return b
    return None


def launch_rtt() -> None:
    cmd = os.environ.get("RTT_WATCH_CMD", str(REPO / "run-openai.sh"))
    subprocess.run(["osascript", "-e", f'tell application "Terminal" to do script "{cmd}"',
                    "-e", 'tell application "Terminal" to activate'], capture_output=True)


def stop_rtt() -> None:
    subprocess.run([str(REPO / "rtt-ctl.sh"), "stop"], capture_output=True)


class Watcher:
    """State machine; all side effects injected so it can be tested without a call."""

    def __init__(self, mic_users: Callable[[], Iterable[Tuple[int, str]]], rtt_running: Callable[[], bool],
                 ask_start: Callable[[str], bool], ask_stop: Callable[[], bool],
                 start: Callable[[], None], stop: Callable[[], None], clock: Callable[[], float] = time.time,
                 log: Callable[[str], None] = print) -> None:
        self.mic_users, self.rtt_running = mic_users, rtt_running
        self.ask_start, self.ask_stop, self.start, self.stop = ask_start, ask_stop, start, stop
        self.clock, self.log = clock, log
        self.in_call_since: Optional[float] = None
        self.idle_since: Optional[float] = None
        self.declined = False
        self.started_by_us = False

    def meeting_app(self) -> Optional[str]:
        for _, bundle in self.mic_users():
            if bundle in MEETING_APPS:
                return bundle
        return None

    def step(self) -> None:
        now = self.clock()
        app = self.meeting_app()
        if app:
            self.idle_since = None
            if self.in_call_since is None:
                self.in_call_since = now
                self.log(f"call detected ({app})")
            if (not self.declined and not self.started_by_us and now - self.in_call_since >= START_AFTER
                    and not self.rtt_running()):
                if self.ask_start(app):
                    self.start()
                    self.started_by_us = True
                    self.log("RTT started")
                else:
                    self.declined = True
                    self.log("declined for this call")
            return
        if self.in_call_since is None:
            return
        if self.idle_since is None:
            self.idle_since = now
        if now - self.idle_since < STOP_AFTER:
            return
        self.log("call ended")
        if self.started_by_us and self.rtt_running() and self.ask_stop():
            self.stop()
            self.log("RTT stopped")
        self.in_call_since = self.idle_since = None
        self.declined = self.started_by_us = False


def main() -> None:
    from mic_activity import mic_users
    dry = "--dry-run" in sys.argv
    pretty = {"com.microsoft.teams2": "Teams", "com.microsoft.teams": "Teams", "us.zoom.xos": "Zoom",
              "com.cisco.webexmeetingsapp": "Webex"}
    w = Watcher(
        mic_users=mic_users, rtt_running=rtt_running,
        ask_start=(lambda app: print(f"[dry] would ask to start for {app}") or False) if dry else
        (lambda app: dialog(f"{pretty.get(app, app)} call detected. Start RTT transcription?",
                            ("Not now", "Start RTT"), "Start RTT", 60) == "Start RTT"),
        ask_stop=(lambda: print("[dry] would ask to stop") or False) if dry else
        (lambda: dialog("The call ended. Stop RTT?", ("Keep running", "Stop RTT"), "Stop RTT", 30) in ("Stop RTT", None)),
        start=launch_rtt, stop=stop_rtt,
        log=lambda m: print(time.strftime("%H:%M:%S"), m, flush=True),
    )
    while True:
        try:
            w.step()
        except Exception as exc:  # keep watching
            print("watch error:", exc, flush=True)
        time.sleep(POLL)


if __name__ == "__main__":
    main()
