"""What languages the current backend + settings will transcribe, for the status bar.

Values are based on what RTT configures and what was measured (speech-bench, 2026-11):
  vibe       Hebrew 4-speaker podcast: best result; English meetings good; English terms inside
             Hebrew speech kept (mixed works).
  openai     Hebrew podcast transcribed well with English terms inline; English is its main use.
  llmspeech  multilingual auto-detect; rttheb passes en-US,he-IL (mixed Hebrew/English).
  azure      language list with continuous language ID (rtthebold: en-US,he-IL) switches per
             segment; one locale otherwise.
  mai        follows --mai-locales; without them auto-detect (Hebrew not measured).
  local      Whisper: --language or auto-detect.
"""
from __future__ import annotations

from typing import Dict, Optional


def _codes(csv: Optional[str]) -> list:
    return [c.strip().split("-")[0].lower() for c in (csv or "").split(",") if c.strip()]


def lang_profile(backend: str, cfg: Dict[str, Optional[str]]) -> dict:
    """Return {"he": bool|None, "en": bool|None, "mix": bool|None, "label": str, "detail": str}.

    True = configured and works, False = will not transcribe it, None = unknown / not measured.
    """
    lang = (cfg.get("language") or "").split("-")[0].lower() or None
    he = en = mix = None
    detail = ""
    if backend == "vibe":
        he, en, mix = True, True, True
        detail = "auto-detect; Hebrew measured best of local options"
    elif backend == "openai":
        if lang:
            he, en, mix = lang == "he", lang == "en", False
            detail = f"forced --language {lang}"
        else:
            he, en, mix = True, True, True
            detail = "auto-detect; Hebrew measured on a podcast"
    elif backend == "llmspeech":
        codes = _codes(cfg.get("llmspeech_locales"))
        if codes:
            he, en = "he" in codes, "en" in codes
            mix = he and en
            detail = "locales " + ",".join(codes)
        else:
            he, en, mix = True, True, True
            detail = "multilingual auto-detect"
    elif backend == "mai":
        codes = _codes(cfg.get("mai_locales"))
        if codes:
            he, en = "he" in codes, "en" in codes
            mix = he and en
            detail = "locales " + ",".join(codes)
        else:
            he, en, mix = None, True, None
            detail = "auto-detect; Hebrew not measured"
    elif backend == "azure":
        codes = _codes(cfg.get("azure_languages")) or ([lang] if lang else ["en"])
        he, en = "he" in codes, "en" in codes
        mix = he and en and len(codes) > 1
        detail = "languages " + ",".join(codes) + (" (switches per segment)" if mix else "")
    elif backend == "local":
        if lang:
            he, en, mix = lang == "he", lang == "en", False
            detail = f"--language {lang}"
        else:
            he, en, mix = None, True, None
            detail = "Whisper auto-detect; Hebrew not measured"
    parts = []
    for code, ok in (("he", he), ("en", en)):
        if ok:
            parts.append(code)
    if mix and he and en:
        label = "he+en"
    elif parts:
        label = " ".join(parts) + (" only" if len(parts) == 1 and (he is False or en is False) else "")
    else:
        label = "lang ?"
    if he is None:
        label += " (he ?)"
    return {"he": he, "en": en, "mix": mix, "label": label, "detail": detail}
