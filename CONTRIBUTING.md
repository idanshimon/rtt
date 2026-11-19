# Contributing

Thanks for helping. Small, focused pull requests are easiest to review.

## Set up

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt -r requirements-dev.txt
.venv/bin/python -m pytest tests -q
```

Tests need no network, no Azure account and no audio device.

## House rules

- **One spec per feature.** Behavior is documented in `openspec/features/<feature>.md`, indexed
  in [openspec/README.md](openspec/README.md). Update the spec, the index, README and
  CHANGELOG in the same pull request as the code.
- **New `transcribe.py` flag?** Also add its `Field(...)` to `config_schema.py`, so `rtt-cli`
  (menu, wizard, chat) picks it up.
- **New backend?** Follow the three layers in the [architecture notes](docs/ARCHITECTURE.md):
  backend class, `process_<x>_backend` worker, `main()` dispatch.
- **Never commit meeting content.** No real audio, transcripts, summaries or names of real
  people or customers. Put them under the git-ignored `private/` folder. Test fixtures must
  be invented.
- **Auth must recover in place.** A token refresh must never restart the transcriber; a
  restart resets speaker labels mid-meeting.
