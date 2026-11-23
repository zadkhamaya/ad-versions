"""package.py — the handover package of a run (spec §8): approved versions renamed by the naming convention, manifest.json,
manifest.csv in the Meta bulk-import layout (columns from config/meta_columns.yaml), report.md, a plain index.html; and the
upload of that folder to R2 with a 7-day link."""
import csv
import html
import json
import mimetypes
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

from media import naming, rulebook, voice

ROOT = Path(__file__).resolve().parent.parent
LINK_TTL_S = 7 * 24 * 3600
PLACEMENTS = {"9X16": "Reels/Stories", "1X1": "Feed", "16X9": "In-stream"}
FORMULA = "(gpu_minutes/60 × rate + voice + LLM) ÷ approved"
EXTRAS = ("manifest.csv", "manifest.json", "report.md")
_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _columns() -> list[dict]:
    return yaml.safe_load((ROOT / "config" / "meta_columns.yaml").read_text())["columns"]


def _cell(col: dict, run: dict, item: dict, name: str, fmt: str) -> str:
    src = col["source"]
    if src == "ad_set":
        val = f"{naming._part(run['campaign'])}_{fmt}"
    elif src == "ad_name":
        val = name.removesuffix(".mp4")
    elif src == "file":
        val = name
    elif src.startswith("run."):
        val = run.get(src[4:])
    elif src.startswith("item."):
        val = item.get(src[5:])
    else:
        raise ValueError(f"meta_columns.yaml: column {col['name']!r} has an unknown source {src!r}")
    val = str(val) if val not in (None, "") else str(col.get("default", ""))
    if val[:1] in _FORMULA:                  # a spreadsheet would run it as a formula
        raise ValueError(f"{name}: {col['name']} starts with {val[:1]!r}, which a spreadsheet reads as a formula")
    return val


def turnaround_min(run: dict) -> float:
    """Minutes from started_at to finished_at (ISO 8601, both with a time zone, finished not before started); ValueError otherwise."""
    start, end = (datetime.fromisoformat(run[k]) for k in ("started_at", "finished_at"))
    if start.utcoffset() is None or end.utcoffset() is None:
        raise ValueError("started_at and finished_at need a time zone (e.g. 2026-11-22T01:00:00Z)")
    if end < start:
        raise ValueError("finished_at is before started_at")
    return (end - start).total_seconds() / 60


def _one_line(text) -> str:
    """User text for report.md: one line, no markup."""
    return re.sub(r"\s+", " ", str(text)).strip().replace("<", "&lt;")


def _qa_line(qa) -> str:
    if not qa:
        return "no QA result"
    return "passed" if qa.get("passed") else "failed: " + _one_line("; ".join(qa.get("reasons") or ["no reason given"]))


def _index(names: list[str], href=lambda n: n) -> str:
    rows = "\n".join(f'<li><a href="{html.escape(href(n), quote=True)}">{html.escape(n)}</a></li>' for n in names)
    return ('<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">\n'
            '<title>Handover package</title>\n<style>body{font:16px/1.5 system-ui,sans-serif;max-width:40rem;margin:2rem auto;padding:0 16px}'
            'li{margin:.3rem 0;word-break:break-all}</style></head>\n'
            f"<body><h1>Handover package</h1>\n<ul>\n{rows}\n</ul></body></html>\n")


def _report(run: dict, rows: list[dict], rejected: list[dict], summary: dict) -> str:
    cost = summary["cost_per_asset_usd"]
    out = [f"# Run report: {run['brand']} {run['campaign']}", "",
           f"- Assets per shoot: {summary['assets']}",
           f"- Cost per asset: {'n/a (nothing approved)' if cost is None else f'${cost:.2f}'}",
           f"  - {FORMULA}; GPU {run['gpu_minutes']:g} min at ${run['gpu_rate_usd_h']:g}/h, voice-over ${run['voice_usd']:.2f}, LLM ${run['llm_usd']:.2f}",
           f"- Turnaround: {summary['turnaround_min']:g} min ({run['started_at']} to {run['finished_at']})", "",
           "## Approved", ""]
    out += [f"- {r['file']} ({r['version']}), QA {_qa_line(r['qa'])}; approved by {_one_line(r['approver']) or 'n/a'} at {_one_line(r['approved_at']) or 'n/a'}" for r in rows] or ["- none"]
    out += ["", "## Rejected", ""]
    out += [f"- {_one_line(x['version'])}: {_one_line(x.get('note') or 'no note')}" for x in rejected] or ["- none"]
    return "\n".join(out) + "\n"


def build(run: dict, approved: list[dict], rejected: list[dict], out_dir: Path) -> dict:
    """Build the package and return {"folder", "files", "report": {...}}. The renders are copied, never moved. The folder is built
    beside out_dir and renamed into place, so out_dir holds this build's files only (a retry replaces the last build) and a failed
    build leaves the old one. Each item's `v` (default 1) is its V number: the files of one build are unique (one per version), and each build starts over."""
    out_dir = Path(out_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    versions = [a["version"] for a in approved]
    dup = sorted({v for v in versions if versions.count(v) > 1})
    if dup:
        raise ValueError(f"duplicate approved version(s): {', '.join(dup)}")
    summary_min = round(turnaround_min(run), 1)
    disclaimer = run.get("disclaimer") or rulebook.load(ROOT / "brand" / "rulebook.yaml").disclaimer   # the one burned into the end card
    lang = run.get("lang", "EN")
    tmp = Path(tempfile.mkdtemp(prefix=f".{out_dir.name}.tmp-", dir=out_dir.parent))
    try:
        rows = []
        for a in approved:
            hook, length_s, fmt = naming.parse_version(a["version"])
            if a.get("length_s") not in (None, length_s) or a.get("fmt") not in (None, fmt):
                raise ValueError(f"{a['version']}: length_s/fmt {a.get('length_s')}/{a.get('fmt')} disagree with the version")
            name = naming.version_name(run["brand"], run["campaign"], hook, length_s, fmt, lang, a.get("v", 1))
            shutil.copy2(a["file"], tmp / name)
            rows.append({"version": a["version"], "file": name, "hook": a["hook_text"], "script": a["script"], "length_s": length_s,
                         "format": fmt, "placement": PLACEMENTS[fmt], "synthetic_media": "yes", "disclaimer": disclaimer,
                         "qa": a.get("qa"), "approver": a.get("approver", ""), "approved_at": a.get("approved_at", ""), "_item": a})

        cols = _columns()
        with (tmp / "manifest.csv").open("w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
            w.writerow([c["name"] for c in cols])
            for r in rows:
                w.writerow([_cell(c, run, r["_item"], r["file"], r["format"]) for c in cols])
        (tmp / "manifest.json").write_text(
            json.dumps({"brand": run["brand"], "campaign": run["campaign"],
                        "files": [{k: v for k, v in r.items() if k != "_item"} for r in rows]}, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8")
        total = run["gpu_minutes"] / 60 * run["gpu_rate_usd_h"] + run["voice_usd"] + run["llm_usd"]
        summary = {"assets": len(rows), "cost_per_asset_usd": round(total / len(rows), 2) if rows else None, "turnaround_min": summary_min}
        (tmp / "report.md").write_text(_report(run, rows, rejected, summary), encoding="utf-8")
        (tmp / "index.html").write_text(_index([r["file"] for r in rows] + list(EXTRAS)), encoding="utf-8")
        if out_dir.exists():
            shutil.rmtree(out_dir)
        os.replace(tmp, out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return {"folder": str(out_dir), "files": [r["file"] for r in rows], "report": summary}


def s3_client_from_env():
    return voice.r2_from_env().client


def upload(out_dir: Path, bucket: str, prefix: str, client=None) -> str:
    """Upload the files of the package (its videos per manifest.json, and the manifests and report) to bucket/prefix/ and return a 7-day presigned link to index.html.
    A presigned URL signs one object, so the uploaded index.html is written after the others, with their signed URLs."""
    out_dir = Path(out_dir)
    client = client or s3_client_from_env()
    prefix = prefix.strip("/")
    key = lambda n: f"{prefix}/{n}" if prefix else n

    def ctype(n):
        return mimetypes.guess_type(n)[0] or "application/octet-stream"

    # only this build's own files (manifest.json lists the videos), never whatever else sits in the folder
    names = [f["file"] for f in json.loads((out_dir / "manifest.json").read_text(encoding="utf-8"))["files"]] + list(EXTRAS)
    urls = {}
    for n in names:
        client.upload_file(str(out_dir / n), bucket, key(n), ExtraArgs={"ContentType": ctype(n)})
        urls[n] = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key(n)}, ExpiresIn=LINK_TTL_S)
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index.html"
        index.write_text(_index(names, href=urls.__getitem__), encoding="utf-8")
        client.upload_file(str(index), bucket, key("index.html"), ExtraArgs={"ContentType": "text/html; charset=utf-8"})
    return client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key("index.html")}, ExpiresIn=LINK_TTL_S)
