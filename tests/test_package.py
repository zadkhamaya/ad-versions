import csv
import json
import re
from pathlib import Path

import pytest
import yaml

from media import naming, package

ROOT = Path(__file__).resolve().parent.parent
RUN = {"brand": "BRAND", "campaign": "SleepLaunch", "started_at": "2026-11-22T01:00:00Z", "finished_at": "2026-11-22T02:30:00Z",
       "gpu_minutes": 60, "gpu_rate_usd_h": 0.5, "voice_usd": 0.2, "llm_usd": 0.1,
       "link": "https://example.com/sleep", "cta": "FAKE CTA (test)", "objective": "FAKE OBJECTIVE (test)"}
QA = {"passed": True, "reasons": [], "measured": {"lufs": -14.0}}


@pytest.fixture
def renders(tmp_path):
    d = tmp_path / "renders"
    d.mkdir()
    for v in ("H1_15S_9X16", "H2_6S_1X1", "H1_30S_16X9"):
        (d / f"{v}.mp4").write_bytes(b"video-" + v.encode())
    return d


def item(renders, version, **kw):
    hook, length_s, fmt = naming.parse_version(version)
    d = {"version": version, "file": str(renders / f"{version}.mp4"), "hook_text": f"Hook, for {version}", "script": f'Script "{version}", plain.',
         "length_s": length_s, "fmt": fmt, "synthetic_media": "yes", "qa": QA, "approver": "reviewer", "approved_at": "2026-11-22T02:00:00Z"}
    d.update(kw)
    return d


@pytest.fixture
def built(renders, tmp_path):
    approved = [item(renders, "H1_15S_9X16"), item(renders, "H2_6S_1X1")]
    rejected = [{"version": "H1_30S_16X9", "note": "voice clipped the last word"}]
    out = tmp_path / "package"
    return package.build(RUN, approved, rejected, out), out, approved


def test_renamed_files_follow_the_convention_and_renders_stay(built, renders):
    res, out, approved = built
    want = [naming.version_name("BRAND", "SleepLaunch", "H1", 15, "9X16"), naming.version_name("BRAND", "SleepLaunch", "H2", 6, "1X1")]
    assert res["files"] == want
    for name, a in zip(want, approved):
        assert (out / name).read_bytes() == Path(a["file"]).read_bytes()
        assert Path(a["file"]).exists()                      # copied, never moved
    assert not (out / "H1_30S_16X9.mp4").exists() and len(list(out.glob("*.mp4"))) == 2
    assert res["folder"] == str(out)


def test_a_second_build_leaves_only_the_new_files_and_uploads_only_them(renders, tmp_path):
    out = tmp_path / "package"
    first = package.build(RUN, [item(renders, "H1_15S_9X16"), item(renders, "H2_6S_1X1")], [], out)
    assert len(first["files"]) == 2
    second = package.build(RUN, [item(renders, "H1_15S_9X16")], [], out)
    assert second["files"] == [first["files"][0]]                      # V01 again: a retry does not rise
    assert sorted(p.name for p in out.glob("*.mp4")) == second["files"]
    assert not list(tmp_path.glob(".package*"))                         # no temp folder left behind
    assert json.loads((out / "manifest.json").read_text())["files"][0]["file"] == second["files"][0]
    (out / "stray.mp4").write_bytes(b"x")                               # whatever else sits in the folder is not shipped
    s3 = FakeS3()
    package.upload(out, "bkt", "p", client=s3)
    assert set(s3.uploaded) == {f"p/{n}" for n in second["files"] + ["manifest.csv", "manifest.json", "report.md", "index.html"]}


def test_a_failed_build_leaves_the_old_package_and_no_temp_folder(renders, tmp_path):
    out = tmp_path / "package"
    good = package.build(RUN, [item(renders, "H1_15S_9X16")], [], out)
    with pytest.raises(ValueError):
        package.build(RUN, [item(renders, "H2_6S_1X1", hook_text="=cmd()")], [], out)
    assert [p.name for p in out.glob("*.mp4")] == good["files"] and not list(tmp_path.glob(".package*"))


def test_duplicate_versions_are_refused(renders, tmp_path):
    with pytest.raises(ValueError, match="duplicate"):
        package.build(RUN, [item(renders, "H1_15S_9X16"), item(renders, "H1_15S_9X16")], [], tmp_path / "p")


def test_csv_formula_injection_is_refused(renders, tmp_path):
    for bad in ("=HYPERLINK(1)", "+1", "-1", "@SUM", "\tx", "\rx"):
        with pytest.raises(ValueError, match="Title"):
            package.build(RUN, [item(renders, "H1_15S_9X16", hook_text=bad)], [], tmp_path / "p")
    with pytest.raises(ValueError, match="Body"):
        package.build(RUN, [item(renders, "H1_15S_9X16", script="=1+1")], [], tmp_path / "p")


def test_report_notes_are_one_line_and_escaped(renders, tmp_path):
    rej = [{"version": "H1_30S_16X9", "note": "bad\n## Fake heading\n<script>x</script>   end"}]
    package.build(RUN, [], rej, tmp_path / "p")
    text = (tmp_path / "p" / "report.md").read_text()
    assert "- H1_30S_16X9: bad ## Fake heading &lt;script>x&lt;/script> end\n" in text and "\n## Fake" not in text and "<script" not in text


def test_turnaround_needs_aware_ordered_times():
    assert package.turnaround_min({"started_at": "2026-11-22T01:00:00+07:00", "finished_at": "2026-11-21T18:00:00Z"}) == 0
    for s, f in (("2026-11-22T01:00:00", "2026-11-22T02:00:00Z"), ("2026-11-22T01:00:00Z", "2026-11-22T02:00:00"),
                 ("2026-11-22T02:00:00Z", "2026-11-22T01:00:00Z"), ("nope", "2026-11-22T01:00:00Z")):
        with pytest.raises(ValueError):
            package.turnaround_min({"started_at": s, "finished_at": f})


def test_manifest_json(built):
    res, out, approved = built
    m = json.loads((out / "manifest.json").read_text())
    assert [f["file"] for f in m["files"]] == res["files"]
    f = m["files"][0]
    assert f["version"] == "H1_15S_9X16" and f["hook"] == "Hook, for H1_15S_9X16" and f["script"].startswith("Script")
    assert f["length_s"] == 15 and f["format"] == "9X16" and f["placement"] == "Reels/Stories"
    assert f["synthetic_media"] == "yes" and f["qa"] == QA and f["approver"] == "reviewer" and f["approved_at"] == "2026-11-22T02:00:00Z"
    assert f["disclaimer"] == "A clinician assesses every request. Care plans differ from person to person."
    assert m["files"][1]["placement"] == "Feed"


def test_placements():
    assert package.PLACEMENTS == {"9X16": "Reels/Stories", "1X1": "Feed", "16X9": "In-stream"}


def test_csv_header_is_the_yaml_list_in_order(built):
    res, out, _ = built
    cols = yaml.safe_load((ROOT / "config" / "meta_columns.yaml").read_text())["columns"]
    rows = list(csv.reader((out / "manifest.csv").open(encoding="utf-8", newline="")))
    assert rows[0] == [c["name"] for c in cols]
    assert rows[0][:2] == ["Campaign Name", "Campaign Objective"] and len(rows) == 3


def test_csv_values_follow_the_mapping_and_quote_commas(built):
    res, out, _ = built
    rows = list(csv.DictReader((out / "manifest.csv").open(encoding="utf-8", newline="")))
    r = rows[0]
    assert r["Campaign Name"] == "SleepLaunch" and r["Campaign Objective"] == "FAKE OBJECTIVE (test)"
    assert r["Ad Set Name"] == "SLEEPLAUNCH_9X16"
    assert r["Ad Name"] == res["files"][0].removesuffix(".mp4") and r["Video File Name"] == res["files"][0]
    assert r["Title"] == "Hook, for H1_15S_9X16" and r["Body"] == 'Script "H1_15S_9X16", plain.'
    assert r["Link"] == "https://example.com/sleep" and r["Call to Action"] == "FAKE CTA (test)"
    assert rows[1]["Ad Set Name"] == "SLEEPLAUNCH_1X1"


def test_csv_defaults_when_run_lacks_objective_and_cta(renders, tmp_path):
    run = {k: v for k, v in RUN.items() if k not in ("objective", "cta")}
    out = tmp_path / "p"
    package.build(run, [item(renders, "H1_15S_9X16")], [], out)
    r = next(csv.DictReader((out / "manifest.csv").open(encoding="utf-8", newline="")))
    d = {c["name"]: c["default"] for c in yaml.safe_load((ROOT / "config" / "meta_columns.yaml").read_text())["columns"] if "default" in c}
    assert r["Campaign Objective"] == d["Campaign Objective"] and r["Call to Action"] == d["Call to Action"]


def test_report(built):
    res, out, _ = built
    text = (out / "report.md").read_text()
    cost = round((60 / 60 * 0.5 + 0.2 + 0.1) / 2, 2)
    assert cost == 0.4
    assert "Assets per shoot: 2" in text
    assert "Cost per asset: $0.40" in text and "(gpu_minutes/60 × rate + voice + LLM) ÷ approved" in text
    assert "Turnaround: 90 min" in text
    assert "H1_30S_16X9" in text and "voice clipped the last word" in text
    assert res["report"] == {"assets": 2, "cost_per_asset_usd": 0.4, "turnaround_min": 90}


def test_report_with_nothing_approved_has_no_division(renders, tmp_path):
    res = package.build(RUN, [], [{"version": "H1_15S_9X16", "note": "n"}], tmp_path / "p")
    assert res["files"] == [] and res["report"]["assets"] == 0 and res["report"]["cost_per_asset_usd"] is None
    assert "Assets per shoot: 0" in (tmp_path / "p" / "report.md").read_text()


def test_qa_reasons_reach_the_report(renders, tmp_path):
    bad = item(renders, "H1_15S_9X16", qa={"passed": False, "reasons": ["loudness -11.2 LUFS"], "measured": {}})
    package.build(RUN, [bad], [], tmp_path / "p")
    assert "loudness -11.2 LUFS" in (tmp_path / "p" / "report.md").read_text()


def test_index_html_lists_files_with_relative_links_and_no_external_assets(built):
    res, out, _ = built
    html = (out / "index.html").read_text()
    for name in res["files"] + ["manifest.csv", "manifest.json", "report.md"]:
        assert f'href="{name}"' in html
    assert "http://" not in html and "https://" not in html and "<script" not in html


def test_mismatched_length_is_refused(renders, tmp_path):
    with pytest.raises(ValueError, match="length"):
        package.build(RUN, [item(renders, "H1_15S_9X16", length_s=30)], [], tmp_path / "p")


# ---- upload ----

class FakeS3:
    def __init__(self):
        self.uploaded, self.presigned, self.html = {}, [], None

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None):
        self.uploaded[Key] = (Bucket, ExtraArgs, Path(Filename).read_bytes())
        if Key.endswith("index.html"):
            self.html = Path(Filename).read_text()
            self.presigned_before_index = len(self.presigned)

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presigned.append((op, Params["Key"], ExpiresIn))
        return f"https://r2.example/{Params['Bucket']}/{Params['Key']}?X-Amz-Expires={ExpiresIn}&sig=abc"


def test_upload_sends_every_file_and_returns_a_7_day_link_to_index(built):
    res, out, _ = built
    s3 = FakeS3()
    link = package.upload(out, "bkt", "runs/r1/package", client=s3)
    keys = set(s3.uploaded)
    assert keys == {f"runs/r1/package/{n}" for n in res["files"] + ["manifest.csv", "manifest.json", "report.md", "index.html"]}
    assert all(b == "bkt" for b, _, _ in s3.uploaded.values())
    assert link == "https://r2.example/bkt/runs/r1/package/index.html?X-Amz-Expires=604800&sig=abc"
    assert all(t == 604800 and op == "get_object" for op, _, t in s3.presigned)


def test_index_is_written_after_presigning_with_per_file_urls_and_uploaded_last(built):
    res, out, _ = built
    s3 = FakeS3()
    package.upload(out, "bkt", "p", client=s3)
    assert list(s3.uploaded)[-1] == "p/index.html"
    assert s3.presigned_before_index == len(res["files"]) + 3     # every file presigned before index.html went up
    for name in res["files"]:
        assert f"https://r2.example/bkt/p/{name}?X-Amz-Expires=604800&amp;sig=abc" in s3.html   # escaped for HTML
    assert 'href="manifest.csv"' not in s3.html                  # no relative links: they would not carry a signature
    assert s3.uploaded["p/index.html"][1]["ContentType"].startswith("text/html")
    assert s3.uploaded[f"p/{res['files'][0]}"][1]["ContentType"] == "video/mp4"


def test_upload_from_env_needs_the_r2_variables(built, monkeypatch):
    for k in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(Exception, match="R2_ACCOUNT_ID"):
        package.upload(built[1], "bkt", "p")


def test_approver_and_approved_at_are_one_line_in_the_report(renders, tmp_path):
    out = tmp_path / "package"
    a = item(renders, "H1_15S_9X16", approver="Reviewer\n## Rejected\n- <b>x</b>", approved_at="2026-11-22T02:00:00Z\r\n- injected")
    package.build(RUN, [a], [], out)
    report = (out / "report.md").read_text()
    approved_line = next(l for l in report.splitlines() if "approved by" in l)
    assert "Reviewer ## Rejected - &lt;b>x&lt;/b>" in approved_line and "- injected" in approved_line
    assert sum(l.startswith("## Rejected") for l in report.splitlines()) == 1 and "<b>" not in report
    assert not any(l.startswith("- injected") for l in report.splitlines())


def test_v_number_and_disclaimer_come_from_the_request(renders, tmp_path):
    out = tmp_path / "package"
    a = [item(renders, "H1_15S_9X16", v=7), item(renders, "H2_6S_1X1")]
    res = package.build({**RUN, "disclaimer": "Care plans differ."}, a, [], out)
    assert [f.rsplit("_", 1)[1] for f in res["files"]] == ["V07.mp4", "V01.mp4"]
    assert all(f["disclaimer"] == "Care plans differ." for f in json.loads((out / "manifest.json").read_text())["files"])
    res = package.build(RUN, a[:1], [], out)                                  # no disclaimer in the request: the rulebook's
    assert json.loads((out / "manifest.json").read_text())["files"][0]["disclaimer"].startswith("A clinician assesses")
    with pytest.raises(ValueError):
        package.build(RUN, [item(renders, "H1_15S_9X16", v=100)], [], out)
