"""Stable speaker identities across chunks.

Chunked backends diarize each chunk on its own: "Speaker A" / "Speaker 0" just means "first voice
in this chunk". SpeakerTracker turns those chunk-local labels into session-wide speakers
("Speaker 1", "Speaker 2", …) by voice: it embeds each local speaker's audio in the chunk with a
small ONNX speaker model (NeMo TitaNet-small via sherpa-onnx, ~80 ms per segment on CPU) and
matches it to known voices. Because labels are now global, renaming "Speaker 2" → "Idan" is
correct for every line, past and future.

If sherpa-onnx or the model file is missing, tracking is off and lines keep chunk-local labels
(the old behaviour). RTT_SPEAKER_TRACKING=0 turns it off explicitly.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

MODEL_DEFAULT = Path(__file__).resolve().parent / "models" / "nemo_en_titanet_small.onnx"
MODEL_URL = ("https://github.com/k2-fsa/sherpa-onnx/releases/download/"
             "speaker-recongition-models/nemo_en_titanet_small.onnx")


class SpokenLine(str):
    """A transcript line that remembers where it came from in its chunk.

    Subclass of str so every existing consumer keeps working; metadata rides along:
      start/end  seconds from the start of the chunk (None if the backend gives no times)
      label      the backend's chunk-local speaker label ("A", "0", …)
      t          wall-clock epoch when the line was spoken (filled by the backend base class)
    """

    start: Optional[float]
    end: Optional[float]
    label: Optional[str]
    t: Optional[float]

    def __new__(cls, text: str, start: Optional[float] = None, end: Optional[float] = None,
                label: Optional[str] = None, t: Optional[float] = None) -> "SpokenLine":
        obj = super().__new__(cls, text)
        obj.start, obj.end, obj.label, obj.t = start, end, label, t
        return obj

    def with_text(self, text: str) -> "SpokenLine":
        return SpokenLine(text, self.start, self.end, self.label, self.t)


def relabel_text(text: str, label: str, new_label: str) -> str:
    prefix = f"Speaker {label}:"
    if text.startswith(prefix):
        return f"Speaker {new_label}:" + text[len(prefix):]
    return text


class SpeakerTracker:
    MIN_SECONDS = 1.0       # less audio than this for a local speaker → no reliable embedding
    MAX_SECONDS = 12.0      # cap per local speaker per chunk (enough for a stable embedding)
    THRESHOLD = 0.45        # cosine vs a known voice's centroid (TitaNet-small, measured 0.97 bal.acc @0.42)

    def __init__(self, model_path: Optional[Path] = None, threshold: Optional[float] = None) -> None:
        self.model_path = Path(model_path or os.environ.get("RTT_SPEAKER_MODEL", MODEL_DEFAULT))
        self.threshold = float(threshold if threshold is not None else os.environ.get("RTT_SPEAKER_THRESHOLD", self.THRESHOLD))
        self._ex = None
        self._lock = threading.Lock()
        self.centroids: List[np.ndarray] = []   # index i → global speaker i+1
        self.counts: List[int] = []
        self.error: Optional[str] = None
        self.stats = {"chunks": 0, "matched": 0, "new": 0, "short": 0}
        # Expected number of people. When set, a voice that matches no one joins the
        # closest known voice instead of becoming person N+1 (noisy calls otherwise
        # split one person into many).
        self.max_speakers: Optional[int] = None
        self.allowed: Optional[List[int]] = None   # voices still matchable once the cap is reached

    def set_max_speakers(self, n: Optional[int]) -> None:
        """Cap the number of people. If more voices exist already, only the n most-heard stay
        matchable (the rest were almost always the same people split by noise)."""
        with self._lock:
            self.max_speakers = n or None
            if self.max_speakers and len(self.centroids) >= self.max_speakers:
                order = sorted(range(len(self.centroids)), key=lambda g: self.counts[g], reverse=True)
                self.allowed = sorted(order[:self.max_speakers])
            else:
                self.allowed = None

    def _candidates(self) -> List[int]:
        if self.allowed is not None:
            return list(self.allowed)
        return list(range(len(self.centroids)))

    # ---- model --------------------------------------------------------------------
    @classmethod
    def create(cls) -> Optional["SpeakerTracker"]:
        if os.environ.get("RTT_SPEAKER_TRACKING", "1").lower() in ("0", "false", "no", "off"):
            return None
        t = cls()
        return t if t.load() else None

    def load(self) -> bool:
        try:
            import sherpa_onnx
        except ImportError:
            self.error = "sherpa-onnx not installed (pip install sherpa-onnx)"
            return False
        if not self.model_path.exists():
            self.error = f"speaker model missing: {self.model_path} (run scripts/get-speaker-model.sh)"
            return False
        cfg = sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(self.model_path), num_threads=2)
        self._ex = sherpa_onnx.SpeakerEmbeddingExtractor(cfg)
        return True

    def embed(self, audio: np.ndarray, sample_rate: int) -> np.ndarray:
        s = self._ex.create_stream()
        s.accept_waveform(sample_rate, np.ascontiguousarray(audio, dtype=np.float32))
        s.input_finished()
        v = np.asarray(self._ex.compute(s), dtype=np.float32)
        return v / (np.linalg.norm(v) or 1.0)

    # ---- matching -----------------------------------------------------------------
    def _assign(self, embs: Dict[str, np.ndarray]) -> Dict[str, int]:
        """Greedy one-to-one match of this chunk's voices to known voices; unknown → new speaker."""
        pairs = []
        for lab, e in embs.items():
            for gi in self._candidates():
                c = self.centroids[gi]
                pairs.append((float(e @ (c / np.linalg.norm(c))), lab, gi))
        pairs.sort(reverse=True)
        out: Dict[str, int] = {}
        used = set()
        for sim, lab, gi in pairs:
            if sim < self.threshold:
                break
            if lab in out or gi in used:
                continue
            out[lab] = gi
            used.add(gi)
        for lab, e in embs.items():
            if lab in out:
                self.stats["matched"] += 1
                gi = out[lab]
                n = min(self.counts[gi], 20)        # running mean, recent-weighted after 20
                self.centroids[gi] = (self.centroids[gi] * n + e) / (n + 1)
                self.counts[gi] += 1
            elif self.max_speakers and len(self._candidates()) >= self.max_speakers:
                self.stats["forced"] = self.stats.get("forced", 0) + 1
                cand = self._candidates()
                free = [gi for gi in cand if gi not in used] or cand
                gi = max(free, key=lambda g: float(e @ (self.centroids[g] / np.linalg.norm(self.centroids[g]))))
                out[lab] = gi
                used.add(gi)
            else:
                self.stats["new"] += 1
                self.centroids.append(e.copy())
                self.counts.append(1)
                out[lab] = len(self.centroids) - 1
                if self.allowed is not None:
                    self.allowed.append(out[lab])
        return out

    def relabel(self, chunk: np.ndarray, sample_rate: int, lines: Sequence[str]) -> List[str]:
        """Replace chunk-local speaker labels with session-wide ones ("Speaker 1", "Speaker 2"…)."""
        spans: Dict[str, List[tuple]] = {}
        for ln in lines:
            lab = getattr(ln, "label", None)
            st, en = getattr(ln, "start", None), getattr(ln, "end", None)
            if lab is None:
                continue
            if st is None or en is None:
                if len({getattr(x, "label", None) for x in lines} - {None}) == 1:
                    st, en = 0.0, chunk.shape[0] / sample_rate     # single voice: whole chunk is theirs
                else:
                    continue
            spans.setdefault(lab, []).append((st, en))
        if not spans:
            return list(lines)
        with self._lock:
            self.stats["chunks"] += 1
            embs: Dict[str, np.ndarray] = {}
            for lab, sp in spans.items():
                parts, total = [], 0.0
                for st, en in sorted(sp, key=lambda x: x[1] - x[0], reverse=True):  # longest turns first
                    a = chunk[int(max(0, st) * sample_rate):int(min(en, chunk.shape[0] / sample_rate) * sample_rate)]
                    if a.size == 0:
                        continue
                    parts.append(a)
                    total += a.shape[0] / sample_rate
                    if total >= self.MAX_SECONDS:
                        break
                if total >= self.MIN_SECONDS:
                    embs[lab] = self.embed(np.concatenate(parts), sample_rate)
                else:
                    self.stats["short"] += 1
            mapping = self._assign(embs) if embs else {}
            # A voice too short to embed: if it is the only unknown left, use the last speaker
            # that was matched in this chunk with that label… otherwise keep it marked uncertain.
            out: List[str] = []
            for ln in lines:
                lab = getattr(ln, "label", None)
                if lab is None:
                    out.append(ln)
                    continue
                if lab in mapping:
                    new = str(mapping[lab] + 1)
                else:
                    new = "?"
                text = relabel_text(str(ln), lab, new)
                out.append(ln.with_text(text) if isinstance(ln, SpokenLine) else text)
            return out

    def reset(self) -> None:
        """Forget all voices (new session, new people)."""
        with self._lock:
            self.centroids, self.counts = [], []
            self.allowed = None
            self.stats = {"chunks": 0, "matched": 0, "new": 0, "short": 0}

    def snapshot(self) -> dict:
        return {"speakers": len(self.centroids), **self.stats, "threshold": self.threshold,
                "max_speakers": self.max_speakers}
