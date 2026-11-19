# Garden file-transcription demo

From the repository root, after installing RTT's dependencies:

```sh
.venv/bin/python demo/run.py
```

RTT transcribes `garden.wav` with the local Whisper `base` model. A first run may download the model. For strictly offline use, cache the model beforehand and run `HF_HUB_OFFLINE=1 .venv/bin/python demo/run.py`. No Azure key, microphone, eSpeak installation or summary service is needed to run the demo.

Each invocation prints a new temporary directory containing `transcript.txt` and the session metadata. Read that transcript directly; do not use `rtt -c` or other live-session controls to find it. You can delete the printed demo directory when finished. The launcher bypasses `.env`, ignores inherited `RTT_*` settings, uses its own control socket, and disables summaries only for its child process.

## Sample provenance and regeneration

`garden.txt` is original narration written for this demo. `garden.wav` is the output of the built-in US English formant voice in [eSpeak NG](https://github.com/espeak-ng/espeak-ng) 1.52.0, converted to mono 16 kHz signed 16-bit PCM. There is no microphone, cloned voice, meeting recording, or third-party spoken material in the sample. The text and rendered audio are redistributable under the repository's [MIT license](../LICENSE); neither the synthesizer nor its voice data is bundled.

To regenerate from the repository root (requires `espeak-ng` and `ffmpeg`, e.g. `brew install espeak-ng ffmpeg`):

```sh
espeak-ng -v en-us -s 125 -f demo/garden.txt --stdout | \
  ffmpeg -hide_banner -loglevel error -i pipe:0 -ar 16000 -ac 1 -c:a pcm_s16le \
    -map_metadata -1 -fflags +bitexact -y demo/garden.wav
```

The narration is a reference, not a canned transcript. Compare the printed transcript with `garden.txt`; for example, listen for “three rows of carrots beside the apple tree.” Exact wording and punctuation can vary.
