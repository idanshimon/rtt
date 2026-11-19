import json
import subprocess
import sys
from pathlib import Path

import corrections
from meetalign.vocab import Vocabulary, mine

ROOT = Path(__file__).resolve().parents[1]
COMMON = {"it", "us", "am", "is", "the", "pr"}


def test_casing_and_plurals():
    v = Vocabulary(["SSP", "NWND", "IT", "MCP", "FHIR", "DevOps"], COMMON)
    assert v.normalize("my SSPS and Ssps") == "my SSPs and SSPs"
    assert v.normalize("at nwnd today, Nwnd's plan") == "at NWND today, NWND's plan"
    assert v.normalize("the MCPS and mcps") == "the MCPs and MCPs"
    assert v.normalize("it is fine, IT team") == "it is fine, IT team"     # ordinary word kept
    assert v.normalize("devops and DEVOPS") == "DevOps and DevOps"
    assert v.normalize("fhir-based, FHIR") == "fhir-based, FHIR"          # hyphenated word left alone
    assert v.normalize("SSPX") == "SSPX"                                  # not the term


def test_mine_finds_mixed_casing():
    lines = ["[10:00:00] Speaker 1: my SSPS and SSPS", "[10:00:01] Speaker 2: the SSP", "x: PRDS PRD PRD OK"]
    found = {d["term"]: d for d in mine(lines, known=["OK"], min_count=3)}
    assert found["SSP"]["forms"] == {"SSPS": 2, "SSP": 1} and found["PRD"]["count"] == 3
    assert "OK" not in found


def test_load_add_remove_and_apply(tmp_path, monkeypatch):
    shared, private = tmp_path / "vocabulary.json", tmp_path / "private" / "vocabulary.json"
    shared.write_text(json.dumps({"terms": ["SSP"]}))
    monkeypatch.setattr(corrections, "vocabulary_paths", lambda: [str(shared), str(private)])
    monkeypatch.setattr(corrections.os.path, "abspath", lambda p: str(tmp_path / "corrections.py"))
    monkeypatch.setenv("RTT_CORRECTIONS_FILE", str(tmp_path / "none.json"))
    assert corrections.add_terms(["NWND", "ssp"]) == ["NWND", "ssp"]     # private file is separate
    assert corrections.add_terms(["nwnd"]) == []                         # no duplicates (any casing)
    assert "NWND" in json.loads(private.read_text())["terms"]
    c = corrections.Corrections.load()
    assert c.apply("the SSPS at nwnd") == "the SSPs at NWND"
    assert set(corrections.load_vocabulary().phrases()) >= {"SSP", "NWND"}
    assert corrections.remove_terms(["nwnd"]) == ["NWND"]
    assert corrections.Corrections.load().apply("nwnd") == "nwnd"


def test_phrase_list_switch_and_llmspeech_definition(monkeypatch, tmp_path):
    f = tmp_path / "v.json"
    f.write_text(json.dumps({"terms": ["NWND", "SSP"]}))
    monkeypatch.setattr(corrections, "vocabulary_paths", lambda: [str(f)])
    from chunked_backends import LLMSpeechBackend
    d = LLMSpeechBackend("https://x", 16000, locales=["en-US"])._build_definition()
    assert d["phraseList"] == {"phrases": ["NWND", "SSP"]}
    monkeypatch.setenv("RTT_PHRASE_LIST", "0")
    assert "phraseList" not in LLMSpeechBackend("https://x", 16000)._build_definition()


def test_cli_vocab_mine(tmp_path):
    t = tmp_path / "t.txt"
    t.write_text("\n".join(["[10:00:00] Speaker 1: the ZQXS and ZQX and ZQXS"] * 2))
    p = subprocess.run([sys.executable, str(ROOT / "rtt_align.py"), "vocab", "mine", str(t), "--json"],
                       capture_output=True, text=True, cwd=ROOT,
                       env={"PATH": "/usr/bin:/bin", "RTT_VOCABULARY": "none"})
    found = json.loads(p.stdout)
    assert found[0]["term"] == "ZQX" and found[0]["count"] == 6


def test_mine_does_not_fold_real_words():
    lines = ["a: AWS AWS AWS AWS AWS AWS AW", "b: DevOps DevOps DevOps", "c: SSPS SSP SSP"]
    found = {d["term"]: d for d in mine(lines, known=["DevOps"], min_count=1)}
    assert "AWS" in found and found["AWS"]["forms"] == {"AWS": 6}       # not folded into "AW"
    assert "DevOps" not in found and "DevOp" not in found                # known term, not split
    assert found["SSP"]["forms"] == {"SSP": 2, "SSPS": 1}


def test_emit_fixes_every_backend_and_keeps_spoken_time(tmp_path, monkeypatch):
    f = tmp_path / "v.json"
    f.write_text(json.dumps({"terms": ["NWND", "SSP"]}))
    monkeypatch.setenv("RTT_VOCABULARY", str(f))
    import time
    from speakers import SpokenLine
    from transcribe import TranscriptBuffer, output_writer
    buf = TranscriptBuffer(tmp_path / "t.txt")
    emit = output_writer(buf, echo=lambda: False)
    emit("Speaker Guest-1: the SSPS at nwnd")                  # classic SDK style: plain str
    t = time.time() - 30
    emit(SpokenLine("Speaker 2: ssps again", 0.0, 1.0, "2", t))  # chunked: keeps spoken time
    emit("[⏸ RTT paused]")                                      # markers untouched
    lines = buf.all_lines()
    assert lines[0].endswith("Speaker Guest-1: the SSPs at NWND")
    assert lines[1].endswith("Speaker 2: SSPs again") and abs(buf._times[1] - t) < 1
    assert lines[2].endswith("[⏸ RTT paused]")
