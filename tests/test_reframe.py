import pytest

from media import reframe


def test_a_mover_on_the_left_third_pulls_the_9x16_window_left_without_a_split(reframe_clips):
    r = reframe.suggest(str(reframe_clips / "left.mp4"), 0.0, 3.0, "9X16")
    assert set(r) == {"x", "coverage", "faces", "split", "review", "span"}
    assert r["x"] < 0.35 and not r["split"] and r["faces"] == []
    assert r["coverage"] > 0.85 and 0.0 <= r["span"][0] < r["span"][1] < 0.4


def test_a_1x1_window_covers_the_left_mover_too(reframe_clips):
    r = reframe.suggest(str(reframe_clips / "left.mp4"), 0.0, 3.0, "1X1")
    assert r["x"] < 0.5 and r["coverage"] > 0.95 and not r["split"]


def test_two_movers_far_apart_are_a_split(reframe_clips):
    r = reframe.suggest(str(reframe_clips / "split.mp4"), 0.0, 3.0, "9X16")
    assert r["split"] and r["coverage"] < 0.85
    assert r["span"][0] < 0.3 and r["span"][1] > 0.7


def test_film_grain_alone_is_no_motion(reframe_clips):
    r = reframe.suggest(str(reframe_clips / "still.mp4"), 0.0, 3.0, "9X16")
    assert 0.4 <= r["x"] <= 0.6 and not r["split"] and r["faces"] == []


def test_a_range_inside_the_clip_is_read(reframe_clips):
    a = reframe.suggest(str(reframe_clips / "left.mp4"), 1.0, 2.5, "9X16")
    assert a["x"] < 0.35


def test_it_is_deterministic(reframe_clips):
    p = str(reframe_clips / "split.mp4")
    assert reframe.suggest(p, 0.0, 3.0, "9X16") == reframe.suggest(p, 0.0, 3.0, "9X16")


def test_a_window_as_wide_as_the_frame_is_centre(reframe_clips):
    r = reframe.suggest(str(reframe_clips / "left.mp4"), 0.0, 3.0, "16X9")
    assert r == {"x": 0.5, "coverage": 1.0, "faces": [], "split": False, "review": False, "span": r["span"]}


# the weight maths, without a video

def test_window_search_picks_the_most_covered_position_and_centres_it_on_the_weight():
    import numpy as np
    w = np.zeros(100); w[10:30] = 1.0
    x, cov = reframe.best_window(w / w.sum(), 32)       # every window starting at 0..10 holds all of it: the one centred on column 20
    assert cov == pytest.approx(1.0) and x == pytest.approx(0.20)
    w = np.zeros(100); w[0:10] = 1.0; w[90:100] = 0.5
    x, cov = reframe.best_window(w / w.sum(), 32)       # the heavier end wins
    assert cov == pytest.approx(2 / 3) and x == pytest.approx(0.16)


def test_split_needs_two_clusters_and_a_gap():
    import numpy as np
    w = np.zeros(100); w[5:15] = 1; w[85:95] = 1; w /= w.sum()
    assert reframe.is_split(w)
    one = np.zeros(100); one[5:15] = 1; one[30:40] = 0.01; one /= one.sum()
    assert not reframe.is_split(one)                             # 99 % on one side
    close = np.zeros(100); close[30:40] = 1; close[45:55] = 1; close /= close.sum()
    assert not reframe.is_split(close)                           # a gap of 5 % is under 10 %
    leak = np.zeros(100); leak[5:15] = 1; leak[85:95] = 1; leak[15:85] = 0.1; leak /= leak.sum()
    assert not reframe.is_split(leak)                            # every 10-column gap holds over 3 % of the weight


def test_suggest_formats_reads_the_frames_once_for_all_formats(reframe_clips):
    p = str(reframe_clips / "left.mp4")
    both = reframe.suggest_formats(p, 0.0, 3.0, ["9X16", "1X1"])
    assert both == {"9X16": reframe.suggest(p, 0.0, 3.0, "9X16"), "1X1": reframe.suggest(p, 0.0, 3.0, "1X1")}


def test_a_small_blob_survives_the_grain_filter(tmp_path):
    from conftest import ff
    ff("-f", "lavfi", "-i", "color=c=0x808080:s=640x360:r=24:d=3", "-f", "lavfi", "-i", "color=c=white:s=24x24:r=24:d=3", "-filter_complex",
       "[0][1]overlay=x='450+30*sin(2*PI*t)':y=150,noise=alls=30:allf=t:all_seed=3", "-c:v", "libx264", "-preset", "ultrafast",
       "-crf", "18", "-pix_fmt", "yuv420p", str(tmp_path / "small.mp4"))
    r = reframe.suggest(str(tmp_path / "small.mp4"), 0.0, 3.0, "9X16")
    assert r["x"] > 0.6 and r["coverage"] > 0.9


# the sheet

def _pixels(jpg):
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(jpg)],
                         capture_output=True, text=True, check=True).stdout.strip().split(",")
    w, h = int(out[0]), int(out[1])
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(jpg), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    return w, h, lambda x, y: tuple(raw[(y * w + x) * 3:(y * w + x) * 3 + 3])


def test_the_sheet_has_a_640_px_row_per_beat_with_two_outlined_boxes(reframe_clips, tmp_path):
    from media import sheet
    src = str(reframe_clips / "left.mp4")
    rows = [{"name": "S1", "src": src, "t": 1.5, "boxes": [{"fmt": "9X16", "cx": 0.5, "wf": 0.3167, "set": False},
                                                          {"fmt": "1X1", "cx": 0.5, "wf": 0.5625, "set": True}], "split": False},
            {"name": "S2", "src": src, "t": 1.5, "boxes": [{"fmt": "9X16", "cx": 0.2, "wf": 0.3167, "set": False}], "split": True}]
    out = tmp_path / "r.jpg"
    sheet.reframe(rows, str(out))
    w, h, px = _pixels(out)
    assert (w, h) == (640, 720)
    yellow, cyan = px(220, 150), px(141, 150)      # left edges: 9X16 box 0.5 +- 0.158 = 219..421 px, 1X1 box 0.5 +- 0.281 = 140..500 px
    assert yellow[0] > 180 and yellow[1] > 180 and yellow[2] < 90
    assert cyan[0] < 90 and cyan[1] > 150 and cyan[2] > 150
    assert px(420, 150)[0] > 180 and px(420, 150)[2] < 90        # the yellow box's right edge
    assert not (px(141, 360 + 150)[0] < 90 and px(141, 360 + 150)[2] > 150)     # row 2 has no 1X1 box: no cyan edge there


def test_a_one_row_sheet_works(reframe_clips, tmp_path):
    from media import sheet
    sheet.reframe([{"name": "S1", "src": str(reframe_clips / "left.mp4"), "t": 1.0,
                    "boxes": [{"fmt": "9X16", "cx": 0.2, "wf": 0.3167, "set": False}], "split": False}], str(tmp_path / "r.jpg"))
    assert _pixels(tmp_path / "r.jpg")[:2] == (640, 360)


# faces that persist, windows centred on the weight, the review flag

def _with_faces(monkeypatch, per_frame):
    monkeypatch.setattr(reframe, "_faces", lambda frames: [list(per_frame(i)) for i in range(len(frames))])


def test_one_stray_face_hit_is_not_a_face(reframe_clips, monkeypatch):
    p = str(reframe_clips / "left.mp4")
    clean = reframe.suggest(p, 0.0, 3.0, "9X16")
    _with_faces(monkeypatch, lambda i: [(186.0, 24.0)] if i == 5 else [])          # one frame of 12, at x 0.78 (the "mug")
    blip = reframe.suggest(p, 0.0, 3.0, "9X16")
    assert blip == clean and blip["faces"] == []


def test_a_face_seen_in_a_quarter_of_the_samples_counts(reframe_clips, monkeypatch):
    p = str(reframe_clips / "still.mp4")
    _with_faces(monkeypatch, lambda i: [(108.0, 24.0)] if i < 3 else [])           # 3 of 12 = n / 4
    r = reframe.suggest(p, 0.0, 3.0, "9X16")
    assert r["faces"] == [0.5] and abs(r["x"] - 0.5) < 0.03
    _with_faces(monkeypatch, lambda i: [(108.0, 24.0)] if i < 2 else [])           # 2 of 12: under it
    assert reframe.suggest(p, 0.0, 3.0, "9X16")["faces"] == []


def test_a_centred_face_stays_centred_when_a_little_motion_sits_to_one_side():
    import numpy as np
    w = np.zeros(240); w[108:132] = 1.0; w[60:64] = 0.09        # the face; 1.5 % stray motion that a window could pick up beside it
    w /= w.sum()
    x, cov = reframe.best_window(w, 76)
    assert abs(x - 0.5) < 0.03 and cov > 0.97
    pulled = np.zeros(240); pulled[108:132] = 1.0; pulled[60:64] = 0.9      # 13 % is worth a window that holds it
    x2, _ = reframe.best_window(pulled / pulled.sum(), 76)
    assert x2 < 0.45


def test_a_window_that_misses_the_action_is_for_review():
    import numpy as np
    w = np.zeros(240); w[100:140] = 1.0; w[10:20] = 0.6         # 13 % of the weight outside any window that holds the 40 columns
    w /= w.sum()
    a = (w, [], (1920, 1080))
    r = reframe.window(a, "9X16")
    assert r["coverage"] < 0.90 and r["review"] is True and r["split"] is False
    tight = np.zeros(240); tight[100:140] = 1.0
    assert reframe.window((tight / tight.sum(), [], (1920, 1080)), "9X16")["review"] is False
    assert reframe.window((np.zeros(240), [], (1920, 1080)), "9X16")["review"] is False       # no weight: nothing to review


def test_the_sheet_marks_review_in_orange(reframe_clips, tmp_path):
    from media import sheet
    src = str(reframe_clips / "left.mp4")

    def orange(review):
        row = {"name": "S1", "src": src, "t": 1.0, "boxes": [{"fmt": "9X16", "cx": 0.2, "wf": 0.3167, "set": False}], "split": False, "review": review}
        out = tmp_path / f"r{review}.jpg"
        sheet.reframe([row], str(out))
        w, h, px = _pixels(out)
        return sum(1 for x in range(w - 110, w) for y in range(0, 36) if px(x, y)[0] > 200 and 100 < px(x, y)[1] < 200 and px(x, y)[2] < 80)
    assert orange(True) > 150 and orange(False) < 20          # grain gives a stray pixel or two
