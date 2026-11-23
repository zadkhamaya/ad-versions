from media import match
from media.ffmpeg import render_picture
from media.timeline import Segment


def test_bright_is_far_from_ref(media):
    assert match.distance(match.stats(str(media / "bright.mp4")), match.stats(str(media / "ref.mp4"))) > 12


def test_transfer_brings_it_within_12(media, tmp_path):
    src, ref = match.stats(str(media / "bright.mp4")), match.stats(str(media / "ref.mp4"))
    seg = Segment("broll", str(media / "bright.mp4"), 0.0, 2.0, "centre", match.transfer_filter(src, ref, 0, 0))
    out = tmp_path / "graded.mp4"
    render_picture([seg], "16X9", str(media / "endcard.png"), str(out), endcard_s=0.5)
    after = match.stats(str(out), 0.0, 2.0)
    assert match.distance(after, ref) <= 12


def test_gain_clamped():
    src = {"mean": [100, 100, 100], "std": [50, 50, 50]}
    ref = {"mean": [40, 40, 40], "std": [5, 5, 5]}
    f = match.transfer_filter(src, ref, 0, 0)
    assert "*0.5+" in f and "*0.1+" not in f


def test_grain_and_sharpen_optional():
    s = {"mean": [10, 10, 10], "std": [5, 5, 5]}
    bare = match.transfer_filter(s, s, 0, 0)
    assert "noise" not in bare and "unsharp" not in bare
    full = match.transfer_filter(s, s, 6, 0.5)
    assert "noise=alls=6:allf=t" in full and "unsharp=5:5:0.5" in full


def test_window_only(media):
    st = match.stats(str(media / "redblue.mp4"), start=1.0, dur=0.5)
    r, g, b = st["mean"]
    assert b > 150 and r < 60
