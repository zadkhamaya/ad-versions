# Media service API

The media service (`media/server.py`, FastAPI) is what n8n calls over the private Docker network. JSON in, JSON out.
Every endpoint is below with one example. Paths in a request are always relative to the data root (`/data` in the
container) and must sit inside `runs/<run_id>/`; the only exception is a render's `music` and `logo`, which may also come
from `brand/` (read-only).

## Files and names

| What | Where |
|---|---|
| Run folder | `runs/<run_id>/` (`run_id`: letters, digits, `-`, `_`, 1 to 64 characters) |
| Finished version | `runs/<run_id>/renders/<version>.mp4`, e.g. `renders/H1_15S_9X16.mp4` |
| Version id | `<hook>_<length>S_<format>`: hook `H1`/`H2`, length `6`/`15`/`30`, format `9X16`/`1X1`/`16X9` (18 in all) |
| B-roll | `runs/<run_id>/broll/<clip>_<w>x<h>.mp4`: `<clip>_1344x768.mp4` (wide, for 16X9 and 1X1) and `<clip>_768x1344.mp4` (tall, for 9X16) |
| Cached voice-over | `runs/<run_id>/vo/<hash>.wav` (one per script; the 18 versions share 6) |
| Frames, sheets | `runs/<run_id>/frames/<clip>_<hash>_<tag>.jpg`; a sheet goes where `out` says |
| Package | `runs/<run_id>/package/` (the files renamed by the naming convention, `manifest.json`, `manifest.csv`, `report.md`, `index.html`) |
| Brand inputs | `brand/music.m4a` (the music bed) and `brand/logo.png` (optional) under the data root |

## Errors

| Status | Body | When |
|---|---|---|
| 400 | `{"detail": "path ... is outside runs/"}` | a path outside the run, absolute, or from another run |
| 404 | `{"detail": "no such file: ..."}` | an input file does not exist |
| 409 | `{"detail": "a package for run ... is already being built"}` | a second `/package` for a run that is still building one |
| 422 | `{"detail": ...}` | a bad request (`detail` is a string, or FastAPI's list of `{type, loc, msg}`), or a refusal before any work: beats too short for the edit, a disclaimer that cannot fit the end card, a file without audio |
| 500 | `{"error": "<Type>: <message>"}` | any failure while working. No traceback, no absolute paths. `/render` also sends `"log"` (what it had done), `/package` sends `"folder"` |

The service stays up after a 500. n8n's error branch reads `error`.

## Timeouts

One render at a time by default (`MAX_RENDERS=1`): a second request waits inside the service for the first. Measured on a
2-vCPU box (see `docs/measured.md`) a 6 s version takes about 16 s, a 15 s one about 85 s, a 30 s 16:9 one about 160 s, so
18 versions take about 26 minutes in a row. Set n8n's `/render` timeout to at least 15 minutes, and send renders one at a
time (or raise the timeout to the length of the queue). `/transcribe` takes the same slot. The other endpoints answer in
seconds; `/health` never waits.

## POST /render

Renders one version and writes `renders/<version>.mp4` (a failed or refused render removes any older file of that name).
Fields: `run_id`, `version`, `source` (the clip), `beats` (`name` S1 to S6, `start`, `end` in seconds, `role`, `face`
`left`/`centre`/`center`/`right`), `broll` (`{"slot1": {"wide": path, "tall": path}}`; a missing slot drops that cutaway),
`vo_text` (the approved voice-over script, also what the captions show), `disclaimer` (burned into the end card), `music`,
optional `logo` and `brand`.

```json
{"run_id": "r1", "version": "H1_15S_9X16", "source": "runs/r1/source.mp4",
 "beats": [{"name": "S1", "start": 0.2, "end": 4.8, "role": "opener-H1", "face": "centre"}],
 "broll": {"slot1": {"wide": "runs/r1/broll/A_1344x768.mp4", "tall": "runs/r1/broll/A_768x1344.mp4"}},
 "vo_text": "Sleep better tonight.", "disclaimer": "A clinician assesses every request.",
 "music": "brand/music.m4a", "logo": "brand/logo.png"}
```

Answer: `{"file": "runs/r1/renders/H1_15S_9X16.mp4", "log": {...}}`. The log:

- `reasons`: strings for problems that do not stop the render and should fail QA: `b-roll colour`,
  `voice-over X.X s, longer than the Y.Y s before the end card`, `captions from transcript (script alignment failed)`.
- `vo_s`, `vo_cached` (true when the voice came from this run's cache), `vo_execution_ms` (the voice job's GPU time, only
  when a job ran and RunPod reported it), `caption_wer` (script against what Whisper heard).
- `segments`, `broll_match` (colour distance before and after the grade), `endcard`, `finish` (loudness measured,
  limiter, room tone: `mixed` or `skipped: ...`, the ffmpeg commands).

## POST /check

Technical QA of a finished file. Give `version` (`H1_15S_9X16`), or `length_s` and `fmt` (both together with a version must agree).

```json
{"file": "runs/r1/renders/H1_15S_9X16.mp4", "version": "H1_15S_9X16"}
```

Answer: `{"passed": true, "reasons": [], "measured": {"length_s": 15.0, "size": "1080x1920", "fps": "24/1", "lufs": -14.0, "tp": -4.2, ...}}`.
Each failing check adds one short reason (length, size, frame rate, pixel format, codecs, loudness, true peak).

## POST /transcribe

Whisper transcript of an audio or video file, and the banned words found in it (the rulebook's list). With `script` (the
approved voice-over text) it is the final-audio check; `version` is optional and only checked for validity.

```json
{"file": "runs/r1/renders/H1_15S_9X16.mp4", "script": "Sleep better tonight.", "version": "H1_15S_9X16"}
```

Answer: `{"text": "sleep better tonight", "banned": [], "wer": 0.0, "passed": true, "reasons": []}`. `wer` is the word error
rate (numbers, contractions and punctuation do not count); `passed` is `wer` at most 0.15 and no banned word. Without
`script` the answer is `{"text": ..., "banned": [...]}`. A file without an audio stream is a 422. `script` must be left out when there is none: an empty string is a 422.

## POST /frames

Stills from a video: `n` evenly spaced frames (1 to 12), or `at`: a list of seconds, or `"last"`.

```json
{"file": "runs/r1/renders/H1_15S_9X16.mp4", "n": 3}
```

Answer: `{"files": ["runs/r1/frames/H1_15S_9X16_3fa2b1_n1.jpg", ...]}`. The name carries a short hash of the input path, so
two clips with the same name in different folders do not overwrite each other.

## POST /sheet

A contact sheet (6 x 3 JPEG, one frame per file, labelled with the file name) of up to 18 files. A file that is missing or
unreadable (a failed render) gets a "no frame" cell instead of failing the sheet.

```json
{"files": ["runs/r1/renders/H1_6S_1X1.mp4", "runs/r1/renders/H1_15S_9X16.mp4"], "out": "runs/r1/sheet.jpg"}
```

Answer: `{"file": "runs/r1/sheet.jpg"}`.

## POST /package

Builds the handover package from the approved versions and, with `upload` true (the default), uploads it to R2 and returns a
7-day link. `upload: false` builds the folder only (the dry run). Each approved item's `file` must be
`runs/<run_id>/renders/<version>.mp4` (its name must equal its `version`). `v` (1 to 99, default 1) is the V number in the
renamed file; n8n raises it when the same name is rendered again. `run.disclaimer` is the text burned into the end card and
goes into the manifest; when absent the rulebook's is used. `qa` per approved version is `{"passed", "reasons"}` as merged
below.

```json
{"run_id": "r1", "upload": false,
 "run": {"brand": "BRAND", "campaign": "SleepLaunch", "started_at": "2026-11-22T01:00:00Z", "finished_at": "2026-11-22T01:45:00Z",
         "gpu_minutes": 30, "gpu_rate_usd_h": 0.8, "voice_usd": 0.1, "llm_usd": 0.1,
         "disclaimer": "A clinician assesses every request.", "link": "https://example.com", "cta": "Learn More"},
 "approved": [{"version": "H1_6S_1X1", "file": "runs/r1/renders/H1_6S_1X1.mp4", "hook_text": "...", "script": "...", "v": 1,
               "qa": {"passed": true, "reasons": []}, "approver": "name", "approved_at": "2026-11-22T01:40:00Z"}],
 "rejected": [{"version": "H1_30S_16X9", "note": "voice clipped the last word"}]}
```

Answer: `{"folder": "runs/r1/package", "files": ["BRAND_SLEEPLAUNCH_H1_6S_1X1_EN_V01.mp4"], "report": {"assets": 1, "cost_per_asset_usd": 0.3, "turnaround_min": 45}, "link": "https://..."}`
(`link` only with `upload`). Both timestamps need a time zone and `finished_at` must not be before `started_at`. A second
`/package` for the same run while one is building or uploading is a 409 (the lock is held through the upload).

### How `qa` is merged for each version

`qa.reasons` is the concatenation of the reasons of the three checks, `qa.passed` is true only when that list is empty:

1. the `/render` log's `reasons`,
2. `/check`'s `reasons` (a `passed: false` with no reason is not possible: every failing check adds one),
3. `/transcribe`'s `reasons` for the final-audio check (when it ran with `script`).

## GET /rulebook, GET /edit, POST /lint

- `GET /rulebook` returns the brand rulebook: `brand`, `disclaimer`, `claims`, `shots`, `banned_words`, `banned_media`,
  `look_fallback`, `tone`. n8n pastes these into the script prompts.
- `GET /edit` returns `config/edit.yaml`: `endcard_s`, `cutaway_s`, `broll_match` and, per hook, the beats and b-roll slots of
  each length. JSON keys are strings, so the lengths come as `"6"`, `"15"`, `"30"`.
- `POST /lint` checks texts against the banned words (whole words, a phrase matches across spaces, line breaks and hyphens):
  `{"texts": ["This will cure you", "A calm night"]}` gives `{"hits": [["cure"], []]}`, one list per text.

## GET /health

`{"ok": true}`. It answers even while renders are queued.
