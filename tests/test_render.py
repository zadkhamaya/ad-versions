import json, subprocess
from pathlib import Path
from media.ffmpeg import render_picture, crop_filter
from media.timeline import plan, load_edit

ROOT = Path(__file__).resolve().parents[1]

def probe(p):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height,codec_name,r_frame_rate:format=duration",
                          "-of", "json", str(p)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)

def test_crop_filter_tall_from_wide():
    f = crop_filter(1920, 1080, "9X16", "left")
    assert f.startswith("crop=608:1080:0:0") and "scale=1080:1920" in f

def test_crop_filter_square_centre():
    assert crop_filter(1920, 1080, "1X1", "centre").startswith("crop=1080:1080:420:0")

def test_render_exact_length_and_size(media, beats, tmp_path):
    broll = {"slot1": str(media / "broll_tall.mp4")}
    segs = plan(beats, str(media / "src.mp4"), broll, "H1", 15, load_edit(ROOT / "config" / "edit.yaml"))
    out = tmp_path / "v.mp4"
    render_picture(segs, "9X16", str(media / "endcard.png"), str(out))
    p = probe(out)
    v = p["streams"][0]
    assert (v["width"], v["height"], v["codec_name"]) == (1080, 1920, "h264")
    assert v["r_frame_rate"] == "24/1"
    assert abs(float(p["format"]["duration"]) - 15.0) <= 0.1

def test_short_broll_is_padded_to_its_slot(media, beats, tmp_path):
    short = tmp_path / "short.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=768x1344:rate=24:duration=2.75",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(short)], check=True)
    segs = plan(beats, str(media / "src.mp4"), {"slot1": str(short)}, "H1", 30, load_edit(ROOT / "config" / "edit.yaml"))
    out = tmp_path / "v.mp4"
    render_picture(segs, "9X16", str(media / "endcard.png"), str(out))
    n = subprocess.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0", "-show_entries",
                        "stream=nb_read_frames", "-of", "csv=p=0", str(out)], capture_output=True, text=True, check=True).stdout.strip()
    assert int(n) == 30 * 24
    assert abs(float(probe(out)["format"]["duration"]) - 30.0) <= 1 / 24 + 1e-3

def test_off_grid_beats_keep_the_endcard_whole(media, beats, tmp_path):
    from media.timeline import Beat
    odd = dict(beats, S1=Beat("S1", 0.2, 5.113, "opener-H1", "centre"), S2=Beat("S2", 5.2, 9.337, "tension", "centre"),
               S3=Beat("S3", 10.2, 14.971, "routine", "left"), S5=Beat("S5", 20.2, 25.777, "calm", "right"))
    segs = plan(odd, str(media / "src.mp4"), {"slot1": str(media / "broll_tall.mp4")}, "H1", 30, load_edit(ROOT / "config" / "edit.yaml"))
    out = tmp_path / "v.mp4"
    render_picture(segs, "9X16", str(media / "endcard.png"), str(out))
    # every frame as one pixel: the 36 end-card frames (navy) must be exactly the last 1.5 s
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(out), "-vf", "scale=1:1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                         capture_output=True, check=True).stdout
    px = [tuple(raw[i:i + 3]) for i in range(0, len(raw), 3)]
    navy = [c[2] > c[0] + 40 and c[0] < 40 and c[1] < 40 for c in px]
    assert len(px) == 720
    assert navy[-36:] == [True] * 36 and not navy[-37]

def test_threads_cap_is_opt_in(media, beats, tmp_path):
    segs = plan(beats, str(media / "src.mp4"), {}, "H1", 6, load_edit(ROOT / "config" / "edit.yaml"))
    default = render_picture(segs, "1X1", str(media / "endcard.png"), str(tmp_path / "a.mp4"))
    capped = render_picture(segs, "1X1", str(media / "endcard.png"), str(tmp_path / "b.mp4"), threads=1)
    assert "-threads" not in default
    i = capped.index("-threads")
    assert capped[i + 1] == "1" and i < capped.index(str(tmp_path / "b.mp4"))


def _x(src_w, src_h, fmt, face):
    return int(crop_filter(src_w, src_h, fmt, face).split(":")[2])

def test_crop_position_is_the_windows_centre_as_a_fraction_of_the_width():
    # 9X16 window 608 px, 1X1 window 1080 px, source 1920 px; the offset is even (4:2:0 chroma)
    assert [_x(1920, 1080, "9X16", f) for f in (0.0, 0.21, 0.5, 1.0)] == [0, 100, 656, 1312]
    assert [_x(1920, 1080, "1X1", f) for f in (0.0, 0.21, 0.5, 1.0)] == [0, 0, 420, 840]
    assert _x(1920, 1080, "1X1", 0.4) == 228
    assert crop_filter(1920, 1080, "9X16", 0.21).startswith("crop=608:1080:100:0,scale=1080:1920")

def test_a_position_that_would_leave_the_frame_is_clamped():
    assert _x(1920, 1080, "9X16", 0.05) == 0 and _x(1920, 1080, "9X16", 0.97) == 1312
    assert _x(1920, 1080, "1X1", 0.99) == 840

def test_a_number_equal_to_the_named_positions_gives_the_same_crop():
    assert _x(1920, 1080, "9X16", 0.5) == _x(1920, 1080, "9X16", "centre")
    assert _x(1920, 1080, "9X16", 304 / 1920) == _x(1920, 1080, "9X16", "left")
    assert _x(1920, 1080, "9X16", 1 - 304 / 1920) == _x(1920, 1080, "9X16", "right")

def test_a_16x9_output_from_a_16x9_source_ignores_the_position():
    for f in ("left", "right", 0.0, 1.0):
        assert crop_filter(1920, 1080, "16X9", f).startswith("crop=1920:1080:0:0")

def test_auto_must_be_resolved_before_the_crop():
    import pytest
    with pytest.raises(ValueError, match="auto"):
        crop_filter(1920, 1080, "9X16", "auto")
