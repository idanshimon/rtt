# Meeting awareness

Status: shipped 2026-11-06.

## Why
Start RTT when a call starts without remembering to, and name the session after the meeting.

## Behavior
- Mic in use: `mic_activity.mic_users()` lists processes capturing the microphone (CoreAudio
  process objects via ctypes; no permissions needed).
- Calendar title: in live mode without `--session-name` (`RTT_CALENDAR=workiq`), once a Teams,
  Zoom or Webex process uses the mic (checked every 15 s), `workiq ask` lists today's meetings as
  `HH:MM-HH:MM | title`; the meeting in progress (from 5 min before start) or starting within 10 min
  is chosen. Title shows in the status bar, goes to session.json, and the folder gets the name at
  close. A scheduled meeting you are not in never names the session.
- Meeting watcher (`meeting_watch.py`, LaunchAgent via `install-meeting-watch.sh`; remove with
  `install-meeting-watch.sh remove`): a meeting app on the mic for `RTT_WATCH_START_AFTER` s (10)
  with no RTT running → dialog "Start RTT?" (opens Terminal running `RTT_WATCH_CMD`). Declining
  silences it for that call. When the app leaves the mic for `RTT_WATCH_STOP_AFTER` s (30) and the
  watcher started RTT → "Stop RTT?" (default Stop after 30 s). Never stops an RTT you started.
  Log `~/.rtt/meeting-watch.log`.

## Implementation
mic_activity.py; session.py `meeting_app_on_mic`, `title_when_in_call`, `pick_current_meeting`,
`lookup_meeting_title`; meeting_watch.py `Watcher` (state machine with injected side effects).

## Verification
mic_activity listed the running RTT's python process as mic user. Real workiq lookup returned
today's meetings (≈20 s). tests: title only once in a call, meeting picking (current, upcoming,
all-day skipped, citation links stripped), watcher state machine (prompt once, stop only own,
ignore non-meeting apps).

## Limits
Watcher not yet tested in a real call; the LaunchAgent must be installed from a normal terminal.
Browser-based meetings are not detected (the mic user is the browser).
