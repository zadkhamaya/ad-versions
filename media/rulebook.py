"""rulebook.py — the brand rulebook (brand/rulebook.yaml) and the hard word check."""
import re
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Rulebook:
    brand: str
    disclaimer: str
    claims: tuple
    shots: tuple
    banned_words: tuple
    banned_media: tuple
    look_fallback: str = ""
    tone: str = ""


def load(path: Path) -> Rulebook:
    d = yaml.safe_load(Path(path).read_text())
    return Rulebook(d["brand"], d["disclaimer"], tuple(d["claims"]), tuple(d["shots"]),
                    tuple(str(w).lower() for w in d["banned_words"]), tuple(d["banned_media"]),
                    d.get("look_fallback", ""), d.get("tone", ""))


def _pattern(phrase: str) -> re.Pattern:
    """A banned phrase as a regex: its words joined by any run of spaces, line breaks or hyphens ("doctor\\nrecommended",
    "clinically-proven"); whole words only, the word boundary applied on a side only where the phrase has a word character
    ("$" must hit "$49", "100%" must not hit "1100%")."""
    body = r"[\s-]+".join(re.escape(w) for w in re.split(r"[\s-]+", phrase) if w)
    return re.compile((r"(?<!\w)" if re.match(r"\w", phrase) else "") + body + (r"(?!\w)" if re.search(r"\w$", phrase) else ""))


def hard_hits(text: str, rb: Rulebook) -> list[str]:
    """Banned phrases found in text, lowercase, sorted; whole words only (a phrase may start or end in a symbol, e.g. 100%, $)."""
    low = text.lower()
    return sorted(w for w in rb.banned_words if _pattern(w).search(low))
