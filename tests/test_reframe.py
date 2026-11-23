import pytest

from media import reframe


def test_a_mover_on_the_left_third_pulls_the_9x16_window_left_without_a_split(reframe_clips):
    r = reframe.suggest(str(reframe_clips / "left.mp4"), 0.0, 3.0, "9X16")
    assert set(r) == {"x", "coverage", "faces", "split", "span"}
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
    assert r == {"x": 0.5, "coverage": 1.0, "faces": [], "split": False, "span": r["span"]}


# the weight maths, without a video

def test_window_search_picks_the_most_covered_position_and_centres_a_plateau():
    import numpy as np
    w = np.zeros(100); w[10:30] = 1.0
    x, cov = reframe.best_window(w / w.sum(), 32)       # every window starting at 0..10 holds all of it: the middle one, start 5
    assert cov == pytest.approx(1.0) and x == pytest.approx(0.21)
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
