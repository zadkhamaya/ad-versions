# Measured

Numbers from runs, not estimates. Each section says what ran, on what, and what it does not cover.

## Render time on the VPS (2 vCPU, 8 GB), 2026-11-22

The real media service (`POST /render` then `POST /check`, through FastAPI's test client) on the demo source clip
(1920×1080, 24 fps, 32.0 s, 37 Mbps H.264 with film grain). Stand-ins: colour-bar test clips as b-roll, a sine tone as
the voice-over (the voice job and Whisper were stubbed), a sine tone as the music bed. Everything else ran for real: the
edit plan, crops, b-roll grade, end card, captions, room tone, ducked mix, two-pass loudness, QA. One render at a time
(`MAX_RENDERS=1`, the default), `X264_THREADS=2`.

| Version | Wall time | QA |
|---|---|---|
| H2_6S_1X1 | 16 s | passed: 6.00 s, 1080×1080, 24/1, −14.0 LUFS, TP −4.2 dBTP |
| H1_15S_9X16 | 85 s | passed: 15.00 s, 1080×1920, 24/1, −14.0 LUFS, TP −5.5 dBTP |
| H1_30S_16X9 | 160 s | passed: 30.00 s, 1920×1080, 24/1, −14.0 LUFS, TP −4.5 dBTP |

- Box load average 0.2 at the start (two code-review processes were reading files during the run). Peak RSS of the whole
  run: 0.99 GB, one render at a time.
- Earlier runs on the same versions while tests ran beside them took 91 / 195 / 19 s and 113 / 200 / 26 s: contention
  costs up to 30 %.
- In the library-level run, the picture (cut, crop, grade, end card, H.264) and the finish (captions burned in, mix,
  loudness, a second H.264 encode) took about half each: 52 + 60 s for the 15 s 9:16, 106 + 93 s for the 30 s 16:9.

**What that means for 18 versions:** about 6 × 16 s + 6 × 85 s + 6 × 160 s ≈ 26 min of rendering, one at a time, plus
the voice jobs (6 distinct scripts, cached per run) and the transcriptions. The spec's "a 30 s version in about a minute"
is 2.7 min here. Two renders at once are not measured (the memory cap is the limit: 0.82–0.99 GB per render against a
1.5 GB container); measure with `docker stats` before raising `MAX_RENDERS`.

**Where the time could come down** (not tried): the picture is encoded twice at CRF 23 (once as the picture, once after
captions and mix). A fast, high-quality intermediate (e.g. `-preset ultrafast -crf 10`) for the picture and one final
encode would cut time and the generation loss below.

## What the smoke renders showed

- **Lengths, sizes, frame rate, codecs, loudness:** every render exact (format, video and audio stream durations equal),
  24/1, yuv420p, H.264 + AAC 48 kHz, −14.0 ± 0.01 LUFS, true peak −4 to −6 dBTP, loudnorm in linear mode.
- **Crop:** the S4 beat (hands on the phone) sits left of both the 9:16 band (centre 608 px) and the 1:1 band (centre
  1080 px): the centre crops show the shirt with the phone at or past the left edge. S4 needs `face: left` in the brief.
  The other beats' faces and hands sit inside the centre crops.
- **Trim:** the edit trims every source beat from its end in proportion. In the 15 s versions each beat keeps about the
  first 55 % (S1 5.5 s → 3.0 s, S2 2.7 s → 1.5 s), so a beat's closing action (S2: the phone set face down) is cut.
- **B-roll grade next to near-black beats:** a realistic stand-in (a warm, bright morning shot) graded onto the dark S2
  look went from a colour distance of 106 to 1.8 (limit 12) and reads as the same night. Colour-bar test clips went from
  109 to 22.8 and were flagged `b-roll colour`: the 0.5–2.0 gain clamp cannot pull full-range bars down to a near-black
  beat. That is the check working; real H3 clips are the test that matters.
- **Grain after the 9:16 scale-up:** the 608 px band is scaled 1.78× to 1080, which enlarges the source grain and softens
  it; the 16:9 version at 3.9 Mbps (from a 37 Mbps master) keeps less grain than the source. Both come from the double
  CRF 23 encode above; `-tune grain` and a higher final bitrate are the first things to try.
- **End card:** a one-line disclaimer at 3.5 % of the width overflowed 1080 px; the service now wraps it and shrinks the
  block to fit 90 % of the height.

## Whisper (faster-whisper small, CPU int8)

Loaded and ran once on a 3 s synthetic tone: 4.9 s including the model load (the model was already cached). Real
speech, and the time per 30 s voice-over, are not measured yet.

## The voice's speaking rate and the script word budget (VoxCPM2, 2026-11-24)

What ran: six real speak jobs through the live media container (`voice.speak`, the designed voice sample, VoxCPM2 with the fixed
params), no renders. The lines were the six voice-over lines of an earlier dry run (brand name changed to Restfield), two hooks at
6, 15 and 30 s. Length is the WAV's duration, which includes any leading or trailing silence.

| Line | Words | Voice (s) | Words/s | GPU time |
|---|---|---|---|---|
| H1 6 s | 9 | 3.20 | 2.81 | 40.1 s (cold start) |
| H1 15 s | 18 | 6.08 | 2.96 | 7.5 s |
| H1 30 s | 37 | 12.48 | 2.97 | 12.3 s |
| H2 6 s | 8 | 3.68 | 2.17 | 6.2 s |
| H2 15 s | 21 | 7.04 | 2.98 | 8.8 s |
| H2 30 s | 35 | 12.48 | 2.80 | 12.2 s |

Together 128 words in 44.96 s: **2.85 words per second** (2.8 to 3.0 on the longer lines; the 6 s lines scatter more, 2.2 to 2.8,
because a pause or a silent edge weighs more in 3 s). The voice speaks fast: the old budget (at most 9, 28 and 60 words) left the
15 s ad with 6 s of voice.

**Formula.** The edit ends the voice-over's slot where the end card begins (`endcard_s` = 1.5 s in `config/edit.yaml`); the voice
should end half a second before that, so

target words = rate x (length - endcard_s - 0.5 s), "too long" past rate x (length - endcard_s)

| Length | Voice should fill | Target words | Prompt range | "May not fit" note past |
|---|---|---|---|---|
| 6 s | 4.0 s | 11.4 | 10 to 11 | 13 words (4.5 s) |
| 15 s | 13.0 s | 37.1 | 32 to 37 | 38 words (13.5 s) |
| 30 s | 28.0 s | 79.8 | 70 to 80 | 81 words (28.5 s) |

The range's top is the target; its bottom is about 15 % under it (a line a little short only leaves a calm gap before the end
card). Both are in the workflow (`Scripts: prompt`, `Scripts: validate`; `tests/test_repo_files.py` checks they agree with this
formula). Not covered: other voice samples or seeds, other text (numbers, long words) and the rate with the real LLM lines; re-measure
if the voice sample or model changes, and check the first real run's `vo_s` against these slots.
