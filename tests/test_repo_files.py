"""The repo is public: no private names, hosts or machine paths in what ships; the deploy files say what the service needs."""
import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
_TRACKED = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.split("\n")
SHIPPED = [ROOT / f for f in _TRACKED if f and not f.startswith("tests/") and (ROOT / f).is_file() and f != "constraints.txt"]
PRIVATE = re.compile("|".join((r"/home/(?!node\b)\w+", r"/Users/\w+", r"\.ts\.net")), re.I)     # machine paths and private-network hosts


def test_nothing_private_ships():
    hits = [(p.relative_to(ROOT).as_posix(), m.group(0)) for p in SHIPPED for m in PRIVATE.finditer(p.read_text(errors="replace"))]
    assert hits == []


def test_env_example_uses_placeholders():
    text = (ROOT / "docker" / ".env.example").read_text()
    assert "n8n.example.com" in text and "the deploy user" in text and "N8N_RESTRICT_FILE_ACCESS_TO=/data" in text and "EXECUTIONS_DATA_MAX_AGE=336" in text and "NODE_FUNCTION_ALLOW_BUILTIN=crypto" in text
    assert "PRELOAD_WHISPER=1" in text


def test_base_image_has_the_ffmpeg_the_sheet_needs():
    text = (ROOT / "docker" / "media.Dockerfile").read_text()
    assert "FROM python:3.12-slim-trixie" in text and "text_align" in text


def test_compose_healthcheck_and_single_worker():
    media = yaml.safe_load((ROOT / "docker" / "compose.yml").read_text())["services"]["media"]
    assert "/health" in " ".join(media["healthcheck"]["test"])
    assert '"--workers", "1"' in (ROOT / "docker" / "media.Dockerfile").read_text()
    assert "--workers 1" in (ROOT / "docker" / "compose.yml").read_text()


def test_superpowers_folder_is_ignored():
    assert ".superpowers/" in (ROOT / ".gitignore").read_text().splitlines()


def test_every_endpoint_is_in_the_api_doc():
    from media import server
    doc = (ROOT / "docs" / "api.md").read_text()
    paths = {r.path for r in server.app.routes if r.path.startswith("/") and r.path not in ("/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect")}
    assert {"/render", "/check", "/transcribe", "/frames", "/sheet", "/package", "/rulebook", "/edit", "/lint", "/health", "/run"} <= paths
    assert [p for p in paths if f"/{p.lstrip('/')}" not in doc] == []


def test_the_render_body_passes_the_brand_logo():
    import json
    wf = json.loads((ROOT / "n8n" / "workflow.json").read_text())
    plan = next(n for n in wf["nodes"] if n["name"] == "Render: plan")
    assert "music: 'brand/music.m4a', logo: 'brand/logo.png'" in plan["parameters"]["jsCode"]


def _wf_code(name):
    import json
    wf = json.loads((ROOT / "n8n" / "workflow.json").read_text())
    return next(n for n in wf["nodes"] if n["name"] == name)["parameters"]["jsCode"]


def test_the_lint_calls_say_which_texts_are_spoken_lines():
    assert "voice: items.map((i) => i.kind === 'line')" in _wf_code("Compliance: collect")
    assert "voice: rewritten.map(() => true)" in _wf_code("Compliance: apply 1")      # a rewrite is always a spoken line
    assert 'A hit named "brackets"' in _wf_code("Compliance: judge prompt")


def test_the_script_word_budget_matches_the_measured_voice_rate():
    """docs/measured.md: ~2.85 words/s; a line fills (length - end card - 0.5 s). The prompt's range and the 'too long' note agree."""
    import re
    prompt, check = _wf_code("Scripts: prompt"), _wf_code("Scripts: validate")
    rate, end = 2.85, 1.5
    rng = {m[0]: (int(m[1]), int(m[2])) for m in re.findall(r'\\"(\d+)\\"[^"]*?\((?:a \d+ second ad, )?(\d+) to (\d+) words\)', prompt.replace('"', '\\"'))}
    assert set(rng) == {"6", "15", "30"}, rng
    maxw = {k: int(v) for k, v in re.findall(r"'(\d+)': (\d+)", re.search(r"const MAXW = \{[^}]*\}", check).group(0))}
    for length, (lo, hi) in rng.items():
        fill = rate * (int(length) - end - 0.5)
        assert lo <= fill <= hi + 1 and 0.85 * fill <= lo, (length, lo, hi, fill)         # the range brackets the fill target
        assert maxw[length] == round(rate * (int(length) - end)) and hi <= maxw[length]       # the note fires only past the end card
    assert "No brackets or parentheses" in prompt
