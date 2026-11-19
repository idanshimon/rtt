# Audio recording and second pass

Status: recording 2026-11-05; WAV-while-recording, "me" attribution 2026-11-06.

## Why
Live chunks trade accuracy for latency; a recording allows a better transcript afterwards and a
check of what was actually said.

## Behavior
- Off by default (`--record`, `RTT_RECORD=1`) because customer meetings may forbid recording;
  `● REC` in the status bar when on. Ignored in file mode.
- Tracks: with mic mixing `others` (meeting audio) and `mic` (you); single device `audio`.
- `mic_hq` (with mic mixing): the same mic at 48 kHz for voice training. The mic opens once at 48 kHz;
  the raw audio is recorded and a stateful low-pass + 3:1 decimation (`_Decimator`) feeds the 16 kHz
  mixer, so transcription input is unchanged. Never open the mic twice: Core Audio starves the first
  stream (measured 7 of 40 blocks). If 48 kHz fails, RTT falls back to 16 kHz and records `mic` only.
  `AudioRecorder.TRACK_RATES` holds per-track rates; resume lengths use each file's own rate.
  Nothing is recorded while paused; the mic track is silent while muted.
- Written as WAV while running (so off-the-record can cut the tail), converted to FLAC 16 kHz mono
  at close.
- `rtt-post [session_dir]` (latest session by default): mixes tracks, re-transcribes with local
  VibeVoice (starts the server if needed) in pieces of `RTT_POST_PIECE_SECONDS` (50 min) →
  `transcript.final.txt`, metadata into session.json.
- "Me": per segment, if the mic track is ≥2× louder than the meeting track, the speaker becomes
  `Speaker <RTT_ME_NAME>` (default Me).

## Implementation
`session.AudioRecorder`: bounded queue off the audio thread, drop counter, `enabled` flag,
`redact_since(wall)` / `redact_last(seconds)` cut by capture time via `audio_index.json`; a crash's
leftover WAV is folded into the FLAC at the next start; a resumed session appends to the FLAC.
`rtt_post.py`: `load_tracks` (FLAC or WAV), `me_ratio`.

## Verification
Real PTY run with `--record`: both tracks written. rtt-post on a session with a Hebrew meeting
track and a separate recording of Idan: the Idan part labelled `Speaker Idan`, the rest
diarized normally. tests: recorder tracks and pause, `me_ratio`.

## Limits
Not yet verified on a real meeting with BlackHole routing or a multi-hour recording.
