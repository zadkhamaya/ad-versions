import subprocess

import pytest
from conftest import has_filter, ff
from media.ffmpeg import finish
from media.qa import loudness as loud

needs_ass = pytest.mark.skipif(not has_filter("subtitles"), reason="ffmpeg built without libass")
ASS = "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n"


def probe(p, entries, stream):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", stream, "-show_entries", entries, "-of", "csv=p=0", str(p)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return out


def _inputs(tmp_path):
    pic = tmp_path / "pic.mp4"
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=24:duration=6", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(pic))
    vo = tmp_path / "vo.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=600:duration=4", str(vo))
    ass = tmp_path / "c.ass"
    ass.write_text(ASS)
    return pic, vo, ass


@needs_ass
def test_finish_hits_loudness(media, tmp_path):
    pic, vo, ass = _inputs(tmp_path)
    out = tmp_path / "out.mp4"
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out))
    i, tp = loud(out)
    assert abs(i - (-14.0)) <= 1.0 and tp <= -1.0
    assert abs(float(probe(out, "format=duration", "v:0")) - 6.0) < 0.1
    assert probe(out, "stream=codec_name,sample_rate", "a:0") == "aac,48000"


@needs_ass
def test_finish_with_room_tone_hits_loudness(media, tmp_path):
    pic, vo, ass = _inputs(tmp_path)
    room = tmp_path / "room.wav"
    ff("-f", "lavfi", "-i", "anoisesrc=color=pink:amplitude=0.3:duration=6", str(room))
    out = tmp_path / "out_room.mp4"
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out), room=str(room))
    i, tp = loud(out)
    assert abs(i - (-14.0)) <= 1.0 and tp <= -1.0


def band_db(p, hz, start=0.0, end=None):
    """Mean level (dB) of a narrow band around hz between start and end seconds (three cascaded bandpasses)."""
    win = f"atrim=start={start}" + (f":end={end}" if end is not None else "") + ",asetpts=PTS-STARTPTS,"
    bp = ",".join([f"bandpass=f={hz}:width_type=h:w=60"] * 3)
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(p), "-af", f"{win}{bp},volumedetect", "-f", "null", "-"],
                       capture_output=True, text=True)
    return float(r.stderr.split("mean_volume:")[1].split("dB")[0])


def audio_duration(p):
    return float(subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=duration",
                                 "-of", "csv=p=0", str(p)], capture_output=True, text=True, check=True).stdout)


def _room_inputs(tmp_path):
    pic, _, ass = _inputs(tmp_path)
    vo = tmp_path / "vo_gap.wav"                       # 3 s of voice tone, then 1 s of silence
    ff("-f", "lavfi", "-i", "sine=frequency=600:duration=3", "-af", "apad=pad_dur=1", str(vo))
    room = tmp_path / "room.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=1000:duration=6", "-af", "volume=-12dB", str(room))   # a tone no other input carries
    return pic, vo, ass, room


@needs_ass
def test_room_tone_is_in_the_mix_only_when_given(media, tmp_path):
    pic, vo, ass, room = _room_inputs(tmp_path)
    with_room, without = tmp_path / "a.mp4", tmp_path / "b.mp4"
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(with_room), room=str(room))
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(without))
    assert band_db(with_room, 1000, 4.5, 5.9) - band_db(without, 1000, 4.5, 5.9) > 12


@needs_ass
def test_room_tone_sits_30_db_under_the_voice(media, tmp_path):
    pic, vo, ass, room = _room_inputs(tmp_path)
    out = tmp_path / "a.mp4"
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out), room=str(room))
    voice_level = band_db(out, 600, 0.3, 2.7)          # voice tone, while the voice speaks
    room_level = band_db(out, 1000, 4.5, 5.9)          # room tone alone, after the voice
    assert abs((voice_level - room_level) - 30) <= 3


@needs_ass
def test_audio_runs_the_pictures_length_and_music_stays_to_the_end(media, tmp_path):
    pic, vo, ass = _inputs(tmp_path)                  # picture 6 s, voice 4 s
    out = tmp_path / "out.mp4"
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out))
    assert abs(audio_duration(out) - 6.0) <= 0.1
    assert band_db(out, 220, 5.0, 5.9) > -55           # the 220 Hz music bed is still playing in the last second


def _peaky_voice(tmp_path):
    vo = tmp_path / "peaky.wav"                        # quiet tone with short loud clicks: peak far above the loudness
    ff("-f", "lavfi", "-i", "aevalsrc=0.1*sin(2*PI*600*t)+if(lt(mod(t\\,0.1)\\,0.0005)\\,1.0\\,0):s=48000:d=4:c=stereo", str(vo))
    return vo


@needs_ass
def test_peaky_voice_stays_linear_and_under_the_true_peak(media, tmp_path):
    pic, _, ass = _inputs(tmp_path)
    vo = _peaky_voice(tmp_path)
    out = tmp_path / "out.mp4"
    log = finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out))
    i, tp = loud(out)
    assert tp <= -1.0 and abs(i - (-14.0)) <= 1.0
    assert log["mode"] == "linear"
    assert log["output"]["tp"] <= -1.0


@needs_ass
def test_caption_path_with_filter_special_characters(media, tmp_path):
    pic, vo, _ = _inputs(tmp_path)
    d = tmp_path / "a:b,c's"
    d.mkdir()
    ass = d / "c.ass"
    ass.write_text(ASS)
    out = tmp_path / "out_sp.mp4"
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out))
    assert out.stat().st_size > 0


@needs_ass
def test_finish_threads_cap_is_opt_in(media, tmp_path):
    pic, vo, ass = _inputs(tmp_path)
    plain = finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(tmp_path / "a.mp4"))
    capped = finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(tmp_path / "b.mp4"), threads=1)
    enc = lambda log: [c for c in log["cmds"] if "libx264" in c]
    assert all("-threads" not in c for c in enc(plain))
    assert all(c[c.index("-threads") + 1] == "1" for c in enc(capped))


def _mono_voice_stereo_room(tmp_path):
    pic, _, ass = _inputs(tmp_path)
    vo = tmp_path / "vo_mono.wav"                      # a MONO voice (what the voice worker sends) over a STEREO room tone
    ff("-f", "lavfi", "-i", "sine=frequency=600:duration=3", "-af", "apad=pad_dur=1", "-ac", "1", str(vo))
    room = tmp_path / "room_st.wav"
    ff("-f", "lavfi", "-i", "sine=frequency=1000:duration=6", "-af", "volume=-12dB", "-ac", "2", str(room))
    return pic, vo, ass, room


@needs_ass
def test_a_mono_voice_still_leaves_the_room_30_db_under(media, tmp_path):
    pic, vo, ass, room = _mono_voice_stereo_room(tmp_path)
    out = tmp_path / "a.mp4"
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out), room=str(room))
    assert abs((band_db(out, 600, 0.3, 2.7) - band_db(out, 1000, 4.5, 5.9)) - 30) <= 1.5


@needs_ass
def test_a_silent_voice_says_why_the_room_tone_is_gone(media, tmp_path):
    pic, _, ass, room = _mono_voice_stereo_room(tmp_path)
    vo = tmp_path / "silent.wav"
    ff("-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono", "-t", "4", str(vo))
    log = finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(tmp_path / "a.mp4"), room=str(room))
    assert log["room"] == "skipped: silent voice"
    log = finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(tmp_path / "b.mp4"))
    assert log["room"] == "not given"


@needs_ass
def test_the_limiter_does_not_pump_on_a_peaky_voice(media, tmp_path):
    pic, _, ass = _inputs(tmp_path)
    log = finish(str(pic), str(_peaky_voice(tmp_path)), str(media / "music.m4a"), str(ass), str(tmp_path / "a.mp4"))
    cmd = next(c for c in log["cmds"] if "libx264" in c)
    assert "alimiter" in " ".join(cmd) and "attack=5:release=150" in " ".join(cmd)


@needs_ass
def test_a_good_mix_logs_the_room_and_every_documented_key(media, tmp_path):
    pic, vo, ass, room = _mono_voice_stereo_room(tmp_path)
    log = finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(tmp_path / "a.mp4"), room=str(room))
    assert log["room"] == "mixed"
    assert set(log) == {"cmds", "measured", "mode", "limiter_db", "tp_target", "output", "room", "attempts", "history"}
