# ad-versions

One brief and one video clip in; 18 checked, named ad versions (2 hooks × 6/15/30 s × 9:16, 1:1, 16:9) and a
ready-to-upload package out. n8n runs the flow; `media/` is the Python + ffmpeg service that renders and checks.

- `media/` — the media service (FastAPI): render, check, transcribe, frames, contact sheet, package
- `brand/` — the fictional brand's rulebook; `config/` — naming convention, edit plan, Meta columns
- `docker/` — the media image and the compose file (n8n + media)
- `n8n/` (to come) — the exported workflow and what it expects

Tests: `python3 -m venv .venv && .venv/bin/pip install -e '.[test]' && .venv/bin/pytest -q` (needs ffmpeg 6+ with
libass and drawtext; the image installs the same versions from `constraints.txt`, a freeze of this venv).

The service reads the brand inputs from `DATA_ROOT/brand/` (`/data/brand/` in the container): put `music.m4a` (the music
bed) and `logo.png` (the end card's logo; optional, the brand name is drawn as text without it) there. The calling contract (every endpoint, error shapes, timeouts, file names) is in `docs/api.md`.
