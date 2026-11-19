# Speaker tracking, click-to-rename, spoken time

Status: shipped 2026-11-06. Extends speaker-rename-hotkey.

## Why
Chunked backends diarize each chunk alone: "Speaker A" / "Speaker 0" only means "first voice in
this chunk". Renaming A → Idan therefore renamed a different person in every chunk. Also, every
line in a chunk got the time the chunk came back, so many lines shared one timestamp.

## Behavior
- Stable ids: lines read `Speaker 1`, `Speaker 2`, … and each number is the same voice for the whole
  session, across chunks and backend switches.
- `Speaker ?`: a voice with under 1 s of audio in its chunk could not be identified; it is not
  guessed. Renaming "?" is refused with a message.
- Rename (click a line, `r`, Ctrl+R, `rtt-ctl rename <from> <to>`): applies to every past and future
  line with that id; renaming a name again (Idan → Idan Shimon) re-points the original id.
- Renames reach the summary: existing bullets are rewritten ("Speaker 2 said" → "Idan said"); the
  rename is re-applied after every later model update so a call already in flight cannot bring the
  old label back. Raw labels only match as "Speaker X" (a label "A" never touches "Series A");
  bare-word replacement is used only when renaming an earlier display name.
- Spoken time: each line's timestamp is when it was said (chunk start + segment offset).
- If tracking is off, lines keep chunk-local labels and RTT says so at start.

## Interfaces
`RTT_SPEAKER_TRACKING` (1), `RTT_SPEAKER_MODEL`, `RTT_SPEAKER_THRESHOLD` (0.45).
`scripts/get-speaker-model.sh` downloads models/nemo_en_titanet_small.onnx (40 MB, gitignored).
Dependency: sherpa-onnx (requirements.txt). `rtt-ctl status` → `speakers`
{speakers, chunks, matched, new, short, threshold}.

## Implementation
- `speakers.SpokenLine`: str subclass carrying `start`, `end` (seconds in chunk), `label`
  (chunk-local), `t` (epoch spoken). Every consumer that treats it as a str keeps working.
- Formatters keep segment times: openai `segments[].start/end`, vibe `Start/End`, llmspeech/mai
  `offsetMilliseconds/durationMilliseconds`.
- `ChunkedHTTPBackend` tracks the wall-clock time of the first buffered sample
  (`push_audio(captured_at)`), advances it on every cut, passes the chunk to
  `SpeakerTracker.relabel`, and stamps `t`. `emit` in transcribe.py uses `t` (sanity window: last
  hour) for the printed time and for `TranscriptBuffer._times` (off the record cuts by spoken time).
- `SpeakerTracker`: per local label, concatenate its longest turns (≤12 s, needs ≥1 s), embed with
  NeMo TitaNet-small (ONNX via sherpa-onnx, CPU, ~80–190 ms per voice), greedy one-to-one match to
  session centroids by cosine ≥ threshold, else a new id; centroid = running mean (recent-weighted
  after 20). Single-voice chunks without times use the whole chunk.
- Session.rename_speaker → TranscriptBuffer.rename_speaker (map), LiveSummarizer.apply_rename,
  ledger entry with the previous map for undo.

## Verification
- Model choice (same/different voice on Idan's recordings + podcast + English meeting voices):
  TitaNet-small balanced accuracy 0.973 raw cosine (threshold 0.42), 0.988 mean-centred;
  ResNet34 0.85, CAM++ 0.70, ERes2Net ≈ TitaNet but 4× slower.
- Simulation (chunk-local labels derived from VibeVoice full-file diarization): label purity
  Hebrew 4-speaker podcast 0.48 → 0.995 (10 s chunks), 0.52 → 0.997 (30 s); English meeting
  0.80 → 0.996.
- tests/test_speakers.py: same voice keeps its id when local labels swap between chunks; short
  voice → "?"; formatters keep times; lines get spoken time. tests/test_ledger.py: rename in
  summary, "Series A" untouched, in-flight update, undo.
- Real run (openai backend, Hebrew podcast at speaking speed): one long monologue kept one id
  across all chunks; timestamps spread per segment.

## Limits
Over-splitting: short or noisy fragments sometimes open an extra id (10 ids for 4 people over
10 minutes at 10 s chunks); the main voices stay correct. Not yet: merging ids that converge,
OpenAI `known_speaker_references` (would let the model itself name enrolled voices). vibe backend
uses the same code path but was not run live after this change.
