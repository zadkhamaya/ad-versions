"""match.py — grade b-roll to the source beat before it (spec §4.3, matching parts 2-3): colour stats, an affine
per-channel transfer as an ffmpeg filter, the source's sharpness and grain, and the distance check."""
import subprocess

import numpy as np

GAIN_MIN, GAIN_MAX = 0.5, 2.0
WIDTH = 160


def _duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


def _size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def stats(path, start=0.0, dur=None, n=8) -> dict:
    """Per-channel mean and spread over n frames sampled evenly in [start, start+dur), read as rgb24 at 160 px wide."""
    path = str(path)
    if dur is None:
        dur = max(_duration(path) - start, 0.04)
    w, h = _size(path)
    sh = int(round(h * WIDTH / w / 2) * 2)
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", path,
           "-vf", f"fps={n}/{dur:.3f},scale={WIDTH}:{sh}", "-frames:v", str(n), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    px = np.frombuffer(raw, np.uint8).reshape(-1, 3).astype(np.float64)
    if not len(px):
        raise ValueError(f"no frames read from {path} at {start}+{dur}")
    return {"mean": [round(float(v), 3) for v in px.mean(0)], "std": [round(float(v), 3) for v in px.std(0)]}


def transfer_filter(src: dict, ref: dict, grain: int, sharpen: float) -> str:
    """lutrgb mapping src's per-channel mean/spread onto ref's (gain clamped to 0.5-2.0), then unsharp and grain."""
    parts = []
    for ch, ms, ss, mr, sr in zip("rgb", src["mean"], src["std"], ref["mean"], ref["std"]):
        k = min(max(sr / ss if ss > 1e-6 else 1.0, GAIN_MIN), GAIN_MAX)
        parts.append(f"{ch}='clip((val-{ms:.3f})*{k:.4g}+{mr:.3f},0,255)'")
    chain = ["lutrgb=" + ":".join(parts)]
    if sharpen:
        chain.append(f"unsharp=5:5:{sharpen:g}")
    if grain:
        chain.append(f"noise=alls={int(grain)}:allf=t")
    return ",".join(chain)


def distance(a: dict, b: dict) -> float:
    """The largest absolute per-channel mean difference (of 255)."""
    return round(max(abs(x - y) for x, y in zip(a["mean"], b["mean"])), 3)
