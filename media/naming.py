"""naming.py — version names by the convention in config/naming.yaml, and the 18-version matrix."""
import re

FORMATS = {"9X16": (1080, 1920), "1X1": (1080, 1080), "16X9": (1920, 1080)}
LENGTHS = (6, 15, 30)
HOOKS = ("H1", "H2")
_CLEAN = re.compile(r"[^A-Z0-9]")


def _part(s: str) -> str:
    out = _CLEAN.sub("", s.upper())
    if not out:
        raise ValueError(f"empty name part: {s!r}")
    return out


def version_name(brand, campaign, hook, length_s, fmt, lang="EN", v=1) -> str:
    if hook not in HOOKS or length_s not in LENGTHS or fmt not in FORMATS or not 1 <= v <= 99:
        raise ValueError(f"bad version {hook} {length_s} {fmt} v{v}")
    return f"{_part(brand)}_{_part(campaign)}_{hook}_{length_s}S_{fmt}_{_part(lang)}_V{v:02d}.mp4"


def version_id(hook, length_s, fmt) -> str:
    """The short id the service and n8n use, e.g. H1_15S_9X16."""
    return f"{hook}_{length_s}S_{fmt}"


def parse_version(vid: str) -> tuple[str, int, str]:
    """H1_15S_9X16 -> ("H1", 15, "9X16"); ValueError for anything outside the matrix."""
    m = re.fullmatch(r"(H\d)_(\d+)S_(\w+)", str(vid))
    if not m or (m[1], int(m[2]), m[3]) not in matrix():
        raise ValueError(f"version {vid!r}: not one of the 18 (e.g. H1_15S_9X16)")
    return m[1], int(m[2]), m[3]


def matrix():
    return [(h, l, f) for h in HOOKS for l in LENGTHS for f in FORMATS]
