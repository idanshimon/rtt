# Domain vocabulary (NWND, SSPs, FHIR…)

Status: shipped 2026-11-10.

## Why
Across 397 real transcripts the model mostly hears the terms but writes them inconsistently:
"SSPS" 56 times for SSPs, "MCPS" 58, "PRDS" 27, "LLMS" 22, "SDKS" 8; plus rarer mishearings
("NWIND"/"N-WAND" for NWND; Teams itself wrote "NWIND" once). One list of right spellings should fix casing
everywhere and, where a backend allows it, steer recognition before it happens.

## Behavior
- Vocabulary = `vocabulary.json` (shared, public terms: SSP, MCP, FHIR, ACR, MCAPS, HITRUST…) +
  `private/vocabulary.json` (customer and people names, e.g. NWND; not in git) + `RTT_VOCABULARY`
  extra paths (`none` disables).
- Casing on every backend (emit choke point + chunked corrections): "SSPS"/"Ssps"/"ssps" → SSPs,
  "nwnd"/"Nwnd's" → NWND/NWND's, "devops" → DevOps. Never changes: an all-lowercase ordinary English
  word (/usr/share/dict/words; "it" stays "it" even with IT in the list), hyphenated words, longer
  tokens ("SSPX"), markers. Idempotent. Spoken time of a line is kept.
- Before transcription (phrase list, `RTT_PHRASE_LIST=0` to turn off):
  `rttheb` (LLM Speech) → `definition.phraseList.phrases`; `rttold`/`rtthebold` (Speech SDK) →
  `PhraseListGrammar` on the recognizer, re-applied on auth restart; `rttvibe` → "Terms: …" hotword
  prompt when no prompt is set. `rtt` (gpt-4o-transcribe-diarize) has no supported prompt field for
  this model, so it gets casing only; MAI is not given a phrase list (not verified for that model).
- `rtt-align vocab list | add TERM… [--shared] | remove TERM… | mine [FILES…] [--add N] [--min] [--top] [--json]`.
  `mine` lists acronym-like terms from transcripts (default all of transcripts/) that are not in the
  vocabulary, with every spelling seen; plurals fold into a singular only if that singular is in the
  vocabulary or at least a third as common (AWS is not folded into AW; DevOps not split).
  Added terms go to the private file unless `--shared`.

## Implementation
`meetalign/vocab.py` (Vocabulary.normalize / phrases, mine) — pure; `corrections.py`
(vocabulary_paths, load_vocabulary, common_words, add_terms, remove_terms, phrase_list;
Corrections.apply runs the vocabulary after rules; a missing corrections.json no longer disables
learned words/vocabulary); `chunked_backends.LLMSpeechBackend._build_definition`,
`VibeVoiceLocalBackend.__init__`; `transcribe.AzureSpeechBackend._apply_phrase_list`,
`transcribe.output_writer`; `rtt_align.cmd_vocab`.

## Verification
tests/test_vocab.py: casing/plurals/possessive, ordinary words and hyphenated words kept; mining with
mixed casing and no false folds (AWS/AW, DevOps); add/remove/no duplicates, load + apply, phrase list
in the LLM Speech definition and the off switch; CLI `vocab mine --json`; emit fixes a plain-str
(Speech SDK) line and a chunked SpokenLine keeping its spoken time. Real run: `rttheb` settings
(`en-US,he-IL`) on a real 15-min customer meeting recording with 65 phrases sent: accepted, no errors, same
time (28 s vs 27 s); text differences between the two runs were normal run-to-run variation —
accuracy gain from the phrase list is NOT measured (the clip mentions the customer name once in both).

## Limits
Casing only fixes terms the model already heard; true mishearings need a learned rule (`T` → `L`)
or a phrase list on a backend that supports it. The phrase list's effect on accuracy is unmeasured.
