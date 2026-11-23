"""timeline.py — which source and b-roll seconds go where in one version (config/edit.yaml)."""
from dataclasses import dataclass
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Beat:
    name: str
    start: float
    end: float
    role: str
    face: str


@dataclass(frozen=True)
class Segment:
    kind: str          # "src" | "broll"
    path: str
    start: float
    dur: float
    face: str
    grade: str = ""    # ffmpeg filter chain after the crop (b-roll only; from media/match.py)
    slot: str = ""     # the edit slot a b-roll segment fills


def load_edit(path, fps=24):
    """config/edit.yaml as a dict. cutaway_s and endcard_s must sit on the frame grid (a whole number of frames at `fps`):
    a cut between frames would drift the audio against the picture; ValueError otherwise."""
    edit = yaml.safe_load(Path(path).read_text())
    for key in ("cutaway_s", "endcard_s"):
        frames = float(edit[key]) * fps
        if abs(frames - round(frames)) > 1e-6:
            raise ValueError(f"edit.yaml: {key} {edit[key]} s is not a whole number of frames at {fps} fps")
    return edit


def plan(beats, source, broll, hook, length_s, edit, *, fps=24):
    order = [e for e in edit[hook][length_s] if not e.startswith("slot") or e in broll]
    cut = float(edit["cutaway_s"])
    need_src = round(length_s - float(edit["endcard_s"]) - cut * sum(e.startswith("slot") for e in order), 3)
    src = [beats[e] for e in order if not e.startswith("slot")]
    have = sum(b.end - b.start for b in src)
    if have + 1e-6 < need_src:
        raise ValueError(f"{hook} {length_s} s: beats give {have:.2f} s, need {need_src:.2f} s")
    k = need_src / have
    # every cut on the frame grid; the last source beat absorbs the rounding so the total stays exact
    frames = [round((b.end - b.start) * k * fps) for b in src]
    frames[-1] = round(need_src * fps) - sum(frames[:-1])
    short = [b.name for b, n in zip(src, frames) if n <= 0]
    if short:
        raise ValueError(f"{hook} {length_s} s: beat {', '.join(short)} is too short, it would get no frames")
    durs = [n / fps for n in frames]
    out, i = [], 0
    for e in order:
        if e.startswith("slot"):
            out.append(Segment("broll", broll[e], 0.0, cut, "centre", slot=e))
        else:
            b = beats[e]
            out.append(Segment("src", source, b.start, durs[i], b.face))
            i += 1
    return out
