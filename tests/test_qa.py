import subprocess

import pytest
from conftest import ff, has_filter
from media import qa, sheet
from media.ffmpeg import finish

needs_ass = pytest.mark.skipif(not has_filter("subtitles"), reason="ffmpeg built without libass")
ASS = "[Script Info]\nPlayResX: 1080\nPlayResY: 1920\n"
WANT = {"length_s": 6, "fmt": "9X16"}


def _make(media, d, name, size="1080x1920", dur=6, loud=-14.0, vo_dur=4):
    d.mkdir(exist_ok=True)
    pic = d / f"pic_{name}"
    ff("-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration={dur}", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(pic))
    vo = d / "vo.wav"
    ff("-f", "lavfi", "-i", f"sine=frequency=600:duration={vo_dur}", str(vo))
    ass = d / "c.ass"
    ass.write_text(ASS)
    out = d / name
    finish(str(pic), str(vo), str(media / "music.m4a"), str(ass), str(out), loud=loud)
    return out


@pytest.fixture(scope="module")
def good(media, tmp_path_factory):
    if not has_filter("subtitles"):
        pytest.skip("ffmpeg built without libass")
    return _make(media, tmp_path_factory.mktemp("qa_good"), "good.mp4")


def test_correct_file_passes(good):
    r = qa.check(str(good), WANT)
    assert r["passed"] is True and r["reasons"] == []
    assert abs(r["measured"]["lufs"] - (-14.0)) <= 1.0 and r["measured"]["tp"] <= -1.0
    assert r["measured"]["size"] == "1080x1920" and r["measured"]["fps"] == "24/1"


@needs_ass
def test_wrong_length_fails(media, tmp_path):
    f = _make(media, tmp_path, "long.mp4", dur=6.5)
    r = qa.check(str(f), WANT)
    assert not r["passed"] and any(x.startswith("length 6.50 s (want 6 ± 0.1)") for x in r["reasons"])


@needs_ass
def test_wrong_size_fails(media, tmp_path):
    f = _make(media, tmp_path, "sq.mp4", size="1080x1080")
    r = qa.check(str(f), WANT)
    assert not r["passed"] and "size 1080x1080 (want 1080x1920)" in r["reasons"]


def test_wrong_frame_rate_fails(good, tmp_path):
    f = tmp_path / "r30.mp4"
    ff("-i", str(good), "-r", "30", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "copy", str(f))
    r = qa.check(str(f), WANT)
    assert not r["passed"] and "frame rate 30/1 (want 24/1)" in r["reasons"]


def test_wrong_codec_fails(good, tmp_path):
    f = tmp_path / "hevc.mp4"
    ff("-i", str(good), "-c:v", "libx265", "-pix_fmt", "yuv420p", "-c:a", "copy", str(f))
    r = qa.check(str(f), WANT)
    assert not r["passed"] and "codec hevc/aac (want h264/aac)" in r["reasons"]


def test_wrong_pixel_format_fails(good, tmp_path):
    f = tmp_path / "444.mp4"
    ff("-i", str(good), "-c:v", "libx264", "-pix_fmt", "yuv444p", "-c:a", "copy", str(f))
    r = qa.check(str(f), WANT)
    assert not r["passed"] and any(x.startswith("pixel format yuv444p (want yuv420p)") for x in r["reasons"])


def test_loud_file_fails_on_loudness(media, tmp_path):
    if not has_filter("subtitles"):
        pytest.skip("ffmpeg built without libass")
    f = _make(media, tmp_path, "loud.mp4", loud=-8.0)
    r = qa.check(str(f), WANT)
    assert not r["passed"] and any(x.startswith("loudness ") and "(want -14 ± 1)" in x for x in r["reasons"])


def test_short_audio_stream_fails(good, tmp_path):
    f = tmp_path / "shortaudio.mp4"                    # video stays 6 s, audio cut to 4 s
    ff("-i", str(good), "-c:v", "copy", "-af", "atrim=duration=4", "-c:a", "aac", str(f))
    r = qa.check(str(f), WANT)
    assert not r["passed"] and any(x.startswith("audio 4.0") and "(want 6 ± 0.1)" in x for x in r["reasons"])


def test_short_video_stream_fails(tmp_path):
    f = tmp_path / "shortvideo.mp4"                    # video 5 s, audio 6 s: the container reads 6 s, the picture lost its end
    ff("-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=24:duration=5", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
       "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(f))
    r = qa.check(str(f), WANT)
    assert not r["passed"] and any(x.startswith("video 5.0") and "(want 6 ± 0.1)" in x for x in r["reasons"])
    assert not any(x.startswith(("length", "audio ")) for x in r["reasons"])


def test_missing_audio_stream_fails(good, tmp_path):
    f = tmp_path / "silent.mp4"
    ff("-i", str(good), "-c:v", "copy", "-an", str(f))
    r = qa.check(str(f), WANT)
    assert not r["passed"] and any("audio stream" in x for x in r["reasons"])


def test_unreadable_file_fails_with_a_reason(tmp_path):
    f = tmp_path / "bad.mp4"
    f.write_bytes(b"not a video")
    r = qa.check(str(f), WANT)
    assert not r["passed"] and r["reasons"]


@pytest.mark.parametrize("script,heard", [
    ("Support 24/7 for your sleep", "support twenty four seven for your sleep"),
    ("Order by 9am", "Order by 9 a.m."),
    ("Order by 9 PM tonight", "order by nine p.m. tonight"),
    ("Don't wait", "Do not wait"),
    ("Save 50%", "save 50 percent"),
    ("Save fifty percent", "save 50%"),
    ("We'll ship it, it's free", "we will ship it it is free"),
    ("A one-time offer", "a one time offer"),
    ("Over 3.5 million sold", "over three point five million sold"),
    ("Rest well & wake up", "rest well and wake up"),
])
def test_script_diff_ignores_how_numbers_and_abbreviations_are_spoken(script, heard):
    assert qa.script_diff(script, heard) == 0.0


def test_script_diff_still_counts_real_errors():
    assert qa.script_diff("Save 50% today", "save 15 percent today") > 0.15


def test_script_diff():
    assert qa.script_diff("sleep better tonight", "sleep better tonight") == 0.0
    assert qa.script_diff("sleep better tonight", "sleep tonight") > 0.3
    assert qa.script_diff("Sleep better, tonight!", "sleep better tonight") == 0.0
    assert qa.script_diff("", "") == 0.0 and qa.script_diff("", "word") == 1.0
    assert qa.script_diff("a b", "x y z q") == 1.0                 # capped at 1


def test_sheet_of_mixed_aspects_is_a_jpeg(media, tmp_path):
    files = []
    for name, size in (("A_9X16.mp4", "1080x1920"), ("B_1X1.mp4", "1080x1080"), ("C_16X9.mp4", "1920x1080"),
                       ("it's:a,b%{x}[c];d=e.mp4", "640x360")):      # the last name is full of drawtext/filtergraph characters
        p = tmp_path / name
        ff("-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
        files.append(str(p))
    out = tmp_path / "sheet.jpg"
    sheet.make(files, str(out))
    data = out.read_bytes()
    assert data[:2] == b"\xff\xd8" and len(data) >= 10_000


def test_sheet_of_frames_with_odd_pixel_aspect_still_tiles(tmp_path):
    # 1344x768 scales to 320x182.9: the pad inherits a SAR of 1280:1281 and concat refused to join it with a 1:1 cell
    files = []
    for name, size in (("odd.mp4", "1344x768"), ("tall.mp4", "768x1344")):
        p = tmp_path / name
        ff("-f", "lavfi", "-i", f"testsrc2=size={size}:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
        files.append(str(p))
    out = tmp_path / "sheet.jpg"
    sheet.make(files, str(out))
    assert out.read_bytes()[:2] == b"\xff\xd8"


def _cell_gray(jpg, col, row, w=160, h=40, cell=320):
    """Raw gray bytes of a w x h window at the centre of a sheet cell."""
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(jpg), "-vf", f"crop={w}:{h}:{col * cell + (cell - w) // 2}:{row * cell + (cell - h) // 2}",
                        "-f", "rawvideo", "-pix_fmt", "gray", "-"], capture_output=True, check=True)
    return r.stdout


def test_sheet_survives_bad_files_and_keeps_every_cell(media, tmp_path):
    good = []
    for n in (1, 2):
        p = tmp_path / f"G{n}.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
        good.append(p)
    garbage = tmp_path / "garbage.mp4"
    garbage.write_bytes(b"not a video")
    truncated = tmp_path / "truncated.mp4"
    truncated.write_bytes(good[0].read_bytes()[:3000])            # no moov atom: ffprobe fails or no frame comes out
    audio_only = tmp_path / "audio_only.mp4"
    ff("-f", "lavfi", "-i", "sine=duration=2", "-c:a", "aac", str(audio_only))
    files = [str(good[0]), str(garbage), str(truncated), str(audio_only), str(good[1])]
    out = tmp_path / "sheet.jpg"
    sheet.make(files, str(out))
    assert max(_cell_gray(out, 0, 0, 160, 160)) > 100             # cell 1: a real frame
    for col in (1, 2, 3):                                           # the bad files: a placeholder with text, not a black gap
        px = _cell_gray(out, col, 0)
        assert max(px) > 200 and sum(px) / len(px) < 60
    assert sum(_cell_gray(out, 4, 0, 160, 160)) / (160 * 160) > 40    # cell 5 (after the bad ones) is still there


def test_sheet_needs_at_least_one_file(tmp_path):
    with pytest.raises(ValueError):
        sheet.make([], str(tmp_path / "s.jpg"))


@pytest.mark.parametrize("script,heard", [
    ("It's calm", "its calm"),                        # a transcriber's homophone: one word, not two edits
    ("We'll sleep well", "well sleep well"),
    ("We're here", "were here"),
    ("Don't wait", "dont wait"),
    ("You can't miss it", "you cant miss it"),
    ("It's a plan", "It is a plan"),
    ("They won't mind", "they will not mind"),
    ("I'm sure, she's sure", "i am sure she is sure"),
    ("Let's rest", "lets rest"),
    ("Isn't it", "is not it"),
])
def test_contraction_homophones_cost_nothing(script, heard):
    assert qa.script_diff(script, heard) == 0.0


@pytest.mark.parametrize("script,heard", [
    ("You can rest", "you can't rest"),                # can vs can't is a real error
    ("Sleep better tonight", "sleep tonight"),
    ("Save 50% today", "save 15 percent today"),
])
def test_real_errors_still_count(script, heard):
    assert qa.script_diff(script, heard) > 0.0


def test_a_unicode_digit_does_not_raise():
    assert qa.script_diff("area 10² only", "area ten only") > 0.0       # "²" is isdigit() but int("²") raises
    assert qa.script_diff("٣ nights", "٣ nights") == 0.0


@pytest.mark.parametrize("script,heard", [
    ("It is not hard", "It's not hard"),
    ("You are not alone", "You're not alone"),
    ("They will not mind", "they won't mind"),
    ("We do not wait", "we don't wait"),
])
def test_pronoun_plus_not_meets_its_contraction(script, heard):
    assert qa.script_diff(script, heard) == 0.0 and qa.script_diff(heard, script) == 0.0


MATCHING = [
    ("It is not hard", "It's not hard"), ("It is not hard", "It isn't hard"), ("You are not alone", "You're not alone"),
    ("You are not alone", "You aren't alone"), ("I have not slept", "I haven't slept"), ("I have not slept", "I've not slept"),
    ("He has not", "He hasn't"), ("That is not", "That isn't"), ("They would not", "They wouldn't"),
    ("It's calm", "its calm"), ("We'll sleep", "well sleep"), ("We're here", "were here"), ("Don't wait", "do not wait"),
    ("Don't wait", "dont wait"), ("They won't mind", "they will not mind"), ("They won't mind", "they wont mind"),
    ("You can't miss it", "you cannot miss it"), ("You can't miss it", "you can not miss it"), ("You can't miss it", "you cant miss it"),
    ("I'm sure", "im sure"), ("You'll see", "youll see"), ("We weren't done", "we werent done"), ("We weren't done", "we were not done"),
    ("I'd go, I'll go, he'll go", "id go ill go hell go"), ("She'd stay", "she would stay"), ("They're late", "theyre late"),
    ("Let's rest", "lets rest"), ("Support 24/7", "support twenty four seven"), ("Order by 9am", "order by 9 a.m."), ("Save 50%", "save 50 percent"),
    ("Sleep well tonight", "sleep well tonight"), ("You were right", "You were right"),
    ("He has gone", "He's gone"), ("What has changed", "What's changed"), ("We had left", "We'd left"),
    ("I had better go", "I'd better go"), ("She has got it", "She's got it"), ("It has been a long day", "It's been a long day"),
]


@pytest.mark.parametrize("script,heard", MATCHING)
def test_contraction_matrix_is_zero_both_ways(script, heard):
    assert qa.script_diff(script, heard) == 0.0 and qa.script_diff(heard, script) == 0.0


@pytest.mark.parametrize("script,heard", [
    ("You can rest", "you can't rest"), ("It is calm", "it isn't calm"), ("We do wait", "we don't wait"), ("They will go", "they won't go"),
    ("We are not done", "We weren't done"), ("We were not done", "we are not done"), ("Sleep better tonight now", "sleep tonight now"),
    ("Save 50% today", "save 15 percent today"), ("He has gone", "He is going"), ("We had left", "We have left"),
])
def test_contraction_real_errors_still_fail(script, heard):
    assert qa.script_diff(script, heard) > 0.15 and qa.script_diff(heard, script) > 0.15


def _px(jpg, x, y):
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(jpg), "-vf", f"crop=1:1:{x}:{y}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
                       capture_output=True, check=True)
    return tuple(r.stdout)


def test_sheet_marks_pass_green_and_fail_red_on_the_cell_edge(media, tmp_path):
    files = []
    for n in (1, 2, 3):
        p = tmp_path / f"V{n}.mp4"
        ff("-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p))
        files.append(str(p))
    files.append(str(tmp_path / "missing.mp4"))                       # a failed render: the placeholder carries its mark too
    out = tmp_path / "sheet.jpg"
    sheet.make(files, str(out), status=["pass", "fail", None, "fail"])
    edge = lambda col: _px(out, col * 320 + 2, 160)
    r, g, b = edge(0); assert g > 140 and r < 110 and b < 110          # pass: green
    r, g, b = edge(1); assert r > 140 and g < 110 and b < 110          # fail: red
    r, g, b = edge(3); assert r > 140 and g < 110 and b < 110          # the placeholder cell too
    assert not (edge(2)[1] > 140 and edge(2)[0] < 110)                  # unmarked: no outline


def test_sheet_status_must_match_the_files(tmp_path):
    with pytest.raises(ValueError):
        sheet.make(["a.mp4", "b.mp4"], str(tmp_path / "s.jpg"), status=["pass"])


# ---- hotwords: a split one-word name in the transcript counts as one word ----

def test_a_hotword_heard_as_two_words_is_one_word():
    assert qa.script_diff("Restfield helps", "rest field helps", hotwords="Restfield") == 0.0
    assert qa.script_diff("Restfield helps", "Rest-Field helps", hotwords="restfield") == 0.0
    assert qa.script_diff("Restfield helps", "re stf ield helps", hotwords="Restfield") == 0.0


def test_without_hotwords_a_split_name_is_still_an_error():
    assert qa.script_diff("Restfield helps", "rest field helps") > 0.3
    assert qa.script_diff("Restfield helps", "rest field helps", hotwords=None) > 0.3
    assert qa.script_diff("Restfield helps", "rest field helps", hotwords="Calmora") > 0.3      # another name does not join it


def test_hotword_joining_does_not_hide_real_errors():
    assert qa.script_diff("Restfield helps you sleep", "rest field helps you", hotwords="Restfield") == 0.25
    assert qa.script_diff("Restfield helps", "rest helps", hotwords="Restfield") == 0.5


@pytest.mark.parametrize("heard", ["restfield 500 helps", "rest 500 field helps", "500 restfield helps",
                                   "restfield \u3054\u8996\u8074 helps", "rest 7 field helps"])
def test_a_token_without_letters_is_never_swallowed_by_a_hotword(heard):
    assert qa.script_diff("Restfield helps", heard, hotwords="Restfield") == qa.script_diff("Restfield helps", heard)
    assert qa.script_diff("Restfield helps", heard, hotwords="Restfield") > 0


def test_hotwords_are_a_comma_or_line_list():
    assert qa.script_diff("Restfield helps", "rest field helps", hotwords="Calmora, Restfield") == 0.0
    assert qa.script_diff("Try Restfield", "try rest field", hotwords="Calmora, Restfield\nOther") == 0.0


def test_a_multi_word_hotword_changes_nothing():
    script = "Call Online sleep clinic now"
    assert qa.script_diff(script, "call online sleep clinics now", hotwords="Online sleep clinic, Restfield") == \
        qa.script_diff(script, "call online sleep clinics now") == 0.2                           # one slip stays one slip
    assert qa.script_diff(script, "call online sleep clinic now", hotwords="Online sleep clinic") == 0.0
    assert qa.script_diff("Restfield helps", "rest field helps", hotwords="Rest field") > 0.3    # a two-word hotword is no joiner


def test_the_script_is_never_changed_by_hotwords():
    assert qa.script_diff("Rest field helps", "restfield helps", hotwords="Restfield") == \
        qa.script_diff("Rest field helps", "restfield helps") > 0
    assert qa.script_diff("rest field helps", "rest field helps", hotwords="Restfield") == 0.0
