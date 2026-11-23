import subprocess
from pathlib import Path

import numpy as np
import pytest
from conftest import ff, has_filter
from media import endcard
from media.naming import FORMATS

needs_drawtext = pytest.mark.skipif(not has_filter("drawtext"), reason="ffmpeg built without drawtext")
DISCLAIMER = "A clinician assesses every request. Care plans differ from person to person."


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_disclaimer_wraps_inside_86_percent_of_the_width(fmt):
    W, _ = FORMATS[fmt]
    fs = endcard.disclaimer_px(fmt)
    lines = endcard.wrap(DISCLAIMER, fs, 0.86 * W)
    assert all(endcard.est_width(l, fs) <= 0.86 * W for l in lines)
    assert " ".join(lines) == DISCLAIMER          # the whole text, nothing dropped, in order


def test_9x16_disclaimer_needs_more_than_one_line():
    W, _ = FORMATS["9X16"]
    assert len(endcard.wrap(DISCLAIMER, endcard.disclaimer_px("9X16"), 0.86 * W)) >= 2


def test_a_word_wider_than_the_line_is_split():
    lines = endcard.wrap("x" * 200, 40, 500)
    assert len(lines) > 1 and all(endcard.est_width(l, 40) <= 500 for l in lines) and "".join(lines) == "x" * 200


def _png_pixels(path, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 3)


def _bright(px):
    return px.max(axis=2) > 200


@needs_drawtext
@pytest.mark.parametrize("fmt", list(FORMATS))
def test_endcard_text_stays_off_the_side_margins(tmp_path, fmt):
    W, H = FORMATS[fmt]
    out = tmp_path / "e.png"
    info = endcard.make(fmt, DISCLAIMER, out, brand="BRAND")
    px = _bright(_png_pixels(out, W, H))
    assert px.any()                                  # text was drawn
    cols = np.where(px.any(axis=0))[0]
    assert cols.min() >= 0.07 * W - 1 and cols.max() <= 0.93 * W + 1     # real glyphs, not the estimate
    assert info["lines"] and " ".join(info["lines"]) == DISCLAIMER


@needs_drawtext
def test_endcard_with_a_logo_puts_it_above_the_text(tmp_path):
    W, H = FORMATS["9X16"]
    logo = tmp_path / "logo.png"
    ff("-f", "lavfi", "-i", "color=c=0xff0000:s=400x200", "-frames:v", "1", str(logo))
    out = tmp_path / "e.png"
    endcard.make("9X16", DISCLAIMER, out, logo=logo)
    px = _png_pixels(out, W, H)
    red = (px[:, :, 0] > 200) & (px[:, :, 1] < 60) & (px[:, :, 2] < 60)
    rows, cols = np.where(red.any(axis=1))[0], np.where(red.any(axis=0))[0]
    assert abs((cols.max() - cols.min() + 1) - 0.4 * W) < 0.03 * W and abs((cols.min() + cols.max()) / 2 - W / 2) < 4
    white = np.where(((px.min(axis=2) > 200)).any(axis=1))[0]
    assert white.size and white.min() > rows.max()      # the disclaimer starts below the logo


@needs_drawtext
def test_endcard_text_cannot_expand_ffmpeg_sequences(tmp_path):
    out = tmp_path / "e.png"
    endcard.make("1X1", "100% safe %{pts} 'quoted': a\\b", out)      # must not crash drawtext or print a timestamp
    assert out.exists()


LONG = ("A clinician assesses every request and decides whether a plan is right for you. Care plans differ from person to person, "
        "and results vary. This service does not replace emergency care. If you are in crisis, contact your local emergency number "
        "right away. Terms, privacy notice and availability apply in your region only. Read the full terms before you start.")[:355]


@needs_drawtext
@pytest.mark.parametrize("fmt", list(FORMATS))
@pytest.mark.parametrize("with_logo", [True, False])
def test_a_long_disclaimer_shrinks_to_fit_the_height(tmp_path, fmt, with_logo):
    W, H = FORMATS[fmt]
    assert len(LONG) == 355
    logo = None
    if with_logo:
        logo = tmp_path / "logo.png"
        ff("-f", "lavfi", "-i", "color=c=0xff0000:s=400x200", "-frames:v", "1", str(logo))
    out = tmp_path / "e.png"
    info = endcard.make(fmt, LONG, out, logo=logo)
    px = _png_pixels(out, W, H)
    bg = px[0, 0]
    ink = (np.abs(px.astype(int) - bg.astype(int)).max(axis=2) > 40)
    rows = np.where(ink.any(axis=1))[0]
    assert rows.min() >= 0.04 * H and rows.max() <= 0.96 * H            # nothing in the top/bottom 4 %
    cols = np.where(ink.any(axis=0))[0]
    assert cols.min() >= 0.07 * W - 1 and cols.max() <= 0.93 * W + 1
    assert " ".join(info["lines"]) == LONG and info["fs"] >= 0.022 * min(W, H) - 1


def test_a_disclaimer_that_cannot_fit_at_a_legible_size_is_refused(tmp_path):
    with pytest.raises(ValueError, match="too long"):
        endcard.make("16X9", ("word " * 800).strip(), tmp_path / "e.png")


# ---- the brand logo: brand/logo.png, made by brand/make-logo.sh ----

BRAND_DIR = Path(__file__).resolve().parents[1] / "brand"


def _rgba(path, w, h):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
                         capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4)


def test_the_logo_is_a_trimmed_offwhite_wordmark_on_transparent():
    logo = BRAND_DIR / "logo.png"
    assert 0 < logo.stat().st_size < 200_000
    w, h = endcard._size(str(logo))
    assert w == 1600 and 200 <= h <= 500                              # about 1600 px wide, a wordmark's proportions
    px = _rgba(logo, w, h)
    alpha = px[..., 3]
    assert alpha[0, 0] == 0 and alpha[-1, -1] == 0                    # transparent outside the letters
    ys, xs = np.where(alpha > 128)
    assert xs.min() <= 4 and xs.max() >= w - 5 and ys.min() <= 4 and ys.max() >= h - 5   # tightly trimmed (the bbox touches all four sides)
    solid = px[alpha == 255][:, :3]
    assert len(solid) > 1000 and (solid == [0xF3, 0xEE, 0xE6]).all()       # one flat warm off-white


@needs_drawtext
def test_the_logo_script_makes_the_same_logo(tmp_path):
    out = tmp_path / "again.png"
    subprocess.run(["bash", str(BRAND_DIR / "make-logo.sh"), str(out)], check=True, capture_output=True)
    assert endcard._size(str(out)) == endcard._size(str(BRAND_DIR / "logo.png"))
    w, h = endcard._size(str(out))
    a, b = _rgba(out, w, h)[..., 3].astype(int), _rgba(BRAND_DIR / "logo.png", w, h)[..., 3].astype(int)
    assert np.abs(a - b).mean() < 1.0                       # the same letters (a different freetype may move an edge pixel)
    assert (BRAND_DIR / "fonts" / "OFL-Lora.txt").read_text().count("SIL OPEN FONT LICENSE") >= 1


@needs_drawtext
def test_the_logo_script_works_from_a_path_with_a_colon_and_a_space(tmp_path):
    import shutil
    odd = tmp_path / "a:b c" / "brand"
    shutil.copytree(BRAND_DIR, odd)
    out = tmp_path / "odd.png"
    subprocess.run(["bash", str(odd / "make-logo.sh"), str(out)], check=True, capture_output=True)
    assert endcard._size(str(out)) == endcard._size(str(BRAND_DIR / "logo.png"))


@needs_drawtext
@pytest.mark.parametrize("fmt", ["9X16", "16X9"])
def test_the_end_card_with_the_brand_logo_fits(tmp_path, fmt):
    W, H = FORMATS[fmt]
    logo = BRAND_DIR / "logo.png"
    out = tmp_path / "e.png"
    info = endcard.make(fmt, DISCLAIMER, out, logo=logo, brand="Restfield")
    lay = endcard.fit(fmt, DISCLAIMER, logo, "Restfield")
    assert info["logo"] is True
    px = _bright(_png_pixels(out, W, H))
    ys, xs = np.where(px)
    assert xs.min() >= 0.07 * W - 1 and xs.max() <= 0.93 * W + 1 and ys.min() >= 0.05 * H and ys.max() <= 0.95 * H     # the whole block is inside the frame
    top, lw, lh = lay["top"], lay["logo_w"], lay["logo_h"]
    band = px[top:top + lh]                                                  # the logo's own rows
    bx = np.where(band.any(axis=0))[0]
    assert band.any() and bx.min() >= (W - lw) // 2 - 2 and bx.max() <= (W + lw) // 2 + 2          # its letters sit inside its box
    assert (bx.max() - bx.min()) > 0.9 * lw                                    # and fill the width of it, trimmed edge to edge
    assert abs((bx.min() + bx.max()) / 2 - W / 2) <= 3                          # centred
    assert px[top + lh:].any()                                                  # and the disclaimer is drawn below it
