"""voice.py — the voice-over: one speak job on the existing voice-en RunPod endpoint (dots.tts), wav through R2.

The job input and the /status shapes are the voice worker's contract. A whole-job failure is `FAILED` with a top-level
`error`; a job still IN_QUEUE after 10 minutes means the endpoint did not wake (spec §12). The RunPod client and R2
are injected; the defaults are built from the environment only when speak() is called.
"""
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

MODEL = "dots-tts"
PARAMS = {"seed": 42, "num_steps": 10, "guidance_scale": 1.2}      # fixed: part of the server's voice-cache key
USER_AGENT = "ad-versions/0.1 (voice)"
JOB_TTL_S = 1800            # RunPod drops the job after this long
WAKE_WAIT_S = 600           # IN_QUEUE longer than this: the endpoint did not wake
LINK_TTL_S = 1800
STATUS_RETRY_S = (2, 4)     # waits between the three tries of a status poll
_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_MAX_ANSWER = 5 * 1024 * 1024


class VoiceError(Exception):
    pass


class TransientError(VoiceError):
    """A failure worth another try: a 5xx answer or a network error."""


class RunPodClient:
    def __init__(self, api_key, api_base="https://api.runpod.ai/v2", timeout=20, sleep=time.sleep):
        if not api_key:
            raise VoiceError("RunPod API key missing")
        self._key, self.api_base, self.timeout, self._sleep = api_key, api_base.rstrip("/"), timeout, sleep

    def _call(self, method, url, payload=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Authorization": f"Bearer {self._key}",
            "User-Agent": USER_AGENT,                    # Python's default agent gets Cloudflare error 1010
            "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read(_MAX_ANSWER)
        except urllib.error.HTTPError as e:
            kind = TransientError if e.code >= 500 else VoiceError
            raise kind(self._redact(f"RunPod answered {e.code}: {e.read().decode('utf-8', 'replace')[:300]}")) from None
        except (urllib.error.URLError, OSError) as e:
            raise TransientError(self._redact(f"RunPod network error: {getattr(e, 'reason', e)}")) from None
        try:
            obj = json.loads(raw.decode("utf-8")) if raw else {}
        except ValueError:
            obj = None
        if not isinstance(obj, dict):
            raise VoiceError("RunPod sent an answer that is not a JSON object")
        return obj

    def _redact(self, text):
        return text.replace(self._key, "***")

    @staticmethod
    def _id(kind, v):
        if not isinstance(v, str) or not _ID.fullmatch(v):
            raise VoiceError(f"{kind}: 1 to 64 letters, digits, - or _")
        return v

    def run(self, endpoint, input, ttl_s):
        ep = self._id("endpoint id", endpoint)
        obj = self._call("POST", f"{self.api_base}/{ep}/run", {"input": input, "policy": {"ttl": ttl_s * 1000}})
        job = obj.get("id")
        if not isinstance(job, str) or not job:
            raise VoiceError("RunPod sent no job id")
        return job

    def status(self, endpoint, job_id):
        """The job's status JSON. A 5xx or a network error is tried again after 2 s and 4 s (three tries in all) before it fails:
        a poll that fails once must not lose a 30-minute GPU job."""
        url = f"{self.api_base}/{self._id('endpoint id', endpoint)}/status/{self._id('job id', job_id)}"
        for wait in STATUS_RETRY_S:
            try:
                return self._call("GET", url)
            except TransientError:
                self._sleep(wait)
        return self._call("GET", url)

    def cancel(self, endpoint, job_id):
        return self._call("POST", f"{self.api_base}/{self._id('endpoint id', endpoint)}/cancel/{self._id('job id', job_id)}")


class R2:
    """Cloudflare R2 through boto3's S3 client: presigned PUT links for the worker, GET for us."""

    def __init__(self, account_id, access_key_id, secret_access_key, bucket):
        import boto3
        from botocore.config import Config
        self.bucket = bucket
        self._s3 = boto3.client("s3", endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com", region_name="auto",
                                aws_access_key_id=access_key_id, aws_secret_access_key=secret_access_key,
                                config=Config(signature_version="s3v4", s3={"addressing_style": "path"}))

    @property
    def client(self):
        """The boto3 S3 client, for uploads (media/package.py)."""
        return self._s3

    def presign_put(self, key, ttl_s=LINK_TTL_S):
        return self._s3.generate_presigned_url("put_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_s)

    def presign_get(self, key, ttl_s=LINK_TTL_S):
        return self._s3.generate_presigned_url("get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl_s)

    def get(self, key):
        return self._s3.get_object(Bucket=self.bucket, Key=key)["Body"].read()


def _env(*names):
    missing = [n for n in names if not os.environ.get(n)]
    if missing:
        raise VoiceError(f"missing environment: {', '.join(missing)}")
    return [os.environ[n] for n in names]


def runpod_from_env():
    return RunPodClient(*_env("RUNPOD_API_KEY"))


def r2_from_env():
    return R2(*_env("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"))


def _state(st):
    """RunPod /status -> (state, error, output); state: queued | running | done | failed."""
    if not isinstance(st, dict):
        raise VoiceError("RunPod status is not an object")
    s = st.get("status")
    if s == "IN_QUEUE":
        return "queued", None, None
    if s == "IN_PROGRESS":
        return "running", None, None
    if s == "COMPLETED":
        out = st.get("output")
        if isinstance(out, dict) and out.get("error"):
            return "failed", str(out["error"]), None
        return "done", None, out
    if s == "FAILED":
        return "failed", str(st.get("error") or "The GPU job failed."), None
    if s in ("CANCELLED", "TIMED_OUT"):
        return "failed", f"The GPU job was {s.lower().replace('_', ' ')}.", None
    raise VoiceError(f"unknown RunPod status: {s!r}")


def _duration(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        raise VoiceError("the voice-over file is not audio") from None


def stub_speak(text, out_wav, *, voice="slt") -> float:
    """The dry-run voice: ffmpeg's flite filter reads `text` (48 kHz mono WAV). No RunPod, no R2. Whisper hears it word for word."""
    out_wav = Path(out_wav)
    txt = out_wav.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    try:
        r = subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"flite=textfile={txt}:voice={voice}",
                            "-ar", "48000", "-ac", "1", str(out_wav)], capture_output=True, text=True)
    finally:
        txt.unlink(missing_ok=True)
    if r.returncode != 0:
        raise VoiceError("the stand-in voice failed (ffmpeg flite): " + r.stderr.strip()[-200:])
    return _duration(out_wav)


def speak(text, out_wav, *, sample_get, transcript, run, version, runpod=None, r2=None, endpoint=None,
          poll_s=2.0, max_total_s=JOB_TTL_S, clock=time.monotonic, sleep=time.sleep, info=None) -> float:
    """Voice `text` in the sample's voice, write the WAV to out_wav, return its length in seconds. If `info` is a dict, the
    job's RunPod `executionTime` (ms; the GPU time to cost the voice) is put in it as "execution_ms" when the status JSON has it."""
    runpod = runpod or runpod_from_env()
    r2 = r2 or r2_from_env()
    endpoint = endpoint or _env("VOICE_EN_ENDPOINT")[0]
    key = f"runs/{run}/vo/{version}"
    job_input = {"op": "speak", "model": MODEL,
                 "voice": {"sample_get": sample_get, "transcript": transcript, "lang": "en"},
                 "params": dict(PARAMS),
                 "rows": [{"id": "vo", "text": text,
                           "out": {"wav": r2.presign_put(f"{key}.wav"), "json": r2.presign_put(f"{key}.json")}}]}
    job = runpod.run(endpoint, job_input, JOB_TTL_S)
    started = queued_since = clock()
    while True:
        st = runpod.status(endpoint, job)
        state, error, output = _state(st)
        now = clock()
        if state == "failed":
            raise VoiceError(error)
        if state == "done":
            if info is not None and isinstance(st.get("executionTime"), (int, float)):
                info["execution_ms"] = st["executionTime"]
            break
        if state != "queued":
            queued_since = now                     # a worker has it: the queue clock restarts only if it goes back
        if now - queued_since >= WAKE_WAIT_S or now - started >= max_total_s:
            try:
                runpod.cancel(endpoint, job)       # do not leave a job to run (and bill) later
            except Exception:
                pass
            raise VoiceError("voice endpoint did not wake in 10 min" if state == "queued"
                             else f"voice job did not finish in {max_total_s // 60} min")
        sleep(poll_s)
    rows = output.get("rows") if isinstance(output, dict) else None
    row = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else None
    if row is None:
        raise VoiceError("the voice worker's result has no rows")
    if row.get("ok") is not True:
        raise VoiceError(str(row.get("error") or "the voice row failed"))
    out_wav = Path(out_wav)
    out_wav.write_bytes(r2.get(f"{key}.wav"))
    return _duration(out_wav)
