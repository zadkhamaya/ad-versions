"""endcard.py — the end card PNG per format: logo (or the brand name as text) above the wrapped disclaimer, made with ffmpeg
drawtext (DejaVu Sans Bold). No PIL: line widths are estimated from a per-character width, set above DejaVu Sans Bold's real
average so the text stays inside 86 % of the frame (checked against the rendered pixels in tests/test_endcard.py)."""
import subprocess
import tempfile
from pathlib import Path

from media.ffmpeg import _size, filter_path
from media.naming import FORMATS

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
BG = "0x0f1b2d"
CHAR_EM = 0.70            # estimated width of one character, in em (DejaVu Sans Bold: ~0.62 mixed text, ~0.78 capitals)
TEXT_W = 0.86             # text lines stay within this share of the frame width
LOGO_W, LOGO_H_MAX = 0.40, 0.25      # logo: 40 % of the width, at most a quarter of the height
LINE_GAP = 1.35


def est_width(line, fs):
    return len(line) * fs * CHAR_EM


def disclaimer_px(fmt):
    return round(0.04 * min(FORMATS[fmt]))


def wrap(text, fs, max_w):
    """Greedy word wrap on est_width; a word wider than max_w is split."""
    per = max(1, int(max_w // (fs * CHAR_EM)))
    lines, cur = [], ""
    for word in text.split():
        while len(word) > per:
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:per])
            word = word[per:]
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= per:
            cur += " " + word
        else:
            lines.append(cur)
            cur = word
    return lines + ([cur] if cur else [])


def _drawtext(path, fs, x_expr, y):
    # textfile + expansion=none: the text is data (no %{...}, no quoting problems)
    return (f"drawtext=fontfile={filter_path(FONT)}:textfile={filter_path(path)}:expansion=none:fontsize={fs}:fontcolor=white:"
            f"x={x_expr}:y={y}")


MIN_FS = 0.022            # the smallest legible disclaimer: this share of the shorter side
FIT_H = 0.90              # the whole block (logo + disclaimer) stays within this share of the height
SHRINK = 0.94


def _layout(fmt, disclaimer, logo_size, brand, scale):
    """Sizes and positions for one scale factor (1.0 = the default sizes). Returns the layout dict."""
    W, H = FORMATS[fmt]
    fs = max(1, round(disclaimer_px(fmt) * scale))
    lines = wrap(disclaimer, fs, TEXT_W * W)
    gap, line_h = round(0.04 * min(W, H) * scale), round(fs * LINE_GAP)
    lay = {"fs": fs, "lines": lines, "gap": gap, "line_h": line_h}
    if logo_size:
        lw, lh = logo_size
        k = min(LOGO_W * W / lw, LOGO_H_MAX * H / lh) * scale
        lay["logo_w"], lay["logo_h"] = max(2, round(lw * k / 2) * 2), max(2, round(lh * k / 2) * 2)
        top_h = lay["logo_h"]
    else:
        bfs = max(1, round(min(0.10 * min(W, H), TEXT_W * W / max(1, len(brand)) / CHAR_EM) * scale))
        lay["brand_fs"], lay["brand_lines"] = bfs, wrap(brand, bfs, TEXT_W * W)
        top_h = round(bfs * LINE_GAP) * len(lay["brand_lines"])
    lay["top_h"] = top_h
    lay["block_h"] = top_h + gap + line_h * len(lines)
    centre_top = round(0.46 * H - lay["block_h"] / 2)
    lay["top"] = max(round((1 - FIT_H) / 2 * H), min(centre_top, round((1 + FIT_H) / 2 * H) - lay["block_h"]))
    return lay


def fit(fmt, disclaimer, logo=None, brand="BRAND"):
    """The layout of the end card: the block (logo/brand + disclaimer) is shrunk until it fits within 90 % of the height;
    a disclaimer that cannot fit at MIN_FS raises ValueError (no drawing: the server asks this before taking a render slot)."""
    W, H = FORMATS[fmt]
    logo_size = _size(str(logo)) if logo else None
    scale = 1.0
    while True:
        lay = _layout(fmt, disclaimer, logo_size, brand, scale)
        if lay["block_h"] <= FIT_H * H:
            return lay
        scale *= SHRINK
        if disclaimer_px(fmt) * scale < MIN_FS * min(W, H):
            raise ValueError(f"disclaimer too long for the {fmt} end card ({len(disclaimer)} characters)")


def make(fmt, disclaimer, out_png, logo=None, brand="BRAND"):
    """Write the end card for `fmt` to out_png (layout from fit()). Returns {"lines", "fs", "logo"}."""
    W, H = FORMATS[fmt]
    lay = fit(fmt, disclaimer, logo, brand)
    fs, lines, gap, line_h, top, logo_h = lay["fs"], lay["lines"], lay["gap"], lay["line_h"], lay["top"], lay["top_h"]
    with tempfile.TemporaryDirectory() as tmp:
        files = []

        def textfile(text):
            p = Path(tmp) / f"t{len(files)}.txt"
            p.write_text(text)
            files.append(p)
            return p
        draws = []
        if not logo:
            for j, text in enumerate(lay["brand_lines"]):
                draws.append(_drawtext(textfile(text), lay["brand_fs"], "(w-text_w)/2", top + j * round(lay["brand_fs"] * LINE_GAP)))
        for j, text in enumerate(lines):
            draws.append(_drawtext(textfile(text), fs, "(w-text_w)/2", top + logo_h + gap + j * line_h))
        base = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c={BG}:s={W}x{H}:d=1:r=1"]
        if logo:
            cmd = [*base, "-i", str(logo), "-filter_complex",
                   f"[1:v]scale={lay['logo_w']}:{lay['logo_h']}[lg];[0:v][lg]overlay=x=(W-w)/2:y={top},{','.join(draws)}[o]", "-map", "[o]"]
        else:
            cmd = [*base, "-vf", ",".join(draws)]
        subprocess.run([*cmd, "-frames:v", "1", str(out_png)], check=True, capture_output=True)
    return {"lines": lines, "fs": fs, "logo": bool(logo)}
