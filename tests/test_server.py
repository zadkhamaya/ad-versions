import json
import os
import shutil
import threading
import time

import pytest
from conftest import ff, has_filter
from fastapi.testclient import TestClient

from media import ffmpeg, voice, whisper

needs_all = pytest.mark.skipif(not (has_filter("drawtext") and has_filter("subtitles")), reason="ffmpeg needs drawtext + libass")
DISCLAIMER = "A clinician assesses every request. Care plans differ from person to person."
WORDS = [{"w": "Sleep", "start": 0.2, "end": 0.6}, {"w": "better", "start": 0.7, "end": 1.2}, {"w": "tonight", "start": 1.3, "end": 2.0}]
BEATS = [{"name": n, "start": s, "end": e, "role": r, "face": f} for n, s, e, r, f in [
    ("S1", 0.2, 4.8, "opener-H1", "centre"), ("S2", 5.2, 9.8, "tension", "centre"), ("S3", 10.2, 14.8, "routine", "left"),
    ("S4", 15.2, 19.8, "opener-H2", "centre"), ("S5", 20.2, 25.8, "calm", "right"), ("S6", 26.2, 31.8, "close", "centre")]]


class FakeR2:
    def presign_get(self, key, ttl_s=1800):
        return f"https://r2.example/{key}?sig=x"


@pytest.fixture
def env(media, tmp_path, monkeypatch):
    root = tmp_path / "data"
    run = root / "runs" / "r1"
    (run / "broll").mkdir(parents=True)
    shutil.copy(media / "src.mp4", run / "source.mp4")
    shutil.copy(media / "broll_wide.mp4", run / "broll" / "H1A_1344x768.mp4")
    shutil.copy(media / "broll_tall.mp4", run / "broll" / "H1A_768x1344.mp4")
    shutil.copy(media / "music.m4a", run / "music.m4a")
    shutil.copy(media / "endcard.png", run / "logo.png")
    wav = tmp_path / "vo_fixed.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=300:duration=3", str(wav))
    calls = []

    def fake_speak(text, out_wav, **kw):
        calls.append({"text": text, **kw})
        shutil.copy(wav, out_wav)
        return 3.0

    monkeypatch.setenv("DATA_ROOT", str(root))
    monkeypatch.setenv("VOICE_SAMPLE_KEY", "samples/voice.wav")
    monkeypatch.setenv("VOICE_SAMPLE_TRANSCRIPT", "This is the sample.")
    monkeypatch.setattr(voice, "speak", fake_speak)
    monkeypatch.setattr(voice, "r2_from_env", lambda: FakeR2())
    monkeypatch.setattr(whisper, "words", lambda wav: WORDS)
    monkeypatch.setattr(whisper, "text", lambda p: "sleep better tonight")
    from media import server
    return {"root": root, "run": run, "client": TestClient(server.app), "speak": calls, "server": server, "media": media}


def body(version="H1_6S_1X1", **kw):
    b = {"run_id": "r1", "version": version, "source": "runs/r1/source.mp4", "beats": [dict(b) for b in BEATS],
         "broll": {"slot1": {"wide": "runs/r1/broll/H1A_1344x768.mp4", "tall": "runs/r1/broll/H1A_768x1344.mp4"}},
         "vo_text": "Sleep better tonight.", "disclaimer": DISCLAIMER, "music": "runs/r1/music.m4a"}
    b.update(kw)
    return b


def test_health(env):
    assert env["client"].get("/health").json() == {"ok": True}


# ---- validation and paths ----

def test_path_outside_runs_is_400(env):
    c = env["client"]
    for bad in ("runs/../../etc/passwd", "../../etc/passwd", "/etc/passwd", "runs/r1/../../outside.mp4", "other/source.mp4"):
        assert c.post("/render", json=body(source=bad)).status_code == 400, bad
    assert c.post("/check", json={"file": "../../etc/passwd", "length_s": 15, "fmt": "9X16"}).status_code == 400
    assert c.post("/transcribe", json={"file": "../../etc/passwd"}).status_code == 400
    assert c.post("/frames", json={"file": "../../etc/passwd", "n": 3}).status_code == 400
    assert c.post("/sheet", json={"files": ["runs/r1/source.mp4"], "out": "../x.jpg"}).status_code == 400


def test_symlink_out_of_runs_is_400(env, tmp_path):
    outside = tmp_path / "secret.mp4"
    shutil.copy(env["run"] / "source.mp4", outside)
    (env["run"] / "link.mp4").symlink_to(outside)
    assert env["client"].post("/render", json=body(source="runs/r1/link.mp4")).status_code == 400
    assert env["client"].post("/transcribe", json={"file": "runs/r1/link.mp4"}).status_code == 400


def test_broll_and_music_and_logo_paths_are_checked_too(env):
    c = env["client"]
    b = body(); b["broll"]["slot1"]["wide"] = "../../etc/passwd"
    assert c.post("/render", json=b).status_code == 400
    assert c.post("/render", json=body(music="/etc/passwd")).status_code == 400
    assert c.post("/render", json=body(logo="../x.png")).status_code == 400


def test_missing_input_file_is_404(env):
    assert env["client"].post("/transcribe", json={"file": "runs/r1/nope.mp4"}).status_code == 404


def test_bad_version_is_422(env):
    c = env["client"]
    for v in ("H3_15S_9X16", "H1_7S_9X16", "H1_15S_4X5", "nonsense", ""):
        r = c.post("/render", json=body(version=v))
        assert r.status_code == 422, v
    assert "version" in c.post("/render", json=body(version="H3_15S_9X16")).text


def test_bad_fields_are_422(env):
    c = env["client"]
    assert c.post("/render", json=body(run_id="../x")).status_code == 422
    assert c.post("/render", json=body(run_id="a" * 65)).status_code == 422
    assert c.post("/render", json=body(vo_text="")).status_code == 422
    b = body(); b["beats"][0]["face"] = "up"
    assert c.post("/render", json=b).status_code == 422
    b = body(); b["beats"][0]["end"] = 0.1
    assert c.post("/render", json=b).status_code == 422
    assert c.post("/render", json=body(broll={"slotX": {"wide": "runs/r1/a.mp4", "tall": "runs/r1/b.mp4"}})).status_code == 422
    assert c.post("/render", json=body(broll={"slot1": "runs/r1/a.mp4"})).status_code == 422
    assert c.post("/check", json={"file": "runs/r1/source.mp4", "length_s": 7, "fmt": "9X16"}).status_code == 422
    assert c.post("/check", json={"file": "runs/r1/source.mp4", "length_s": 15, "fmt": "4X5"}).status_code == 422
    assert c.post("/frames", json={"file": "runs/r1/source.mp4"}).status_code == 422
    assert c.post("/frames", json={"file": "runs/r1/source.mp4", "n": 2, "at": "last"}).status_code == 422
    assert c.post("/frames", json={"file": "runs/r1/source.mp4", "at": "first"}).status_code == 422


def test_missing_beats_for_the_edit_is_422(env):
    b = body("H1_15S_9X16"); b["beats"] = [x for x in BEATS if x["name"] != "S4"]
    r = env["client"].post("/render", json=b)
    assert r.status_code == 422 and "S4" in r.text


def test_length_in_check_is_cast_to_int(env):
    r = env["client"].post("/check", json={"file": "runs/r1/source.mp4", "length_s": "15", "fmt": "9X16"})
    assert r.status_code == 200 and r.json()["passed"] is False      # the source clip is no ad, but the request was valid


# ---- render ----

@needs_all
def test_render_6s_square_passes_check(env):
    c = env["client"]
    r = c.post("/render", json=body("H1_6S_1X1"))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["file"] == "runs/r1/renders/H1_6S_1X1.mp4" and (env["root"] / j["file"]).exists()
    assert j["log"]["version"] == "H1_6S_1X1" and j["log"]["reasons"] == []
    ck = c.post("/check", json={"file": j["file"], "length_s": 6, "fmt": "1X1"}).json()
    assert ck["passed"], ck
    # the voice came from the env's sample, by a presigned link, in this run's name
    sp = env["speak"][0]
    assert sp["text"] == "Sleep better tonight." and sp["sample_get"].startswith("https://r2.example/samples/voice.wav")
    assert sp["transcript"] == "This is the sample." and sp["run"] == "r1" and sp["version"] == "H1_6S_1X1"
    assert not list((env["run"]).glob("work*"))                      # scratch removed


@needs_all
def test_render_with_logo_and_default_brand(env):
    r = env["client"].post("/render", json=body("H1_6S_16X9", logo="runs/r1/logo.png"))
    assert r.status_code == 200, r.text


def _colour_source(path, dark, size="1920x1080"):
    ff("-f", "lavfi", "-i", f"color=c={dark}:s={size}:d=32:r=24", "-f", "lavfi", "-i", "sine=frequency=200:duration=32",
       "-vf", "noise=alls=12:allf=t", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path))


def _bright(path, size):
    ff("-f", "lavfi", "-i", f"color=c=0xb08850:s={size}:d=3:r=24", "-vf", "noise=alls=12:allf=t",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path))


@needs_all
def test_bright_broll_next_to_a_dark_beat_is_matched(env):
    run = env["run"]
    _colour_source(run / "source.mp4", "0x1c2a3a")
    _bright(run / "broll" / "H1A_1344x768.mp4", "1344x768")
    _bright(run / "broll" / "H1A_768x1344.mp4", "768x1344")
    r = env["client"].post("/render", json=body("H1_15S_9X16"))
    assert r.status_code == 200, r.text
    m = r.json()["log"]["broll_match"]["slot1"]
    assert m["after"] <= m["before"] and m["after"] <= 12 and m["before"] > 40
    assert "b-roll colour" not in r.json()["log"]["reasons"]


@needs_all
def test_a_bad_match_adds_the_reason(env, monkeypatch):
    from media import match
    run = env["run"]
    _colour_source(run / "source.mp4", "0x1c2a3a")
    _bright(run / "broll" / "H1A_1344x768.mp4", "1344x768")
    monkeypatch.setattr(match, "transfer_filter", lambda *a, **k: "null")       # no grade: the cutaway stays bright
    r = env["client"].post("/render", json=body("H1_15S_16X9"))
    assert r.status_code == 200, r.text
    log = r.json()["log"]
    assert log["broll_match"]["slot1"]["after"] > 12 and "b-roll colour" in log["reasons"]


def test_broll_size_follows_the_format_and_grade_follows_the_previous_source_beat(env, monkeypatch):
    seen = {}

    def fake_picture(segments, fmt, endcard_png, out_path, **kw):
        seen["segs"], seen["fmt"], seen["kw"] = segments, fmt, kw
        raise RuntimeError("stop here")

    graded = []
    from media import match
    real = match.transfer_filter
    monkeypatch.setattr(match, "transfer_filter", lambda s, r, g, sh: graded.append((s, r, g, sh)) or real(s, r, g, sh))
    monkeypatch.setattr(ffmpeg, "render_picture", fake_picture)
    c = env["client"]
    for fmt, want in (("9X16", "768x1344"), ("1X1", "1344x768"), ("16X9", "1344x768")):
        assert c.post("/render", json=body(f"H1_15S_{fmt}")).status_code == 500
        b = [s for s in seen["segs"] if s.kind == "broll"][0]
        assert b.path.endswith(want + ".mp4") and b.slot == "slot1" and b.grade.startswith("lutrgb=")
    prev = [s for s in seen["segs"] if s.kind == "src"][1]                    # S2 comes before slot1 in H1 15
    assert graded[-1][2:] == (6, 0.5)                                         # grain and sharpen from edit.yaml
    assert graded[-1][1] == __import__("media.match", fromlist=["stats"]).stats(env["run"] / "source.mp4", prev.start, prev.dur)


def test_a_missing_slot_drops_the_cutaway(env, monkeypatch):
    seen = {}
    monkeypatch.setattr(ffmpeg, "render_picture", lambda segs, *a, **k: seen.setdefault("segs", segs) and (_ for _ in ()).throw(RuntimeError("stop")))
    env["client"].post("/render", json=body("H1_15S_9X16", broll={}))
    assert [s.kind for s in seen["segs"]] == ["src"] * 4


def test_failure_is_500_with_error_and_log(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("encoder exploded")
    monkeypatch.setattr(ffmpeg, "render_picture", boom)
    r = env["client"].post("/render", json=body("H1_6S_1X1"))
    assert r.status_code == 500
    j = r.json()
    assert "encoder exploded" in j["error"] and j["log"]["version"] == "H1_6S_1X1" and "file" not in j
    assert env["client"].get("/health").status_code == 200                   # the server lives on
    assert not (env["root"] / "runs/r1/renders/H1_6S_1X1.mp4").exists() and not list(env["run"].glob("work*"))


def test_voice_failure_is_500_and_names_the_error(env, monkeypatch):
    def fail(*a, **k):
        raise voice.VoiceError("voice endpoint did not wake in 10 min")
    monkeypatch.setattr(voice, "speak", fail)
    monkeypatch.setattr(ffmpeg, "render_picture", lambda *a, **k: None)
    r = env["client"].post("/render", json=body("H1_6S_1X1"))
    assert r.status_code == 500 and "did not wake" in r.json()["error"]


def test_missing_voice_sample_env_is_a_500_with_the_name(env, monkeypatch):
    monkeypatch.delenv("VOICE_SAMPLE_KEY")
    monkeypatch.setattr(ffmpeg, "render_picture", lambda *a, **k: None)
    r = env["client"].post("/render", json=body("H1_6S_1X1"))
    assert r.status_code == 500 and "VOICE_SAMPLE_KEY" in r.json()["error"]


def test_room_tone_is_cut_from_the_source_and_handed_to_finish(env, monkeypatch):
    got = {}

    def fake_picture(segments, fmt, endcard_png, out_path, **kw):
        ffmpeg_ff = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1080x1080:d=6:r=24", "-c:v", "libx264",
                     "-pix_fmt", "yuv420p", out_path]
        import subprocess; subprocess.run(ffmpeg_ff, check=True)

    def fake_finish(picture, vo_wav, music, ass_path, out_path, **kw):
        got.update(kw, picture=picture, room_len=ffmpeg.duration(kw["room"]), pic_len=ffmpeg.duration(picture))
        shutil.copy(picture, out_path)
        return {"cmds": [], "output": {"i": -14.0, "tp": -1.5}}

    monkeypatch.setattr(ffmpeg, "render_picture", fake_picture)
    monkeypatch.setattr(ffmpeg, "finish", fake_finish)
    r = env["client"].post("/render", json=body("H1_6S_1X1"))
    assert r.status_code == 200, r.text
    assert abs(got["room_len"] - got["pic_len"]) < 0.1 and got["room"].endswith(".wav")
    assert got["threads"] == 2                                              # X264_THREADS default


def test_x264_threads_come_from_the_env(env, monkeypatch):
    monkeypatch.setenv("X264_THREADS", "1")
    seen = {}
    monkeypatch.setattr(ffmpeg, "render_picture", lambda *a, **k: seen.update(k) or (_ for _ in ()).throw(RuntimeError("stop")))
    env["client"].post("/render", json=body("H1_6S_1X1"))
    assert seen["threads"] == 1


def test_max_renders_defaults_to_one(monkeypatch):
    from media import server
    monkeypatch.delenv("MAX_RENDERS", raising=False)
    assert server.max_renders() == 1
    monkeypatch.setenv("MAX_RENDERS", "2")
    assert server.max_renders() == 2
    monkeypatch.setenv("MAX_RENDERS", "junk")
    assert server.max_renders() == 1


def _concurrent_renders(env, monkeypatch, slots):
    live, peak, lock = [0], [0], threading.Lock()

    def slow(*a, **k):
        with lock:
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        time.sleep(0.6)
        with lock:
            live[0] -= 1
        raise RuntimeError("stop")

    monkeypatch.setattr(ffmpeg, "render_picture", slow)
    server = env["server"]
    monkeypatch.setattr(server, "RENDER_SLOTS", threading.BoundedSemaphore(slots))
    codes = []

    def go(v):
        codes.append(TestClient(server.app).post("/render", json=body(v)).status_code)

    ts = [threading.Thread(target=go, args=(v,)) for v in ("H1_6S_1X1", "H1_6S_16X9", "H2_6S_1X1")]
    [t.start() for t in ts]; [t.join() for t in ts]
    return codes, peak[0]


def test_never_more_than_max_renders_at_once(env, monkeypatch):
    assert _concurrent_renders(env, monkeypatch, 2) == ([500, 500, 500], 2)


def test_one_render_at_a_time_with_one_slot(env, monkeypatch):
    assert _concurrent_renders(env, monkeypatch, 1) == ([500, 500, 500], 1)


def _post_in_thread(server, path, payload, wait):
    """POST from a thread; returns (status or None if still waiting after `wait` s, the thread)."""
    done = []

    def go():
        r = TestClient(server.app).post(path, json=payload)
        done.append((r.status_code, r.text))

    t = threading.Thread(target=go, daemon=True)
    t.start(); t.join(wait)
    t.done = done                                    # [(status, body text)] once the request has answered
    return (done[0][0] if done else None), t


def test_transcribe_waits_for_the_same_slots(env, monkeypatch):
    server = env["server"]
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(server, "RENDER_SLOTS", sem)
    sem.acquire()
    try:
        status, t = _post_in_thread(server, "/transcribe", {"file": "runs/r1/source.mp4"}, 0.5)
        assert status is None                        # held back while a render has the slot
    finally:
        sem.release()
    t.join(10)
    assert not t.is_alive()
    assert [d[0] for d in t.done] == [200] and "sleep better tonight" in t.done[0][1]


def test_transcribe_of_a_file_without_audio_is_422(env, tmp_path):
    silent = env["run"] / "silent.mp4"
    ff("-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent))
    r = env["client"].post("/transcribe", json={"file": "runs/r1/silent.mp4"})
    assert r.status_code == 422 and "no audio" in r.json()["detail"] and "error" not in r.json()


# ---- fix round 1 ----

def _stub_pipeline(env, monkeypatch):
    """render_picture/finish stand-ins: a gray picture, finish copies it. Returns the dict finish fills."""
    import subprocess
    got = {}

    def fake_picture(segments, fmt, endcard_png, out_path, **kw):
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=gray:s=1080x1080:d=6:r=24", "-c:v", "libx264",
                        "-pix_fmt", "yuv420p", out_path], check=True)

    def fake_finish(picture, vo_wav, music, ass_path, out_path, **kw):
        got.update(vo=vo_wav, music=music, ass=open(ass_path).read())
        shutil.copy(picture, out_path)
        return {"cmds": [], "output": {"i": -14.0, "tp": -1.5}}

    monkeypatch.setattr(ffmpeg, "render_picture", fake_picture)
    monkeypatch.setattr(ffmpeg, "finish", fake_finish)
    return got


def test_same_vo_text_calls_the_voice_once_per_run(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    c = env["client"]
    assert c.post("/render", json=body("H1_6S_1X1")).status_code == 200
    assert c.post("/render", json=body("H1_6S_16X9")).status_code == 200
    assert len(env["speak"]) == 1
    assert len(list((env["run"] / "vo").glob("*.wav"))) == 1
    assert c.post("/render", json=body("H1_6S_1X1", vo_text="Another script.")).status_code == 200
    assert len(env["speak"]) == 2


def test_concurrent_renders_of_one_script_share_one_voice_job(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    server = env["server"]
    monkeypatch.setattr(server, "RENDER_SLOTS", threading.BoundedSemaphore(2))
    real = voice.speak

    def slow_speak(text, out_wav, **kw):
        time.sleep(0.5)
        return real(text, out_wav, **kw)

    monkeypatch.setattr(voice, "speak", slow_speak)
    codes = []
    ts = [threading.Thread(target=lambda v=v: codes.append(TestClient(server.app).post("/render", json=body(v)).status_code))
          for v in ("H1_6S_1X1", "H1_6S_16X9")]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert codes == [200, 200] and len(env["speak"]) == 1


def test_a_failed_voice_job_is_not_cached(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    real = voice.speak
    monkeypatch.setattr(voice, "speak", lambda *a, **k: (_ for _ in ()).throw(voice.VoiceError("boom")))
    assert env["client"].post("/render", json=body("H1_6S_1X1")).status_code == 500
    assert not list((env["run"] / "vo").glob("*.wav"))
    monkeypatch.setattr(voice, "speak", real)
    assert env["client"].post("/render", json=body("H1_6S_1X1")).status_code == 200


def test_brand_folder_is_readable_for_music_and_logo_only(env, monkeypatch):
    got = _stub_pipeline(env, monkeypatch)
    brand = env["root"] / "brand"
    brand.mkdir()
    shutil.copy(env["run"] / "music.m4a", brand / "music.m4a")
    shutil.copy(env["run"] / "logo.png", brand / "logo.png")
    c = env["client"]
    r = c.post("/render", json=body("H1_6S_1X1", music="brand/music.m4a", logo="brand/logo.png"))
    assert r.status_code == 200, r.text
    assert got["music"].endswith("brand/music.m4a")
    assert c.post("/render", json=body(music="brand/../runs/r2/x.m4a")).status_code == 400
    assert c.post("/render", json=body(music="brand/../../etc/passwd")).status_code == 400
    assert c.post("/render", json=body(logo="brand/../secret.png")).status_code == 400
    # not for the source or the b-roll, and never for an output
    shutil.copy(env["run"] / "source.mp4", brand / "source.mp4")
    assert c.post("/render", json=body(source="brand/source.mp4")).status_code == 400
    assert c.post("/check", json={"file": "brand/music.m4a", "length_s": 15, "fmt": "9X16"}).status_code == 400
    assert c.post("/sheet", json={"files": ["runs/r1/source.mp4"], "out": "brand/s.jpg"}).status_code == 400


def test_inputs_must_belong_to_the_requests_run(env):
    other = env["root"] / "runs" / "r2"
    other.mkdir()
    shutil.copy(env["run"] / "source.mp4", other / "source.mp4")
    shutil.copy(env["run"] / "music.m4a", other / "music.m4a")
    c = env["client"]
    assert c.post("/render", json=body(source="runs/r2/source.mp4")).status_code == 400
    assert c.post("/render", json=body(music="runs/r2/music.m4a")).status_code == 400
    b = body(); b["broll"]["slot1"]["tall"] = "runs/r2/source.mp4"
    assert c.post("/render", json=b).status_code == 400


def test_a_failed_rerender_does_not_leave_the_old_file(env, monkeypatch):
    out = env["root"] / "runs/r1/renders/H1_6S_1X1.mp4"
    out.parent.mkdir(parents=True)
    out.write_bytes(b"old")
    monkeypatch.setattr(ffmpeg, "render_picture", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert env["client"].post("/render", json=body("H1_6S_1X1")).status_code == 500
    assert not out.exists()


def test_a_disclaimer_that_cannot_fit_is_422_before_a_slot(env, monkeypatch):
    from media import endcard
    server = env["server"]
    monkeypatch.setattr(endcard, "FIT_H", 0.1)             # nothing fits
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(server, "RENDER_SLOTS", sem)
    sem.acquire()                                          # a render holds the only slot: waiting would time the post out
    try:
        status, t = _post_in_thread(server, "/render", body("H1_6S_1X1", disclaimer=DISCLAIMER * 4), 20)
    finally:
        sem.release()
    assert status == 422 and "too long" in t.done[0][1]


def test_beats_too_short_is_422_before_a_slot_is_taken(env, monkeypatch):
    server = env["server"]
    sem = threading.BoundedSemaphore(1)
    monkeypatch.setattr(server, "RENDER_SLOTS", sem)
    sem.acquire()                                    # a render is running: a request that waits would never answer
    try:
        short = body("H1_30S_16X9")
        for x in short["beats"]:
            x["end"] = x["start"] + 0.5
        status, t = _post_in_thread(server, "/render", short, 20)
        assert status == 422 and "beats give" in t.done[0][1]
        missing = body("H1_15S_9X16"); missing["beats"] = [x for x in missing["beats"] if x["name"] != "S4"]
        status, t = _post_in_thread(server, "/render", missing, 20)
        assert status == 422 and "beats missing" in t.done[0][1] and "S4" in t.done[0][1]
    finally:
        sem.release()


def test_a_different_voice_sample_or_transcript_is_a_new_voice_job(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    c = env["client"]
    assert c.post("/render", json=body("H1_6S_1X1")).status_code == 200
    monkeypatch.setenv("VOICE_SAMPLE_KEY", "samples/other.wav")
    assert c.post("/render", json=body("H1_6S_16X9")).status_code == 200
    assert len(env["speak"]) == 2
    monkeypatch.setenv("VOICE_SAMPLE_TRANSCRIPT", "Another sample text.")
    assert c.post("/render", json=body("H2_6S_1X1")).status_code == 200
    assert len(env["speak"]) == 3
    assert c.post("/render", json=body("H2_6S_16X9")).status_code == 200      # nothing changed: cached
    assert len(env["speak"]) == 3


def test_the_voice_model_and_params_are_part_of_the_cache_key(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    c = env["client"]
    assert c.post("/render", json=body("H1_6S_1X1")).status_code == 200
    monkeypatch.setattr(voice, "PARAMS", {**voice.PARAMS, "seed": 7})
    assert c.post("/render", json=body("H1_6S_16X9")).status_code == 200
    assert len(env["speak"]) == 2


# ---- the other endpoints ----

def test_frames_forms(env):
    c, run = env["client"], env["run"]
    r = c.post("/frames", json={"file": "runs/r1/source.mp4", "at": [1.0, 2.0]})
    assert r.status_code == 200 and len(r.json()["files"]) == 2
    for f in r.json()["files"]:
        assert f.startswith("runs/r1/frames/") and f.endswith(".jpg") and (env["root"] / f).stat().st_size > 1000
        assert (env["root"] / f).read_bytes()[:2] == b"\xff\xd8"
    r = c.post("/frames", json={"file": "runs/r1/source.mp4", "n": 3})
    assert len(r.json()["files"]) == 3 and len(set(r.json()["files"])) == 3
    r = c.post("/frames", json={"file": "runs/r1/source.mp4", "at": "last"})
    assert len(r.json()["files"]) == 1 and (env["root"] / r.json()["files"][0]).exists()
    assert c.post("/frames", json={"file": "runs/r1/source.mp4", "at": [99.0]}).status_code == 422


def test_check_and_transcribe_and_sheet(env):
    c = env["client"]
    j = c.post("/check", json={"file": "runs/r1/source.mp4", "length_s": 30, "fmt": "16X9"}).json()
    assert set(j) >= {"passed", "reasons", "measured"}
    assert c.post("/transcribe", json={"file": "runs/r1/source.mp4"}).json() == {"text": "sleep better tonight", "banned": []}
    r = c.post("/sheet", json={"files": ["runs/r1/source.mp4", "runs/r1/broll/H1A_768x1344.mp4"], "out": "runs/r1/sheet.jpg"})
    assert r.status_code == 200 and r.json() == {"file": "runs/r1/sheet.jpg"} and (env["root"] / "runs/r1/sheet.jpg").stat().st_size > 1000
    assert c.post("/sheet", json={"files": [], "out": "runs/r1/s.jpg"}).status_code == 422
    assert c.post("/sheet", json={"files": ["runs/r1/source.mp4"], "out": "runs/r1/s.png"}).status_code == 422


# ---- /package ----

PKG_RUN = {"brand": "BRAND", "campaign": "SleepLaunch", "started_at": "2026-11-22T01:00:00Z", "finished_at": "2026-11-22T01:45:00Z",
           "gpu_minutes": 30, "gpu_rate_usd_h": 0.8, "voice_usd": 0.1, "llm_usd": 0.1, "link": "https://example.com", "cta": "Learn More"}


class FakeS3:
    def __init__(self):
        self.keys = []

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.keys.append((Bucket, Key))

    def generate_presigned_url(self, op, Params, ExpiresIn):
        return f"https://r2.example/{Params['Bucket']}/{Params['Key']}?e={ExpiresIn}"


def pkg_body(env, **kw):
    renders = env["root"] / "runs" / "r1" / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    (renders / "H1_6S_1X1.mp4").write_bytes(b"one")
    (renders / "H2_15S_9X16.mp4").write_bytes(b"two")
    b = {"run_id": "r1", "run": dict(PKG_RUN), "upload": False,
         "approved": [{"version": "H1_6S_1X1", "file": "runs/r1/renders/H1_6S_1X1.mp4", "hook_text": "h1", "script": "s1", "qa": {"passed": True},
                       "approver": "reviewer", "approved_at": "2026-11-22T01:40:00Z"},
                      {"version": "H2_15S_9X16", "file": "runs/r1/renders/H2_15S_9X16.mp4", "hook_text": "h2", "script": "s2", "qa": {"passed": True},
                       "approver": "reviewer", "approved_at": "2026-11-22T01:40:00Z"}],
         "rejected": [{"version": "H1_30S_16X9", "note": "bad cut"}]}
    b.update(kw)
    return b


def test_package_dry_run_builds_the_folder_without_r2(env, monkeypatch):
    from media import package
    monkeypatch.setattr(package, "s3_client_from_env", lambda: pytest.fail("a dry run must not touch R2"))
    r = env["client"].post("/package", json=pkg_body(env))
    assert r.status_code == 200
    j = r.json()
    assert j["folder"] == "runs/r1/package" and len(j["files"]) == 2 and "link" not in j
    assert j["report"] == {"assets": 2, "cost_per_asset_usd": 0.3, "turnaround_min": 45}
    pkg = env["root"] / "runs" / "r1" / "package"
    assert all((pkg / f).is_file() for f in j["files"] + ["manifest.json", "manifest.csv", "report.md", "index.html"])
    assert "bad cut" in (pkg / "report.md").read_text()
    assert (env["root"] / "runs/r1/renders/H1_6S_1X1.mp4").exists()


def test_package_with_upload_returns_the_link(env, monkeypatch):
    from media import package
    s3 = FakeS3()
    monkeypatch.setattr(package, "s3_client_from_env", lambda: s3)
    monkeypatch.setenv("R2_BUCKET", "bkt")
    r = env["client"].post("/package", json=pkg_body(env, upload=True))
    assert r.status_code == 200 and r.json()["link"] == "https://r2.example/bkt/runs/r1/package/index.html?e=604800"
    assert ("bkt", "runs/r1/package/report.md") in s3.keys and s3.keys[-1] == ("bkt", "runs/r1/package/index.html")


def test_package_upload_failure_is_500_json(env, monkeypatch):
    from media import package
    def boom():
        raise RuntimeError("R2 unreachable")
    monkeypatch.setattr(package, "s3_client_from_env", boom)
    monkeypatch.setenv("R2_BUCKET", "bkt")
    r = env["client"].post("/package", json=pkg_body(env, upload=True))
    assert r.status_code == 500 and "R2 unreachable" in r.json()["error"]


def test_package_files_must_be_in_the_runs_renders(env):
    c = env["client"]
    b = pkg_body(env)
    for bad in ("runs/r1/source.mp4", "runs/r2/renders/x.mp4", "../etc/passwd", "/etc/passwd", "brand/rulebook.yaml"):
        b["approved"][0]["file"] = bad
        assert c.post("/package", json=b).status_code == 400, bad
    b["approved"][0]["file"] = "runs/r1/renders/missing.mp4"
    assert c.post("/package", json=b).status_code == 404


def test_package_bad_requests_are_422(env):
    c = env["client"]
    assert c.post("/package", json=pkg_body(env, approved=[{"version": "H9_6S_1X1", "file": "runs/r1/renders/H1_6S_1X1.mp4",
                                                          "hook_text": "h", "script": "s"}])).status_code == 422
    assert c.post("/package", json=pkg_body(env, run_id="../x")).status_code == 422
    assert c.post("/package", json=pkg_body(env, run={**PKG_RUN, "campaign": "!!!"})).status_code == 422
    assert c.post("/package", json=pkg_body(env, run={k: v for k, v in PKG_RUN.items() if k != "gpu_minutes"})).status_code == 422


def test_package_upload_without_a_bucket_is_500(env, monkeypatch):
    monkeypatch.delenv("R2_BUCKET", raising=False)
    r = env["client"].post("/package", json=pkg_body(env, upload=True))
    assert r.status_code == 500 and "R2_BUCKET" in r.json()["error"]


def test_package_times_must_be_aware_and_ordered(env):
    c = env["client"]
    for s, f in (("2026-11-22T01:00:00", "2026-11-22T02:00:00Z"), ("2026-11-22T01:00:00Z", "2026-11-22T02:00:00"),
                 ("2026-11-22T02:00:00Z", "2026-11-22T01:00:00Z"), ("yesterday", "2026-11-22T02:00:00Z")):
        r = c.post("/package", json=pkg_body(env, run={**PKG_RUN, "started_at": s, "finished_at": f}))
        assert r.status_code == 422, (s, f)


def test_package_costs_must_be_finite(env):
    import json
    c = env["client"]
    for key in ("gpu_minutes", "gpu_rate_usd_h", "voice_usd", "llm_usd"):
        raw = json.dumps(pkg_body(env, run={**PKG_RUN, key: 1e999}))    # json.dumps writes Infinity
        assert "Infinity" in raw
        assert c.post("/package", content=raw, headers={"content-type": "application/json"}).status_code == 422, key
        raw = json.dumps(pkg_body(env, run={**PKG_RUN, key: float("nan")}))
        assert c.post("/package", content=raw, headers={"content-type": "application/json"}).status_code == 422, key


def test_package_refuses_duplicates_formula_text_and_an_empty_upload(env, monkeypatch):
    from media import package
    monkeypatch.setattr(package, "s3_client_from_env", lambda: pytest.fail("refused requests must not touch R2"))
    monkeypatch.setenv("R2_BUCKET", "bkt")
    c = env["client"]
    b = pkg_body(env)
    assert c.post("/package", json={**b, "approved": [b["approved"][0], b["approved"][0]]}).status_code == 422
    b = pkg_body(env)
    b["approved"][0]["hook_text"] = "=HYPERLINK(1)"
    assert c.post("/package", json=b).status_code == 422
    assert c.post("/package", json=pkg_body(env, approved=[], upload=True)).status_code == 422
    assert c.post("/package", json=pkg_body(env, approved=[], upload=False)).status_code == 200


def test_package_retry_ships_only_the_second_build(env, monkeypatch):
    from media import package
    s3 = FakeS3()
    monkeypatch.setattr(package, "s3_client_from_env", lambda: s3)
    monkeypatch.setenv("R2_BUCKET", "bkt")
    c = env["client"]
    b = pkg_body(env)
    assert len(c.post("/package", json={**b, "upload": False}).json()["files"]) == 2
    b["approved"] = b["approved"][:1]
    r = c.post("/package", json={**b, "upload": True})
    assert r.status_code == 200 and len(r.json()["files"]) == 1
    assert len(s3.keys) == 5 and len(list((env["root"] / "runs/r1/package").glob("*.mp4"))) == 1   # video + 3 extras + index


def test_a_concurrent_second_package_for_the_run_is_409(env, monkeypatch):
    from media import package
    server = env["server"]
    started, release = threading.Event(), threading.Event()
    real = package.build

    def slow(*a, **k):
        started.set()
        assert release.wait(20)
        return real(*a, **k)

    monkeypatch.setattr(package, "build", slow)
    b = pkg_body(env)
    first, t = [], threading.Thread(target=lambda: first.append(TestClient(server.app).post("/package", json=b).status_code), daemon=True)
    t.start()
    assert started.wait(10)
    try:
        r = TestClient(server.app).post("/package", json=b)
        assert r.status_code == 409 and "already being built" in r.json()["detail"]
    finally:
        release.set()
    t.join(20)
    assert first == [200]
    assert env["client"].post("/package", json=b).status_code == 200          # the lock is released afterwards


def test_a_failed_package_releases_the_lock(env):
    c = env["client"]
    assert c.post("/package", json=pkg_body(env, run={**PKG_RUN, "campaign": "!!!"})).status_code == 422
    assert c.post("/package", json=pkg_body(env)).status_code == 200


# ---- errors and shapes n8n depends on (final fix wave G) ----

def _lenient(env):
    """A client that gets the 500 the handler sends instead of the exception re-raised (the test client's default)."""
    return TestClient(env["server"].app, raise_server_exceptions=False)


def test_an_unexpected_failure_is_a_json_500_without_paths(env, monkeypatch):
    c = _lenient(env)
    (env["run"] / "bad.mp4").write_bytes(b"not a video")
    r = c.post("/frames", json={"file": "runs/r1/bad.mp4", "n": 2})            # ffprobe fails
    assert r.status_code == 500 and r.headers["content-type"].startswith("application/json")
    err = r.json()["error"]
    assert err.startswith("CalledProcessError:") and str(env["root"]) not in err and "/tmp" not in err and "Traceback" not in err
    monkeypatch.setattr(ffmpeg, "duration", lambda p: (_ for _ in ()).throw(OSError(f"disk full writing {env['root']}/runs/r1/x.mp4")))
    r = c.post("/frames", json={"file": "runs/r1/source.mp4", "n": 2})
    assert r.status_code == 500 and r.json()["error"] == "OSError: disk full writing x.mp4"
    monkeypatch.setattr(whisper, "text", lambda p: (_ for _ in ()).throw(RuntimeError("whisper crashed")))
    r = c.post("/transcribe", json={"file": "runs/r1/source.mp4"})
    assert r.status_code == 500 and r.json() == {"error": "RuntimeError: whisper crashed"}
    assert c.get("/health").status_code == 200


def test_the_render_error_has_no_absolute_paths_either(env, monkeypatch):
    import subprocess
    monkeypatch.setattr(ffmpeg, "render_picture", lambda *a, **k: subprocess.run(["ffmpeg", "-v", "error", "-i", str(env["root"] / "nope.mp4")], check=True))
    r = env["client"].post("/render", json=body("H1_6S_1X1"))
    assert r.status_code == 500 and str(env["root"]) not in r.json()["error"] and "/tmp" not in r.json()["error"]


def test_scrub_paths():
    from media.safe import scrub_paths
    assert scrub_paths("No such file /data/runs/r1/a b.mp4: gone") == "No such file a b.mp4: gone"
    assert scrub_paths("'/tmp/x/y.mp4' and /etc/passwd") == "'y.mp4' and passwd"
    assert scrub_paths("runs/r1/a.mp4 24/7 https://r2.example/b/k and/or 1/2") == "runs/r1/a.mp4 24/7 https://r2.example/b/k and/or 1/2"


def test_a_refused_rerender_does_not_leave_the_old_file_either(env):
    out = env["root"] / "runs/r1/renders/H1_15S_9X16.mp4"
    out.parent.mkdir(parents=True)
    c = env["client"]
    missing = body("H1_15S_9X16"); missing["beats"] = [x for x in BEATS if x["name"] != "S4"]
    out.write_bytes(b"old")
    assert c.post("/render", json=missing).status_code == 422 and not out.exists()
    out.write_bytes(b"old")
    assert c.post("/render", json=body("H1_15S_9X16", source="runs/r1/none.mp4")).status_code == 404 and not out.exists()
    out.write_bytes(b"old")
    assert c.post("/render", json=body("H1_15S_9X16", source="/etc/passwd")).status_code == 400 and not out.exists()


def test_beat_times_must_be_finite_and_inside_the_source(env):
    import json
    c = env["client"]
    for key in ("start", "end"):
        b = body(); b["beats"][0][key] = float("inf")
        raw = json.dumps(b)
        assert "Infinity" in raw
        assert c.post("/render", content=raw, headers={"content-type": "application/json"}).status_code == 422, key
    b = body(); b["beats"][0]["start"] = float("nan")
    assert c.post("/render", content=json.dumps(b), headers={"content-type": "application/json"}).status_code == 422
    b = body(); b["beats"][5]["end"] = 40.0                                     # the source clip is 32 s
    r = c.post("/render", json=b)
    assert r.status_code == 422 and "S6" in r.json()["detail"] and "32.00" in r.json()["detail"]


def test_face_accepts_the_american_spelling(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    b = body(); b["beats"][0]["face"] = "center"
    assert env["client"].post("/render", json=b).status_code == 200
    b["beats"][0]["face"] = "middle"
    assert env["client"].post("/render", json=b).status_code == 422


def test_check_reasons_carry_no_absolute_path_and_check_takes_a_version(env):
    c = env["client"]
    (env["run"] / "bad.mp4").write_bytes(b"not a video")
    r = c.post("/check", json={"file": "runs/r1/bad.mp4", "length_s": 15, "fmt": "9X16"}).json()
    assert not r["passed"] and str(env["root"]) not in " ".join(r["reasons"]) and "/tmp" not in " ".join(r["reasons"])
    by_version = c.post("/check", json={"file": "runs/r1/source.mp4", "version": "H1_30S_16X9"}).json()
    by_parts = c.post("/check", json={"file": "runs/r1/source.mp4", "length_s": 30, "fmt": "16X9"}).json()
    assert by_version == by_parts
    assert c.post("/check", json={"file": "runs/r1/source.mp4", "version": "H1_30S_16X9", "length_s": 30}).status_code == 200
    for bad in ({"version": "H1_30S_16X9", "length_s": 15}, {"version": "H1_30S_16X9", "fmt": "1X1"}, {"version": "H3_30S_16X9"},
                {"version": "nonsense"}, {}, {"length_s": 30}, {"fmt": "1X1"}):
        assert c.post("/check", json={"file": "runs/r1/source.mp4", **bad}).status_code == 422, bad


def test_sheet_draws_a_no_frame_cell_for_a_missing_render(env):
    c = env["client"]
    r = c.post("/sheet", json={"files": ["runs/r1/source.mp4", "runs/r1/renders/H1_6S_1X1.mp4"], "out": "runs/r1/sheet.jpg"})
    assert r.status_code == 200 and (env["root"] / "runs/r1/sheet.jpg").stat().st_size > 1000
    assert c.post("/sheet", json={"files": ["runs/r2/renders/x.mp4"], "out": "runs/r1/s2.jpg"}).status_code == 200      # a name only
    assert c.post("/sheet", json={"files": ["../x.mp4"], "out": "runs/r1/s3.jpg"}).status_code == 400                   # still inside runs/
    assert c.post("/sheet", json={"files": ["brand/x.mp4"], "out": "runs/r1/s3.jpg"}).status_code == 400


def test_frames_of_same_named_clips_in_different_folders_do_not_collide(env):
    (env["run"] / "other").mkdir()
    shutil.copy(env["run"] / "source.mp4", env["run"] / "other" / "source.mp4")
    c = env["client"]
    a = c.post("/frames", json={"file": "runs/r1/source.mp4", "n": 2}).json()["files"]
    b = c.post("/frames", json={"file": "runs/r1/other/source.mp4", "n": 2}).json()["files"]
    assert len(set(a) | set(b)) == 4
    assert c.post("/frames", json={"file": "runs/r1/source.mp4", "n": 2}).json()["files"] == a          # stable for the same clip


def test_health_is_async_so_queued_renders_cannot_block_it(env):
    import inspect
    assert inspect.iscoroutinefunction(env["server"].health)


# ---- render correctness (final fix wave H) ----

def _speak_seconds(env, monkeypatch, seconds, info=None):
    wav = env["root"].parent / f"vo_{seconds}.wav"
    ff("-f", "lavfi", "-i", f"sine=frequency=300:duration={seconds}", str(wav))

    def speak(text, out_wav, **kw):
        if info is not None and kw.get("info") is not None:
            kw["info"].update(info)
        shutil.copy(wav, out_wav)
        return float(seconds)

    monkeypatch.setattr(voice, "speak", speak)


def test_a_voice_over_longer_than_the_picture_before_the_end_card_is_a_reason(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    c = env["client"]
    _speak_seconds(env, monkeypatch, 5)                                    # H1_6S: 6 s - 1.5 s end card = 4.5 s
    j = c.post("/render", json=body("H1_6S_1X1")).json()
    assert j["log"]["vo_s"] == 5.0
    assert j["log"]["reasons"] == ["voice-over 5.0 s, longer than the 4.5 s before the end card"]
    _speak_seconds(env, monkeypatch, 4)
    j = c.post("/render", json=body("H1_6S_16X9", vo_text="Sleep better tonight")).json()
    assert j["log"]["reasons"] == []


def test_captions_show_the_approved_script_timed_by_whisper(env, monkeypatch):
    got = _stub_pipeline(env, monkeypatch)
    script = "A plan built around your routine and your sleep"
    heard = "A plan built around your routine in your sleep".split()                      # "and" misheard as "in"
    monkeypatch.setattr(whisper, "words", lambda wav: [{"w": w, "start": 0.2 * i, "end": 0.2 * i + 0.15} for i, w in enumerate(heard)])
    j = env["client"].post("/render", json=body("H1_6S_1X1", vo_text=script)).json()
    line = next(l for l in got["ass"].splitlines() if l.startswith("Dialogue:"))
    shown = " ".join(l.split(",,")[-1] for l in got["ass"].splitlines() if l.startswith("Dialogue:"))
    assert line.endswith(",0,0,0,,A plan built") and shown == script                 # the approved words, not "in"
    assert "0:00:00.00" in line
    assert j["log"]["reasons"] == [] and j["log"]["caption_wer"] == 0.11


def test_captions_fall_back_to_the_transcript_when_it_is_far_from_the_script(env, monkeypatch):
    got = _stub_pipeline(env, monkeypatch)
    monkeypatch.setattr(whisper, "words", lambda wav: [{"w": "Buy", "start": 0.2, "end": 0.6}, {"w": "it", "start": 0.7, "end": 1.2},
                                                       {"w": "now", "start": 1.3, "end": 2.0}])
    j = env["client"].post("/render", json=body("H1_6S_1X1")).json()
    assert "Buy it now" in got["ass"] and "Sleep" not in got["ass"]
    assert j["log"]["reasons"] == ["captions from transcript (script alignment failed)"]


def test_the_render_log_says_whether_the_voice_was_cached_and_what_it_cost(env, monkeypatch):
    _stub_pipeline(env, monkeypatch)
    _speak_seconds(env, monkeypatch, 3, info={"execution_ms": 4321})
    c = env["client"]
    first = c.post("/render", json=body("H1_6S_1X1")).json()["log"]
    again = c.post("/render", json=body("H1_6S_16X9")).json()["log"]
    assert first["vo_cached"] is False and first["vo_execution_ms"] == 4321
    assert again["vo_cached"] is True and "vo_execution_ms" not in again


def test_whisper_preload_is_opt_in(env, monkeypatch):
    server = env["server"]
    loaded = threading.Event()
    monkeypatch.setattr(whisper, "preload", loaded.set)
    monkeypatch.delenv("PRELOAD_WHISPER", raising=False)
    with TestClient(server.app) as c:
        assert c.get("/health").status_code == 200
    assert not loaded.wait(0.3)
    monkeypatch.setenv("PRELOAD_WHISPER", "1")
    with TestClient(server.app) as c:
        assert c.get("/health").status_code == 200
        assert loaded.wait(5)


def test_a_failed_preload_does_not_stop_the_server(env, monkeypatch):
    done = threading.Event()
    monkeypatch.setattr(whisper, "preload", lambda: (done.set(), (_ for _ in ()).throw(OSError("offline")))[1])
    monkeypatch.setenv("PRELOAD_WHISPER", "1")
    with TestClient(env["server"].app) as c:
        assert done.wait(5) and c.get("/health").status_code == 200


# ---- endpoints n8n needs (final fix wave I) ----

def test_transcribe_with_a_script_returns_the_final_audio_verdict(env, monkeypatch):
    c = env["client"]
    r = c.post("/transcribe", json={"file": "runs/r1/source.mp4", "script": "Sleep better tonight."}).json()
    assert r == {"text": "sleep better tonight", "wer": 0.0, "banned": [], "passed": True, "reasons": []}
    r = c.post("/transcribe", json={"file": "runs/r1/source.mp4", "script": "Sleep deep tonight.", "version": "H1_6S_1X1"}).json()
    assert r["wer"] == 0.33 and r["passed"] is False and "voice-over differs from the script" in r["reasons"][0]
    monkeypatch.setattr(whisper, "text", lambda p: "this cures insomnia, guaranteed")
    r = c.post("/transcribe", json={"file": "runs/r1/source.mp4", "script": "this cures insomnia, guaranteed"}).json()
    assert r["wer"] == 0.0 and r["banned"] == ["cures", "guaranteed"] and r["passed"] is False
    assert r["reasons"] == ["banned word(s) in the audio: cures, guaranteed"]
    assert c.post("/transcribe", json={"file": "runs/r1/source.mp4"}).json() == {"text": "this cures insomnia, guaranteed",
                                                                                 "banned": ["cures", "guaranteed"]}
    assert c.post("/transcribe", json={"file": "runs/r1/source.mp4", "script": "x", "version": "H9_1S_1X1"}).status_code == 422
    assert c.post("/transcribe", json={"file": "runs/r1/source.mp4", "script": ""}).status_code == 422


def test_rulebook_edit_and_lint_endpoints(env):
    c = env["client"]
    rb = c.get("/rulebook").json()
    assert set(rb) == {"brand", "disclaimer", "claims", "shots", "banned_words", "banned_media", "look_fallback", "tone"}
    assert rb["disclaimer"] == DISCLAIMER and "cure" in rb["banned_words"] and isinstance(rb["claims"], list) and len(rb["shots"]) >= 4
    edit = c.get("/edit").json()
    assert edit["endcard_s"] == 1.5 and edit["cutaway_s"] == 3.0 and edit["H1"]["15"] == ["S1", "S2", "slot1", "S4", "S5"]
    assert edit["H2"]["6"] == ["S4"] and set(edit["H1"]) == {"6", "15", "30"}              # int keys come as strings
    r = c.post("/lint", json={"texts": ["A secure night", "This will cure you, guaranteed", "100% calm", "doctor\nrecommended"]}).json()
    assert r == {"hits": [[], ["cure", "guaranteed"], ["100%"], ["doctor recommended"]]}
    assert c.post("/lint", json={"texts": []}).json() == {"hits": []}
    assert c.post("/lint", json={}).status_code == 422


def test_package_file_stem_must_equal_the_version(env):
    c = env["client"]
    b = pkg_body(env)
    shutil.copy(env["root"] / "runs/r1/renders/H1_6S_1X1.mp4", env["root"] / "runs/r1/renders/final.mp4")
    b["approved"][0]["file"] = "runs/r1/renders/final.mp4"
    r = c.post("/package", json=b)
    assert r.status_code == 422 and "H1_6S_1X1" in r.json()["detail"] and "final" in r.json()["detail"]


def test_package_disclaimer_comes_from_the_request_and_v_from_the_item(env):
    import json as _json
    c = env["client"]
    b = pkg_body(env)
    b["run"]["disclaimer"] = "A clinician reviews each request."
    b["approved"][1]["v"] = 3
    r = c.post("/package", json=b)
    assert r.status_code == 200, r.text
    assert [f.split("_")[-1] for f in r.json()["files"]] == ["V01.mp4", "V03.mp4"]
    m = _json.loads((env["root"] / "runs/r1/package/manifest.json").read_text())
    assert {f["disclaimer"] for f in m["files"]} == {"A clinician reviews each request."}
    b = pkg_body(env)                                                           # absent: the rulebook's
    r = c.post("/package", json=b)
    assert {f["disclaimer"] for f in _json.loads((env["root"] / "runs/r1/package/manifest.json").read_text())["files"]} == {DISCLAIMER}
    for bad in (0, 100, "x"):
        b = pkg_body(env); b["approved"][0]["v"] = bad
        assert c.post("/package", json=b).status_code == 422, bad
    b = pkg_body(env); b["run"]["disclaimer"] = ""
    assert c.post("/package", json=b).status_code == 422


def test_the_package_lock_is_held_through_the_upload(env, monkeypatch):
    from media import package
    started, release = threading.Event(), threading.Event()

    class SlowS3(FakeS3):
        def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
            started.set()
            assert release.wait(20)
            super().upload_file(Filename, Bucket, Key, ExtraArgs)

    monkeypatch.setattr(package, "s3_client_from_env", lambda: SlowS3())
    monkeypatch.setenv("R2_BUCKET", "bkt")
    b = pkg_body(env, upload=True)
    first = []
    t = threading.Thread(target=lambda: first.append(TestClient(env["server"].app).post("/package", json=b).status_code), daemon=True)
    t.start()
    assert started.wait(10)                                    # the folder is built, the upload is running
    try:
        r = TestClient(env["server"].app).post("/package", json=b)
        assert r.status_code == 409 and "already being built" in r.json()["detail"]
    finally:
        release.set()
    t.join(20)
    assert first == [200]
    monkeypatch.setattr(package, "s3_client_from_env", lambda: FakeS3())
    assert env["client"].post("/package", json=b).status_code == 200                       # released afterwards


# ---- Task 6c: crop positions ----

def _faces_seen(env, monkeypatch, version, mutate):
    seen = {}
    monkeypatch.setattr(ffmpeg, "render_picture", lambda segs, fmt, *a, **k: seen.update(segs=segs) or (_ for _ in ()).throw(RuntimeError("stop")))
    b = body(version)
    mutate(b)
    r = env["client"].post("/render", json=b)
    assert r.status_code == 500 and r.json()["error"] == "RuntimeError: stop", r.text
    return [s.face for s in seen["segs"] if s.kind == "src"]


def test_face_takes_a_number_and_x_wins_per_format(env, monkeypatch):
    def mutate(b):
        b["beats"][0]["face"] = 0.21
        b["beats"][0]["x"] = {"9X16": 0.7}
    assert _faces_seen(env, monkeypatch, "H1_6S_9X16", mutate) == [0.7]
    assert _faces_seen(env, monkeypatch, "H1_6S_1X1", mutate) == [0.21]
    assert _faces_seen(env, monkeypatch, "H1_6S_16X9", mutate) == [0.21]


def test_face_numbers_must_be_a_fraction(env):
    c = env["client"]
    for bad in (1.5, -0.1, "middle"):
        b = body(); b["beats"][0]["face"] = bad
        assert c.post("/render", json=b).status_code == 422, bad
    for bad in ({"9X16": 1.2}, {"9X16": -1}, {"4X5": 0.5}, {"16X9": 0.5}, {"9X16": "left"}, 0.5):
        b = body(); b["beats"][0]["x"] = bad
        assert c.post("/render", json=b).status_code == 422, bad


# ---- Task 6c: face "auto" ----

def _auto_render(env, monkeypatch, clip, version, mutate=lambda b: None, beat_face="auto"):
    """Render with a stub picture; returns (response json, faces of the source segments as the picture saw them)."""
    _stub_pipeline(env, monkeypatch)
    stub, seen = ffmpeg.render_picture, {}
    monkeypatch.setattr(ffmpeg, "render_picture", lambda segs, fmt, *a, **k: seen.update(segs=segs) or stub(segs, fmt, *a, **k))
    if env.get("clip") != clip:                   # copied once: a new copy is a new mtime, which is a new cache key
        shutil.copy(env["reframe_long"] / clip, env["run"] / "clip.mp4")
        env["clip"] = clip
    b = body(version, source="runs/r1/clip.mp4")
    b["beats"][0]["face"] = beat_face
    mutate(b)
    r = env["client"].post("/render", json=b)
    assert r.status_code == 200, r.text
    return r.json(), [s.face for s in seen["segs"] if s.kind == "src"]


@pytest.fixture
def auto(env, reframe_long):
    env["reframe_long"] = reframe_long
    return env


def test_auto_follows_the_action_logs_it_and_caches_it(auto, monkeypatch):
    from media import reframe
    calls = []
    real = reframe.analyse
    monkeypatch.setattr(reframe, "analyse", lambda *a, **k: calls.append(a) or real(*a, **k))
    j, faces = _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_9X16")
    assert isinstance(faces[0], float) and faces[0] < 0.35
    s = j["log"]["reframe"]["S1"]["9X16"]
    assert s["x"] == faces[0] and not s["split"] and set(s) == {"x", "coverage", "faces", "split", "span"}
    assert j["log"]["reasons"] == [] and len(calls) == 1
    cache = json.loads((auto["run"] / "reframe.json").read_text())
    assert len(cache) == 1 and "S1" in next(iter(cache)) and "9X16" in next(iter(cache))
    j2, faces2 = _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_9X16")        # the same beat and format: from the cache
    assert faces2 == faces and j2["log"]["reframe"] == j["log"]["reframe"] and len(calls) == 1
    _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_1X1")                       # another format of the same beat: one more read
    assert len(calls) == 2 and len(json.loads((auto["run"] / "reframe.json").read_text())) == 2


def test_auto_split_adds_the_reason(auto, monkeypatch):
    j, faces = _auto_render(auto, monkeypatch, "split.mp4", "H1_6S_9X16")
    assert j["log"]["reframe"]["S1"]["9X16"]["split"] is True
    assert j["log"]["reasons"] == ["crop split action in S1 (9X16)"]


def test_auto_changes_with_the_beat_or_the_source(auto, monkeypatch):
    a, fa = _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_9X16")
    b, fb = _auto_render(auto, monkeypatch, "split.mp4", "H1_6S_9X16")              # same name, same clip path: the cache is by source file
    assert b["log"]["reframe"]["S1"]["9X16"]["split"] is True and fa != fb


def test_auto_is_skipped_where_a_number_decides_or_nothing_can_move(auto, monkeypatch):
    _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_9X16")                        # something in the cache already
    j, faces = _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_9X16", lambda b: b["beats"][0].update(x={"9X16": 0.7}))
    assert faces == [0.7] and "reframe" not in j["log"]
    j, faces = _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_16X9")             # a 16:9 source into 16:9: nothing to crop
    assert "reframe" not in j["log"] and not any("16X9" in k for k in json.loads((auto["run"] / "reframe.json").read_text()))
    j, faces = _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_9X16", beat_face=0.3)
    assert faces == [0.3] and "reframe" not in j["log"]


def test_a_corrupt_reframe_cache_is_ignored(auto, monkeypatch):
    (auto["run"] / "reframe.json").write_text("{not json")
    j, faces = _auto_render(auto, monkeypatch, "left.mp4", "H1_6S_9X16")
    assert faces[0] < 0.35 and json.loads((auto["run"] / "reframe.json").read_text())


# ---- Task 6c: POST /reframe ----

def _reframe_body(**kw):
    b = {"run_id": "r1", "source": "runs/r1/clip.mp4", "beats": [{"name": "S1", "start": 0.0, "end": 3.0}, {"name": "S2", "start": 0.0, "end": 3.0}]}
    b.update(kw)
    return b


@pytest.fixture
def rf(env, reframe_clips):
    shutil.copy(reframe_clips / "left.mp4", env["run"] / "clip.mp4")
    env["clips"] = reframe_clips
    return env


def _size(path):
    import subprocess
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip().split(",")
    return int(out[0]), int(out[1])


def test_reframe_answers_each_beat_and_format_and_writes_a_sheet(rf):
    r = rf["client"].post("/reframe", json=_reframe_body())
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["sheet"] == "runs/r1/reframe.jpg" and set(j["beats"]) == {"S1", "S2"}
    for beat in j["beats"].values():
        assert set(beat) == {"9X16", "1X1"}
        assert set(beat["9X16"]) == {"x", "coverage", "faces", "split", "span"} and beat["9X16"]["x"] < 0.35 and not beat["9X16"]["split"]
    jpg = rf["root"] / "runs/r1/reframe.jpg"
    assert jpg.read_bytes()[:2] == b"\xff\xd8" and _size(jpg) == (640, 720)


def test_reframe_flags_a_split_and_takes_the_formats_asked(rf):
    shutil.copy(rf["clips"] / "split.mp4", rf["run"] / "clip.mp4")
    j = rf["client"].post("/reframe", json=_reframe_body(formats=["9X16"])).json()
    assert j["beats"]["S1"] == {"9X16": j["beats"]["S1"]["9X16"]} and j["beats"]["S1"]["9X16"]["split"] is True


def test_reframe_shows_a_given_position_as_set_and_does_not_suggest_for_it(rf, monkeypatch):
    from media import reframe
    seen = []
    real = reframe.analyse
    monkeypatch.setattr(reframe, "analyse", lambda *a, **k: seen.append(a) or real(*a, **k))
    b = _reframe_body()
    b["beats"][0]["face"] = 0.5
    b["beats"][1]["x"] = {"1X1": 0.3}
    j = rf["client"].post("/reframe", json=b).json()
    for fmt in ("9X16", "1X1"):                              # the centre of the window as cropped (an even offset): 0.5 to within a pixel
        assert j["beats"]["S1"][fmt]["set"] is True and abs(j["beats"]["S1"][fmt]["x"] - 0.5) < 0.003
    assert j["beats"]["S2"]["1X1"] == {"x": 0.3, "set": True} and j["beats"]["S2"]["9X16"]["x"] < 0.35 and "set" not in j["beats"]["S2"]["9X16"]
    assert len(seen) == 1                                   # S1 needed none; S2 needed its 9X16 only
    for explicit, want in (("left", 0.158), ("right", 0.842)):          # the window's centre: a 202 px window in the 640 px clip
        b = _reframe_body(formats=["9X16"])
        b["beats"][0]["face"] = explicit
        got = rf["client"].post("/reframe", json=b).json()["beats"]["S1"]["9X16"]
        assert got["set"] is True and abs(got["x"] - want) < 0.001
    b = _reframe_body(formats=["9X16"])
    b["beats"][0]["face"] = "auto"                           # auto is a suggestion, whatever the other fields
    assert "set" not in rf["client"].post("/reframe", json=b).json()["beats"]["S1"]["9X16"]


def test_reframe_shares_runs_cache_with_render(rf, monkeypatch):
    from media import reframe
    seen = []
    real = reframe.analyse
    monkeypatch.setattr(reframe, "analyse", lambda *a, **k: seen.append(a) or real(*a, **k))
    c = rf["client"]
    first = c.post("/reframe", json=_reframe_body()).json()
    assert len(seen) == 2 and len(json.loads((rf["run"] / "reframe.json").read_text())) == 4        # 2 beats x 2 formats
    assert c.post("/reframe", json=_reframe_body()).json() == first and len(seen) == 2


def test_reframe_refusals(rf):
    c = rf["client"]
    assert c.post("/reframe", json=_reframe_body(source="../../etc/passwd")).status_code == 400
    assert c.post("/reframe", json=_reframe_body(source="brand/clip.mp4")).status_code == 400
    assert c.post("/reframe", json=_reframe_body(source="runs/r2/clip.mp4")).status_code == 400
    assert c.post("/reframe", json=_reframe_body(source="runs/r1/nope.mp4")).status_code == 404
    assert c.post("/reframe", json=_reframe_body(run_id="../x")).status_code == 422
    assert c.post("/reframe", json=_reframe_body(beats=[])).status_code == 422
    assert c.post("/reframe", json=_reframe_body(beats=[{"name": "S1", "start": 0, "end": 3}] * 2)).status_code == 422       # a name twice
    assert c.post("/reframe", json=_reframe_body(beats=[{"name": f"S{i}", "start": 0, "end": 3} for i in range(13)])).status_code == 422
    assert c.post("/reframe", json=_reframe_body(formats=["16X9"])).status_code == 422
    assert c.post("/reframe", json=_reframe_body(formats=[])).status_code == 422
    r = c.post("/reframe", json=_reframe_body(beats=[{"name": "S9", "start": 1, "end": 9}]))
    assert r.status_code == 422 and "S9" in r.json()["detail"]
