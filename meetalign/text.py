"""Word helpers shared by matching and refinement."""
import re
from typing import List, Tuple

WORD_RE = re.compile(r"[a-z0-9']+")
TOKEN_NORM = re.compile(r"[^a-z0-9']+")
FILLER = {"yeah", "yes", "ok", "okay", "um", "uh", "so", "and", "the", "a", "i", "to", "it", "is",
          "of", "that", "right", "you", "we", "but", "like", "know", "mean", "just", "oh", "well"}
COMMON = FILLER | {"have", "has", "had", "was", "were", "are", "be", "been", "for", "on", "in", "at",
                   "with", "this", "there", "they", "them", "then", "than", "what", "when", "who", "how",
                   "not", "no", "do", "does", "did", "can", "could", "would", "should", "will", "if",
                   "or", "as", "from", "by", "an", "my", "our", "your", "his", "her", "its", "me", "us",
                   "he", "she", "all", "any", "some", "one", "two", "get", "got", "go", "going", "think",
                   "said", "say", "see", "want", "need", "out", "up", "about", "into", "more", "very"}


def words(text: str) -> List[str]:
    return WORD_RE.findall(text.lower().replace("\u2019", "'"))


def toks(text: str) -> List[Tuple[str, str]]:
    """[(original token, normalized)]. Tokens the app redacted ("****") normalize to "*"."""
    out = []
    for tok in text.split():
        core = tok.strip(".,?!;:")
        if core and set(core) <= {"*"}:
            out.append((tok, "*"))
            continue
        n = TOKEN_NORM.sub("", tok.lower().replace("\u2019", "'"))
        if n:
            out.append((tok, n))
    return out
