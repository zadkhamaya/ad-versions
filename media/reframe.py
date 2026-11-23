"""reframe.py — where to put a 9:16 or 1:1 crop window on a 16:9 beat: motion and faces, robust to film grain.

The recipe was measured on the real source clip (240 px wide grey frames, 12 samples per beat):
- raw frame differences are useless, the grain "moves" everywhere; after a Gaussian blur (sigma 2 px), differences between
  samples two apart, thresholded at max(97th percentile, 4.0), motion sits where people move;
- OpenCV's Haar frontal-face cascade on the frames upscaled 2x and histogram-equalised (scaleFactor 1.1, minNeighbors 6,
  minSize 30) finds the faces.
One step is added to the measured recipe: the threshold mask is morphologically opened (5x5), because grain at the threshold leaves
isolated speckle (a still, grain-only clip gave 1-4 % of its pixel-differences, scattered over every column) while motion is
blobs; and less than MIN_MOTION of the pixel-differences left after that is no motion at all.
Column weights = thresholded motion counts (normalised to 1) + face boxes (normalised to 2: faces count twice all the motion).
The window goes where it covers the most weight. A pure function of the frames it reads: no network, deterministic.
"""
import functools
import subprocess

import cv2
import numpy as np

from media import ffmpeg

AW = 240                  # analysis width in px
BLUR_SIGMA = 2.0
STEP = 2                  # frames compared are two samples apart
PERCENTILE = 97
OPEN = 5                  # grain speckle is removed by opening the mask with a OPEN x OPEN block
MIN_MOTION = 0.001        # under this share of all pixel-differences moving (after the opening) = a still shot
MIN_THRESHOLD = 4.0
FACE_WEIGHT = 2.0         # faces together count this many times all the motion
FACE_SCALE = 2            # frames are upscaled before the face search
SPLIT_SIDE = 0.25         # a split needs this much weight on each side of the gap ...
SPLIT_GAP = 0.10          # ... a gap this wide (fraction of the width) ...
SPLIT_GAP_WEIGHT = 0.03   # ... holding less than this much weight
SPLIT_COVERAGE = 0.85     # ... and a window that covers less than this
FACE_MERGE = 0.05         # detections closer than this (fraction of the width) are one face


@functools.lru_cache(maxsize=1)
def _cascade():
    c = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
    if c.empty():
        raise RuntimeError("OpenCV's frontal-face cascade is missing")
    return c


def _frames(path, start, end, n, aw, ah):
    """Up to n grey aw x ah frames evenly spread over [start, end) as an array (k, ah, aw)."""
    dur = end - start
    raw = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{dur:.3f}", "-i", str(path), "-an",
                          "-vf", f"fps={n / dur:.6f},scale={aw}:{ah},format=gray", "-frames:v", str(n), "-f", "rawvideo", "-"],
                         capture_output=True, check=True).stdout
    k = len(raw) // (aw * ah)
    return np.frombuffer(raw, np.uint8)[:k * aw * ah].reshape(k, ah, aw)


def _motion(frames):
    """Per-column count of pixels that moved (blurred, STEP samples apart, over max(97th percentile, 4.0)): grain is not motion."""
    if len(frames) <= STEP:
        return np.zeros(frames.shape[2])
    blurred = np.stack([cv2.GaussianBlur(f.astype(np.float32), (0, 0), BLUR_SIGMA) for f in frames])
    diff = np.abs(blurred[STEP:] - blurred[:-STEP])
    mask = (diff > max(float(np.percentile(diff, PERCENTILE)), MIN_THRESHOLD)).astype(np.uint8)
    block = np.ones((OPEN, OPEN), np.uint8)
    mask = np.stack([cv2.morphologyEx(m, cv2.MORPH_OPEN, block) for m in mask])
    if mask.sum() < MIN_MOTION * mask.size:
        return np.zeros(frames.shape[2])
    return mask.sum(axis=(0, 1)).astype(float)


def _faces(frames):
    """Face boxes (x, w) in analysis pixels, one list entry per detection over all frames."""
    boxes = []
    for f in frames:
        up = cv2.equalizeHist(cv2.resize(f, None, fx=FACE_SCALE, fy=FACE_SCALE))
        for x, _, w, _ in _cascade().detectMultiScale(up, scaleFactor=1.1, minNeighbors=6, minSize=(30, 30)):
            boxes.append((x / FACE_SCALE, w / FACE_SCALE))
    return boxes


def _face_centres(boxes, aw):
    """Distinct faces as x centres (fractions of the width): detections within FACE_MERGE of each other are one face."""
    groups = []
    for c in sorted((x + w / 2) / aw for x, w in boxes):
        if groups and c - groups[-1][-1] < FACE_MERGE:
            groups[-1].append(c)
        else:
            groups.append([c])
    return [round(float(np.mean(g)), 3) for g in groups]


def weights(frames, aw):
    """(column weights summing to 1 or all zero, face x centres)."""
    motion = _motion(frames)
    w = motion / motion.sum() if motion.sum() > 0 else motion
    boxes = _faces(frames)
    if boxes:
        face = np.zeros(aw)
        for x, bw in boxes:
            face[max(0, int(round(x))):min(aw, int(round(x + bw)))] += 1.0
        w = w + FACE_WEIGHT * face / face.sum()
    total = w.sum()
    return (w / total if total > 0 else w), _face_centres(boxes, aw)


def best_window(w, wcols):
    """(centre as a fraction of the width, covered share of the weight) of the wcols-wide window holding the most weight;
    among equally good positions, the one nearest the middle of those positions."""
    n = len(w)
    wcols = min(wcols, n)
    cum = np.concatenate([[0.0], np.cumsum(w)])
    cover = cum[wcols:] - cum[:n - wcols + 1]
    best = np.flatnonzero(cover >= cover.max() - 1e-9)
    p = int(best[np.argmin(np.abs(best - (best[0] + best[-1]) / 2))])
    return (p + wcols / 2) / n, float(cover[p])


def is_split(w):
    """Two clusters of weight: at least SPLIT_SIDE of it on each side of a gap of at least SPLIT_GAP of the width that holds
    less than SPLIT_GAP_WEIGHT (a wider such gap contains one this size, so this size is the one to look for)."""
    n = len(w)
    g = int(np.ceil(SPLIT_GAP * n - 1e-9))
    cum = np.concatenate([[0.0], np.cumsum(w)])
    total = cum[-1]
    if total <= 0:
        return False
    for a in range(1, n - g):
        if (cum[a + g] - cum[a]) / total < SPLIT_GAP_WEIGHT and cum[a] / total >= SPLIT_SIDE and (total - cum[a + g]) / total >= SPLIT_SIDE:
            return True
    return False


def span(w):
    """The 10-90 % range of the weight, as fractions of the width."""
    n = len(w)
    cum = np.cumsum(w) / w.sum()
    return [round(float(np.searchsorted(cum, 0.10)) / n, 3), round(float(np.searchsorted(cum, 0.90) + 1) / n, 3)]


def analyse(path, start, end, *, n=12):
    """What the frames of [start, end) say, once for any number of formats: (column weights at AW columns, face x centres, (src_w, src_h))."""
    sw, sh = ffmpeg._size(path)
    ah = max(2, int(round(AW * sh / sw / 2)) * 2)
    frames = _frames(path, start, end, n, AW, ah)
    w, faces = weights(frames, AW) if len(frames) else (np.zeros(AW), [])
    return w, faces, (sw, sh)


def window(analysis, fmt):
    """The suggestion for `fmt` from an analysis (see suggest)."""
    w, faces, (sw, sh) = analysis
    cw = ffmpeg.crop_box(sw, sh, fmt)[0]
    wcols = int(round(cw / sw * AW))
    if w.sum() <= 0 or wcols >= AW:
        return {"x": 0.5, "coverage": 1.0, "faces": faces, "split": False, "span": span(w) if w.sum() > 0 else [0.0, 1.0]}
    x, coverage = best_window(w, wcols)
    return {"x": round(x, 4), "coverage": round(coverage, 3), "faces": faces,
            "split": bool(coverage < SPLIT_COVERAGE and is_split(w)), "span": span(w)}


def suggest(path, start, end, fmt, *, n=12):
    """Where the `fmt` crop window should sit over [start, end) of the clip at `path`:
    {"x": window centre (fraction of the width), "coverage": share of the weight inside it, "faces": [x centres],
     "split": true when the action sits in two places the window cannot hold together, "span": [lo, hi] (the 10-90 % range)}.
    No motion and no face (a still shot): centre, coverage 1.0. A window as wide as the frame: centre."""
    return window(analyse(path, start, end, n=n), fmt)


def suggest_formats(path, start, end, fmts, *, n=12):
    """{fmt: suggestion} for several formats from one pass over the frames."""
    a = analyse(path, start, end, n=n)
    return {f: window(a, f) for f in fmts}
