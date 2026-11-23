from media.captions import to_ass

W = [{"w": "Sleep", "start": 0.0, "end": 0.4}, {"w": "better", "start": 0.4, "end": 0.8},
     {"w": "starting", "start": 0.8, "end": 1.3}, {"w": "tonight", "start": 1.3, "end": 1.9}]


def test_groups_and_margins():
    ass = to_ass(W, "9X16")
    lines = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(lines) == 2 and "Sleep better starting" in lines[0] and "tonight" in lines[1]
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass and ",538," in ass   # MarginV = round(0.28 * 1920)


def test_wide_margin():
    assert ",130," in to_ass(W, "16X9")  # round(0.12 * 1080)


def test_style_and_timing():
    ass = to_ass(W, "9X16")
    style = next(l for l in ass.splitlines() if l.startswith("Style:"))
    f = style.split(":", 1)[1].split(",")
    assert f[1] == "DejaVu Sans" and f[2] == "65"            # 6 % of 1080
    assert f[3] == "&H00FFFFFF" and f[5] == "&H00000000"     # white text, black outline
    assert f[7] == "-1" and f[16] == "3" and f[18] == "2"    # bold, outline 3, bottom centre
    first = next(l for l in ass.splitlines() if l.startswith("Dialogue:"))
    assert first.startswith("Dialogue: 0,0:00:00.00,0:00:01.30,")
    assert to_ass(W, "16X9").count("\nStyle:") == 1 and ",77," in to_ass(W, "16X9")  # 4 % of 1920


def test_empty_words_gives_header_only():
    assert "Dialogue:" not in to_ass([], "1X1")


def test_word_text_cannot_inject_ass_markup():
    words = [{"w": "{\\an8}Hi", "start": 0, "end": 0.3}, {"w": "a\\Nb", "start": 0.3, "end": 0.6},
             {"w": "line\nbreak", "start": 0.6, "end": 0.9}, {"w": "{}", "start": 0.9, "end": 1.0}]
    ass = to_ass(words, "9X16")
    lines = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(lines) == 1
    text = lines[0].split(",", 9)[9]
    assert "{" not in text and "}" not in text and "\\" not in text
    assert text == "an8Hi aNb line break"


def _heard(*pairs):
    return [{"w": w, "start": s, "end": e} for w, s, e in pairs]


def test_script_words_take_the_timing_of_the_words_heard():
    from media.captions import from_script
    words, wer = from_script("Sleep better, tonight.", _heard(("Sleep", 0.2, 0.6), ("better", 0.7, 1.2), ("tonight", 1.3, 2.0)))
    assert wer == 0.0
    assert words == [{"w": "Sleep", "start": 0.2, "end": 0.6}, {"w": "better,", "start": 0.7, "end": 1.2}, {"w": "tonight.", "start": 1.3, "end": 2.0}]


def test_a_misheard_word_shows_the_scripts_word():
    from media.captions import from_script
    script = "A plan built around your routine and your sleep"
    heard = _heard(("A", 0, .2), ("plan", .2, .5), ("built", .5, .8), ("around", .8, 1.1), ("your", 1.1, 1.3), ("routine", 1.3, 1.8),
                   ("in", 1.8, 1.9), ("your", 1.9, 2.1), ("sleep", 2.1, 2.5))                     # "and" heard as "in"
    words, wer = from_script(script, heard)
    assert [w["w"] for w in words] == script.split() and 0 < wer < 0.3
    assert words[6] == {"w": "and", "start": 1.8, "end": 1.9}                                      # the script's word, Whisper's slot
    assert "in" not in [w["w"] for w in words]


def test_a_word_the_transcript_dropped_is_spread_over_its_gap():
    from media.captions import from_script
    heard = _heard(("Talk", 0, .4), ("clinician", 1.0, 1.6), ("from", 1.6, 2.0), ("home", 2.0, 2.5), ("any", 2.5, 2.8),
                   ("night", 2.8, 3.1), ("you", 3.1, 3.3), ("like", 3.3, 3.6))
    words, wer = from_script("Talk to a clinician from home any night you like", heard)
    assert wer == 0.2 and [w["w"] for w in words][:4] == ["Talk", "to", "a", "clinician"]
    gap = words[1:3]                                                                              # two dropped words between 0.4 and 1.0
    assert abs(gap[0]["start"] - 0.4) < 1e-9 and abs(gap[-1]["end"] - 1.0) < 1e-9 and abs(gap[0]["end"] - 0.7) < 1e-9
    assert all(abs(a["end"] - b["start"]) < 1e-9 for a, b in zip(gap, gap[1:]))


def test_extra_words_heard_are_left_out_and_numbers_align_across_spellings():
    from media.captions import from_script
    words, wer = from_script("Support 24/7 for your sleep", _heard(("Support", 0, .5), ("twenty", .5, .8), ("four", .8, 1.0), ("seven", 1.0, 1.3),
                                                                   ("um", 1.3, 1.4), ("for", 1.4, 1.6), ("your", 1.6, 1.8), ("sleep", 1.8, 2.2)))
    assert wer < 0.2 and [w["w"] for w in words] == ["Support", "24/7", "for", "your", "sleep"]       # "um" costs one insertion
    assert words[1]["start"] == 0.5 and words[1]["end"] == 1.3


def test_a_transcript_far_from_the_script_gives_none():
    from media.captions import from_script
    words, wer = from_script("Sleep better tonight", _heard(("Buy", 0, .3), ("now", .3, .6), ("or", .6, .9), ("never", .9, 1.2)))
    assert words is None and wer > 0.3
    assert from_script("Sleep better", [])[0] is None
    assert from_script("", _heard(("x", 0, 1)))[0] is None


def test_timings_never_run_backwards():
    from media.captions import from_script
    heard = _heard(("a", 0, .3), ("b", .3, .6), ("c", .6, .9), ("d", .9, 1.2), ("e", 1.2, 1.5), ("six", 2.4, 2.7), ("seven", 2.7, 3.0),
                   ("eight", 3.0, 3.3))
    words, wer = from_script("a b c d e six one seven eight", heard)
    assert words is not None and wer < 0.3
    ts = [t for w in words for t in (w["start"], w["end"])]
    assert ts == sorted(ts)


def test_a_tokenless_script_word_rides_on_its_neighbour():
    from media.captions import from_script
    heard = _heard(("Sleep", 0, .4), ("better", .4, .8), ("tonight", .8, 1.2), ("with", 1.2, 1.4), ("us", 1.4, 1.6))
    words, wer = from_script("Sleep better — tonight with us", heard)
    assert wer == 0.0 and [w["w"] for w in words] == ["Sleep", "better —", "tonight", "with", "us"]
    assert words[1] == {"w": "better —", "start": 0.4, "end": 0.8}
    words, _ = from_script("— Sleep better tonight with us", heard)                       # first word: waits for the next one
    assert [w["w"] for w in words][:2] == ["— Sleep", "better"] and words[0]["start"] == 0
