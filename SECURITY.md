# Security and privacy

## What leaves your machine

| Backend | Audio goes to |
|---|---|
| `local` (Whisper) | Nowhere. Everything runs on your machine. |
| `openai`, `llmspeech`, `mai`, `azure` | The Azure resource **you** configure, in your own subscription. |
| `vibe` | A VibeVoice server on `127.0.0.1` that you run yourself. |
| Live summary (optional) | Transcript text goes to GitHub Copilot under your own `gh` sign-in. Turn it off with `--no-summary` or `RTT_SUMMARY=0`. |

Transcripts, recordings, summaries and the action log stay in local folders
(`transcripts/`, `sessions/`, `private/`) that are git-ignored. RTT never commits or uploads them.

## Recording consent

Recording or transcribing a conversation can require the consent of everyone in it, depending
on where you and the other people are. **You** are responsible for telling people and getting
consent. RTT has an "off the record" key (`o`) to delete recent lines, but that is not a
substitute for consent.

## Credentials

- Prefer Azure AD sign-in (`az login`) over API keys. RTT mints short-lived tokens and never
  writes them to disk.
- Keep keys only in `.env` (git-ignored). `rtt --show-config` prints settings without secrets.
- The control socket (`~/.rtt/run/rtt.sock`) is local to your user account.

## Reporting a vulnerability

Please open a [private security advisory](../../security/advisories/new) on this repository
instead of a public issue. Include steps to reproduce and the version (`git rev-parse HEAD`).
