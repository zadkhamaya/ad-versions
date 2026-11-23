from pathlib import Path
import pytest
from media.timeline import plan, load_edit

EDIT = load_edit(Path(__file__).resolve().parents[1] / "config" / "edit.yaml")
BROLL = {"slot1": "b1.mp4", "slot2": "b2.mp4", "slot3": "b3.mp4"}

def total(segs): return round(sum(x.dur for x in segs), 3)
def names(segs): return [x.path if x.kind == "broll" else x.start for x in segs]

def test_six_is_the_opener(beats):
    s = plan(beats, "src.mp4", BROLL, "H1", 6, EDIT)
    assert total(s) == 4.5 and [x.kind for x in s] == ["src"] and s[0].start == 0.2

def test_fifteen_order(beats):
    s = plan(beats, "src.mp4", BROLL, "H1", 15, EDIT)
    assert [x.kind for x in s] == ["src", "src", "broll", "src", "src"] and total(s) == 13.5
    assert s[2].path == "b1.mp4" and s[2].dur == 3.0

def test_thirty_h2_opens_on_s4(beats):
    s = plan(beats, "src.mp4", BROLL, "H2", 30, EDIT)
    kinds = [x.kind for x in s]
    assert s[0].start == 15.2 and kinds.count("broll") == 3 and total(s) == 28.5
    assert all(not (a == b == "broll") for a, b in zip(kinds, kinds[1:]))

def test_dropped_cutaway_keeps_length(beats):
    s = plan(beats, "src.mp4", {"slot1": "b1.mp4"}, "H1", 30, EDIT)
    assert [x.kind for x in s].count("broll") == 1 and total(s) == 28.5

def test_faces_follow_beats(beats):
    s = plan(beats, "src.mp4", BROLL, "H1", 30, EDIT)
    assert [x.face for x in s if x.kind == "src"] == ["centre", "centre", "left", "centre", "right", "centre"]

def test_too_short_refused(beats):
    from media.timeline import Beat
    short = dict(beats, S1=Beat("S1", 0.2, 2.0, "opener-H1", "centre"))
    with pytest.raises(ValueError, match="H1 6"):
        plan(short, "src.mp4", BROLL, "H1", 6, EDIT)

def test_durations_are_whole_frames(beats):
    for hook in ("H1", "H2"):
        for length in (6, 15, 30):
            for b in (BROLL, {"slot1": "b1.mp4"}):
                s = plan(beats, "src.mp4", b, hook, length, EDIT)
                for x in s:
                    assert abs(x.dur * 24 - round(x.dur * 24)) < 1e-6, (hook, length, x)

def test_durations_whole_frames_odd_beats(beats):
    from media.timeline import Beat
    odd = dict(beats, S1=Beat("S1", 0.2, 5.113, "opener-H1", "centre"), S2=Beat("S2", 5.2, 9.337, "tension", "centre"),
               S3=Beat("S3", 10.2, 14.971, "routine", "left"))
    s = plan(odd, "src.mp4", BROLL, "H1", 30, EDIT)
    assert all(abs(x.dur * 24 - round(x.dur * 24)) < 1e-6 for x in s)
    assert total(s) == 28.5

def test_fps_keyword(beats):
    s = plan(beats, "src.mp4", BROLL, "H1", 15, EDIT, fps=25)
    assert all(abs(x.dur * 25 - round(x.dur * 25)) < 1e-6 for x in s)


def test_edit_times_must_sit_on_the_24_fps_grid(tmp_path):
    base = (Path(__file__).resolve().parents[1] / "config" / "edit.yaml").read_text()
    assert load_edit(tmp_path_edit(tmp_path, base))["cutaway_s"] == 3.0
    for key, bad in (("cutaway_s", "3.01"), ("endcard_s", "1.51"), ("cutaway_s", "2.99")):
        text = base.replace(f"{key}: {'3.0' if key == 'cutaway_s' else '1.5'}", f"{key}: {bad}")
        assert text != base
        with pytest.raises(ValueError, match=key):
            load_edit(tmp_path_edit(tmp_path, text))
    assert load_edit(tmp_path_edit(tmp_path, base.replace("endcard_s: 1.5", "endcard_s: 1.625")))["endcard_s"] == 1.625   # 39 frames


def tmp_path_edit(tmp_path, text):
    p = tmp_path / "edit.yaml"
    p.write_text(text)
    return p


def test_a_beat_that_would_get_no_frames_is_refused():
    from media.timeline import Beat
    beats = {"S1": Beat("S1", 0, 5.0, "", "centre"), "S2": Beat("S2", 5, 10.0, "", "centre"), "S4": Beat("S4", 10, 15.0, "", "centre"),
             "S5": Beat("S5", 15, 15.001, "", "centre")}
    with pytest.raises(ValueError, match="S5.*no frames"):
        plan(beats, "s.mp4", {"slot1": "b.mp4"}, "H1", 15, EDIT)
    beats["S5"] = Beat("S5", 15, 20.0, "", "centre")
    assert total(plan(beats, "s.mp4", {"slot1": "b.mp4"}, "H1", 15, EDIT)) == 13.5
