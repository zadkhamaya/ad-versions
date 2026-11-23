import subprocess

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
