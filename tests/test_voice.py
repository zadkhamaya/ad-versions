import io
import json
import urllib.request
import wave

import pytest

from media import voice
from media.voice import R2, RunPodClient, VoiceError, speak


def wav_bytes(seconds=1.0, rate=16000):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * seconds))
    return buf.getvalue()


class FakeRunPod:
    def __init__(self, statuses):
        self.statuses, self.jobs, self.polls, self.cancelled = list(statuses), [], 0, []

    def run(self, endpoint, input, ttl_s):
        self.jobs.append((endpoint, input, ttl_s))
        return "j1"

    def status(self, endpoint, job_id):
        self.polls += 1
        return self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]

    def cancel(self, endpoint, job_id):
        self.cancelled.append(job_id)


class FakeR2:
    def __init__(self, data=None):
        self.data = data if data is not None else wav_bytes(1.5)
        self.keys = []

    def presign_put(self, key):
        self.keys.append(key)
        return f"https://r2.test/put/{key}"

    def get(self, key):
        return self.data


class Clock:
    def __init__(self):
        self.t = 0.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


def call(tmp_path, rp, r2=None, clock=None, **kw):
    clock = clock or Clock()
    return speak("Sleep better tonight.", tmp_path / "vo.wav", sample_get="https://r2.test/get/sample.wav",
                 transcript="a sample", run="run1", version="H1_15S_9X16", runpod=rp, r2=r2 or FakeR2(),
                 endpoint="ep1", clock=clock.now, sleep=clock.sleep, **kw)


def test_speak_job_shape_and_wav(tmp_path):
    rp = FakeRunPod([{"status": "IN_PROGRESS"}, {"status": "COMPLETED", "output": {"rows": [
        {"id": "vo", "ok": True, "seed": 42, "duration_s": 1.5, "generate_s": 0.7}]}}])
    r2 = FakeR2()
    secs = call(tmp_path, rp, r2)
    assert abs(secs - 1.5) < 0.05
    assert (tmp_path / "vo.wav").read_bytes() == r2.data
    endpoint, job, _ttl = rp.jobs[0]
    assert endpoint == "ep1"
    assert set(job) == {"op", "model", "voice", "params", "rows"}
    assert job["op"] == "speak" and job["model"] == "voxcpm2"
    assert job["voice"] == {"sample_get": "https://r2.test/get/sample.wav", "transcript": "a sample", "lang": "en"}
    assert job["params"] == {"seed": 42, "cfg_value": 1.6, "inference_timesteps": 10}
    assert job["rows"] == [{"id": "vo", "text": "Sleep better tonight.", "out": {
        "wav": "https://r2.test/put/runs/run1/vo/H1_15S_9X16.wav", "json": "https://r2.test/put/runs/run1/vo/H1_15S_9X16.json"}}]
    assert rp.polls == 2


def test_failed_job_raises_with_top_level_error(tmp_path):
    rp = FakeRunPod([{"status": "FAILED", "error": "CUDA out of memory"}])
    with pytest.raises(VoiceError, match="CUDA out of memory"):
        call(tmp_path, rp)


def test_failed_row_raises(tmp_path):
    rp = FakeRunPod([{"status": "COMPLETED", "output": {"rows": [
        {"id": "vo", "ok": False, "seed": 42, "duration_s": None, "generate_s": 0.1, "error": "text too long"}]}}])
    with pytest.raises(VoiceError, match="text too long"):
        call(tmp_path, rp)


def test_in_queue_past_ten_minutes_gives_up(tmp_path):
    rp = FakeRunPod([{"status": "IN_QUEUE"}])
    clock = Clock()
    with pytest.raises(VoiceError, match="voice endpoint did not wake in 10 min"):
        call(tmp_path, rp, clock=clock)
    assert 600 <= clock.t < 700 and rp.cancelled == ["j1"]


def test_in_queue_then_running_is_fine(tmp_path):
    rp = FakeRunPod([{"status": "IN_QUEUE"}] * 5 + [{"status": "COMPLETED", "output": {"rows": [
        {"id": "vo", "ok": True, "seed": 42, "duration_s": 1.5, "generate_s": 0.7}]}}])
    assert call(tmp_path, rp) > 1


def test_runpod_client_sets_user_agent_and_bearer(monkeypatch):
    seen = []

    class Resp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return json.dumps({"id": "j9", "status": "IN_QUEUE"}).encode()

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return Resp()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    c = RunPodClient("k-secret")
    assert c.run("ep1", {"op": "speak"}, 1800) == "j9"
    assert c.status("ep1", "j9")["status"] == "IN_QUEUE"
    run_req, status_req = seen
    assert run_req.full_url == "https://api.runpod.ai/v2/ep1/run" and run_req.get_method() == "POST"
    assert json.loads(run_req.data) == {"input": {"op": "speak"}, "policy": {"ttl": 1800000}}
    assert status_req.full_url == "https://api.runpod.ai/v2/ep1/status/j9"
    for r in seen:
        assert r.get_header("User-agent") and "python-urllib" not in r.get_header("User-agent").lower()
        assert r.get_header("Authorization") == "Bearer k-secret"


def test_runpod_client_error_does_not_leak_key(monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.URLError("refused k-secret")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    with pytest.raises(VoiceError) as e:
        RunPodClient("k-secret").status("ep1", "j9")
    assert "k-secret" not in str(e.value)


def test_r2_presigns_against_the_account_endpoint():
    r2 = R2(account_id="acct123", access_key_id="AKID", secret_access_key="SECRET", bucket="bkt")
    url = r2.presign_put("runs/run1/vo/H1_15S_9X16.wav")
    assert url.startswith("https://acct123.r2.cloudflarestorage.com/bkt/runs/run1/vo/H1_15S_9X16.wav?")
    assert "X-Amz-Signature=" in url and "SECRET" not in url


def test_defaults_build_from_env_only_when_called(monkeypatch):
    for k in ("RUNPOD_API_KEY", "VOICE_EN_ENDPOINT", "R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(VoiceError, match="RUNPOD_API_KEY"):
        voice.runpod_from_env()
    with pytest.raises(VoiceError, match="R2_ACCOUNT_ID"):
        voice.r2_from_env()


def test_r2_presigns_a_get_link_offline():
    r2 = voice.R2("acct", "AKIAEXAMPLE", "secret", "bucket")
    url = r2.presign_get("samples/voice.wav", ttl_s=600)
    assert url.startswith("https://acct.r2.cloudflarestorage.com/bucket/samples/voice.wav?") and "X-Amz-Signature=" in url
    assert "X-Amz-Expires=600" in url


def _urlopen_script(monkeypatch, outcomes):
    """urlopen answering each call from `outcomes`: an exception to raise, or a dict to return as JSON."""
    import urllib.error
    calls = []

    class Resp:
        def __init__(self, obj): self.obj = obj
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self, n=-1): return json.dumps(self.obj).encode()

    def fake(req, timeout=None):
        calls.append(req.full_url)
        o = outcomes.pop(0) if len(outcomes) > 1 else outcomes[0]
        if isinstance(o, Exception):
            raise o
        return Resp(o)

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def _http(code):
    import urllib.error
    return urllib.error.HTTPError("https://api.runpod.ai/x", code, "err", {}, io.BytesIO(b"upstream"))


def test_status_retries_a_5xx_and_a_network_error_then_succeeds(monkeypatch):
    import urllib.error
    waits = []
    calls = _urlopen_script(monkeypatch, [_http(502), urllib.error.URLError("reset"), {"status": "COMPLETED"}])
    assert RunPodClient("k", sleep=waits.append).status("ep1", "j9") == {"status": "COMPLETED"}
    assert len(calls) == 3 and waits == [2, 4]


def test_status_gives_up_after_three_tries(monkeypatch):
    waits = []
    calls = _urlopen_script(monkeypatch, [_http(503)])
    with pytest.raises(VoiceError, match="503"):
        RunPodClient("k", sleep=waits.append).status("ep1", "j9")
    assert len(calls) == 3 and waits == [2, 4]


def test_status_does_not_retry_a_4xx(monkeypatch):
    waits = []
    calls = _urlopen_script(monkeypatch, [_http(404), {"status": "COMPLETED"}])
    with pytest.raises(VoiceError, match="404"):
        RunPodClient("k", sleep=waits.append).status("ep1", "j9")
    assert len(calls) == 1 and waits == []


def test_run_is_not_retried(monkeypatch):
    calls = _urlopen_script(monkeypatch, [_http(502), {"id": "j1"}])
    with pytest.raises(VoiceError):
        RunPodClient("k", sleep=lambda s: None).run("ep1", {"op": "speak"}, 1800)     # a retried POST could start two billed jobs
    assert len(calls) == 1


def test_speak_reports_the_execution_time(tmp_path):
    done = {"status": "COMPLETED", "executionTime": 4321, "delayTime": 99, "output": {"rows": [{"id": "vo", "ok": True}]}}
    info = {}
    call(tmp_path, FakeRunPod([done]), info=info)
    assert info == {"execution_ms": 4321}
    info = {}
    call(tmp_path, FakeRunPod([{**done, "executionTime": None}]), info=info)
    assert info == {}                                              # absent: nothing invented
    call(tmp_path, FakeRunPod([done]))                             # info is optional


# ---- the text VoxCPM2 is given: brackets ----

@pytest.mark.parametrize("text,want", [
    ("(softly) Sleep better tonight.", "Sleep better tonight."),                       # a leading style note is dropped with its words
    ("\uff08softly\uff09 Sleep better tonight.", "Sleep better tonight."),               # full-width pair too
    ("  (softly)Sleep better tonight.", "Sleep better tonight."),
    ("Sleep (really) better tonight.", "Sleep, really, better tonight."),                # any other pair: commas around its words
    ("Sleep better (tonight)", "Sleep better, tonight"),
    ("Sleep better [tonight] and \uff08soon\uff09.", "Sleep better, tonight, and, soon."),
    ("Sleep ) better ( tonight .", "Sleep better tonight ."),                          # stray brackets removed, spaces collapsed
    ("Sleep   better\n tonight.", "Sleep better tonight."),
    ("((softly)) Sleep better.", "Sleep better."),                                         # a nested leading note goes whole
    ("(softly) (slowly) Sleep better.", "Sleep better."),
    ("Sleep better. (Really) now.", "Sleep better. Really, now."),                      # no comma after sentence punctuation
    ("Line one.\n(softly) Line two.", "Line one. softly, Line two."),
    ("Sleep {really} better <tonight> \u3010now\u3011.", "Sleep, really, better, tonight, now."),
    ("Sleep ((really)) better.", "Sleep, really, better."),
    ("Sleep better tonight.", "Sleep better tonight."),
])
def test_text_for_voxcpm2_handles_brackets(text, want):
    assert voice.clean_text(text) == want


def test_the_job_carries_the_cleaned_text(tmp_path):
    rp = FakeRunPod([{"status": "COMPLETED", "output": {"rows": [{"id": "vo", "ok": True, "seed": 42, "duration_s": 1.5, "generate_s": 0.7}]}}])
    speak("(softly) Sleep (really) better.", tmp_path / "vo.wav", sample_get="https://r2.test/get/sample.wav", transcript="a sample",
          run="run1", version="V", runpod=rp, r2=FakeR2(), endpoint="ep1", clock=Clock().now, sleep=lambda s: None)
    assert rp.jobs[0][1]["rows"][0]["text"] == "Sleep, really, better."


def test_params_are_exactly_the_three_voxcpm2_ones():
    assert voice.MODEL == "voxcpm2"
    assert set(voice.PARAMS) == {"seed", "cfg_value", "inference_timesteps"}


def test_a_text_that_is_only_a_style_note_is_refused(tmp_path):
    rp = FakeRunPod([])
    with pytest.raises(VoiceError, match="empty"):
        speak("(softly)", tmp_path / "vo.wav", sample_get="x", transcript="t", run="r", version="V", runpod=rp, r2=FakeR2(), endpoint="ep1")
    assert rp.jobs == []


def test_has_brackets_knows_every_bracket_character():
    for ch in "()\uff08\uff09[]{}\u3010\u3011<>":
        assert voice.has_brackets(f"Sleep {ch} better"), ch
    assert not voice.has_brackets("Sleep better, tonight. 100% calm - really?")
