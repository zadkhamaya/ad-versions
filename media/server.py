"""server.py — the media HTTP service n8n calls over the private Docker network: render, frames, check, transcribe, sheet.

Every path in a request is relative to DATA_ROOT (default /data) and must resolve, symlinks followed, inside DATA_ROOT/runs/
(else 400). A render failure is HTTP 500 with {"error", "log"} so n8n's error branch catches it; the server itself never dies.
"""
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal, Optional, Union

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from media import captions, endcard, ffmpeg, match, naming, package, qa, reframe, rulebook, sheet, timeline, voice, whisper
from media.safe import scrub_paths

ROOT = Path(__file__).resolve().parent.parent
RUN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def max_renders() -> int:
    """Renders (and transcriptions) at once. 1 by default: one graded 30 s 16:9 render peaked at 824 MB of the 1.5 GB cap."""
    try:
        return max(1, int(os.environ.get("MAX_RENDERS", "1")))
    except ValueError:
        return 1


RENDER_SLOTS = threading.BoundedSemaphore(max_renders())
_vo_locks, _vo_guard = {}, threading.Lock()
_reframe_lock = threading.Lock()   # runs/<id>/reframe.json is read, extended and rewritten under this
_pkg_locks = {}                    # run_id -> Lock: one /package build at a time per run (a second one is a 409)
SLOTS = ("slot1", "slot2", "slot3")
LENGTHS = (6, 15, 30)
FPS = 24

def _preload_whisper():
    try:
        whisper.preload()
    except Exception:                  # offline at start: the first transcription tries again
        traceback.print_exc()


@asynccontextmanager
async def lifespan(app):
    """PRELOAD_WHISPER=1: load the model in the background at startup, so the first render does not pay the download/load
    inside its slot (a request that needs it meanwhile waits on the loader's lock)."""
    if os.environ.get("PRELOAD_WHISPER") == "1":
        threading.Thread(target=_preload_whisper, name="whisper-preload", daemon=True).start()
    yield


app = FastAPI(title="ad-versions media service", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
def _invalid(request: Request, exc: RequestValidationError):
    """422 as FastAPI sends it, minus each error's `input` and `ctx`: an inf/nan input cannot be written as JSON (that was a 500)."""
    return JSONResponse({"detail": [{"type": e["type"], "loc": list(e["loc"]), "msg": e["msg"]} for e in exc.errors()]}, status_code=422)


@app.exception_handler(Exception)
def _unexpected(request: Request, exc: Exception):
    """Anything not handled below is a 500 with {"error": "<type>: <message>"}: JSON for n8n, no traceback, no absolute paths."""
    traceback.print_exception(exc)
    return JSONResponse({"error": scrub_paths(f"{type(exc).__name__}: {exc}")[:600]}, status_code=500)


# ---- paths ----

def data_root() -> Path:
    return Path(os.environ.get("DATA_ROOT", "/data")).resolve()


def resolve(rel: str, *, must_exist=True, run=None, brand=False) -> tuple[Path, str]:
    """(absolute path, run_id) of a request path, symlinks followed: inside DATA_ROOT/runs/<run_id>/ (that run's, when `run` is
    given), or, with brand=True, a read-only input inside DATA_ROOT/brand/ (run_id ""); anything else is 400."""
    root = data_root()
    p = Path(rel)
    if p.is_absolute() or "\0" in rel:
        raise HTTPException(400, f"path {rel!r} must be relative to the data root")
    full = (root / p).resolve()
    run_id = None
    if brand:
        try:
            full.relative_to((root / "brand").resolve())
            run_id = ""
        except ValueError:
            pass
    if run_id is None:
        try:
            parts = full.relative_to((root / "runs").resolve()).parts
        except ValueError:
            raise HTTPException(400, f"path {rel!r} is outside runs/{' and brand/' if brand else ''}") from None
        if len(parts) < 2 or not RUN_ID.fullmatch(parts[0]):
            raise HTTPException(400, f"path {rel!r} is not inside a run folder (runs/<run_id>/...)")
        if run is not None and parts[0] != run:
            raise HTTPException(400, f"path {rel!r} belongs to run {parts[0]!r}, not {run!r}")
        run_id = parts[0]
    if must_exist and not full.is_file():
        raise HTTPException(404, f"no such file: {rel}")
    return full, run_id


def rel(path: Path) -> str:
    return path.relative_to(data_root()).as_posix()


# ---- request models ----

Fraction = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]


class BeatIn(BaseModel):
    """face: left | centre | center | right | auto, or a number 0..1 (the crop window's centre as a fraction of the source width).
    x: a per-format centre ({"9X16": 0.2, "1X1": 0.4}) that wins over `face` for that format."""
    name: str = Field(min_length=1, max_length=20)
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(allow_inf_nan=False)
    role: str = ""
    face: Union[Literal["left", "centre", "right", "auto"], Fraction] = "centre"
    x: Optional[dict[Literal["9X16", "1X1"], Fraction]] = None

    @field_validator("face", mode="before")
    @classmethod
    def _spelling(cls, v):
        return "centre" if v == "center" else v          # American spelling accepted

    @model_validator(mode="after")
    def _ordered(self):
        if self.end <= self.start:
            raise ValueError(f"beat {self.name}: end must be after start")
        return self


class BrollIn(BaseModel):
    wide: str = Field(min_length=1)       # the 1344x768 clip, used for 16X9 and 1X1
    tall: str = Field(min_length=1)       # the 768x1344 clip, used for 9X16


class RenderIn(BaseModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    version: str
    source: str
    beats: list[BeatIn] = Field(min_length=1)
    broll: dict[str, BrollIn] = {}        # {"slot1": {"wide": path, "tall": path}, ...}; a missing slot drops that cutaway
    vo_text: str = Field(min_length=1, max_length=2000)
    disclaimer: str = Field(min_length=1, max_length=400)
    music: str
    logo: Optional[str] = None            # a PNG; absent: the brand name is drawn as text
    brand: Optional[str] = Field(default=None, min_length=1, max_length=40)

    @field_validator("version")
    @classmethod
    def _version(cls, v):
        naming.parse_version(v)           # ValueError -> 422 with the reason
        return v

    @field_validator("broll")
    @classmethod
    def _slots(cls, v):
        bad = [k for k in v if k not in SLOTS]
        if bad:
            raise ValueError(f"unknown slot(s) {bad}; expected {list(SLOTS)}")
        return v


class FramesIn(BaseModel):
    file: str
    n: Optional[int] = Field(default=None, ge=1, le=12)
    at: Optional[Union[Literal["last"], list[float]]] = None

    @model_validator(mode="after")
    def _one_form(self):
        if (self.n is None) == (self.at is None):
            raise ValueError('give exactly one of "n" or "at"')
        if isinstance(self.at, list) and not 1 <= len(self.at) <= 20:
            raise ValueError('"at" needs 1 to 20 times')
        if isinstance(self.at, list) and any(t < 0 for t in self.at):
            raise ValueError('"at" times must not be negative')
        return self


class CheckIn(BaseModel):
    """What to check a file against: `version` (H1_15S_9X16) or `length_s` + `fmt`; given together they must agree."""
    file: str
    version: Optional[str] = None
    length_s: Optional[int] = None
    fmt: Optional[Literal["9X16", "1X1", "16X9"]] = None

    @field_validator("length_s")
    @classmethod
    def _len(cls, v):
        if v is not None and v not in LENGTHS:
            raise ValueError(f"length_s must be one of {LENGTHS}")
        return v

    @model_validator(mode="after")
    def _expected(self):
        if self.version is not None:
            _, length_s, fmt = naming.parse_version(self.version)          # ValueError -> 422
            if self.length_s not in (None, length_s) or self.fmt not in (None, fmt):
                raise ValueError(f"length_s/fmt {self.length_s}/{self.fmt} disagree with version {self.version}")
            self.length_s, self.fmt = length_s, fmt
        elif self.length_s is None or self.fmt is None:
            raise ValueError('give "version", or both "length_s" and "fmt"')
        return self


class FileIn(BaseModel):
    file: str


class TranscribeIn(BaseModel):
    """script: the approved voice-over text to compare with (the final-audio check); version only labels the answer."""
    file: str
    script: Optional[str] = Field(default=None, min_length=1, max_length=2000)
    version: Optional[str] = None

    @field_validator("version")
    @classmethod
    def _version(cls, v):
        if v is not None:
            naming.parse_version(v)
        return v


class LintIn(BaseModel):
    texts: list[str] = Field(max_length=200)


class SheetIn(BaseModel):
    files: list[str] = Field(min_length=1, max_length=18)
    out: str

    @field_validator("out")
    @classmethod
    def _jpg(cls, v):
        if not v.lower().endswith(".jpg"):
            raise ValueError("out must be a .jpg path")
        return v


class RunIn(BaseModel):
    """The run's facts for the package: names, cost inputs, times; link/cta/objective/lang feed the Meta CSV."""
    model_config = ConfigDict(extra="allow")
    brand: str = Field(min_length=1, max_length=40)
    campaign: str = Field(min_length=1, max_length=60)
    started_at: str
    finished_at: str
    disclaimer: Optional[str] = Field(default=None, min_length=1, max_length=400)    # the one burned into the end card
    gpu_minutes: float = Field(ge=0, allow_inf_nan=False)
    gpu_rate_usd_h: float = Field(ge=0, allow_inf_nan=False)
    voice_usd: float = Field(ge=0, allow_inf_nan=False)
    llm_usd: float = Field(ge=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def _times(self):
        package.turnaround_min(self.model_dump())    # ValueError -> 422: time zones present, finished not before started
        return self


class ApprovedIn(BaseModel):
    version: str
    file: str                                # runs/<run_id>/renders/<file>.mp4
    hook_text: str
    script: str
    length_s: Optional[int] = None
    fmt: Optional[str] = None
    qa: Optional[dict] = None
    v: int = Field(default=1, ge=1, le=99)          # the V number in the file name: rises when the same name is rendered again
    approver: str = ""
    approved_at: str = ""

    @field_validator("version")
    @classmethod
    def _version(cls, v):
        naming.parse_version(v)
        return v


class RejectedIn(BaseModel):
    version: str
    note: str = ""


class PackageIn(BaseModel):
    run_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    run: RunIn
    approved: list[ApprovedIn]
    rejected: list[RejectedIn] = []
    upload: bool = True                      # false: build the folder only (n8n's dry run)


# ---- render ----

def _rulebook_brand():
    try:
        return rulebook.load(ROOT / "brand" / "rulebook.yaml").brand
    except Exception:
        return "BRAND"


def _env_int(name, default):
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _grade(segs, source, edit, log):
    """Set each b-roll segment's grade from the source segment before it; log the colour distance before and after."""
    cfg = edit["broll_match"]
    graded, refs = [], {}
    for i, s in enumerate(segs):
        if s.kind != "broll":
            graded.append(s)
            continue
        prev = next((p for p in reversed(segs[:i]) if p.kind == "src"), None) or next(p for p in segs if p.kind == "src")
        ref = match.stats(source, prev.start, prev.dur)
        clip = match.stats(s.path, 0.0, min(s.dur, ffmpeg.duration(s.path)))
        graded.append(dataclasses.replace(s, grade=match.transfer_filter(clip, ref, cfg["grain"], cfg["sharpen"])))
        refs[s.slot] = ref
        log["broll_match"][s.slot] = {"before": match.distance(clip, ref), "after": None}
    return graded, refs, cfg["max_diff"]


def _inputs(req: RenderIn, fmt: str):
    """Every input path of a render request, resolved and checked: this run's own files (400 otherwise, 404 if missing);
    music and logo may also come from DATA_ROOT/brand/ (read-only)."""
    size = "tall" if fmt == "9X16" else "wide"
    for b in req.broll.values():                      # both sizes are checked, whichever the format uses
        resolve(b.wide, run=req.run_id), resolve(b.tall, run=req.run_id)
    return (resolve(req.source, run=req.run_id)[0],
            {slot: str(resolve(getattr(b, size), run=req.run_id)[0]) for slot, b in req.broll.items()},
            resolve(req.music, run=req.run_id, brand=True)[0],
            resolve(req.logo, run=req.run_id, brand=True)[0] if req.logo else None)


def _prepare(req: RenderIn):
    """Everything that can be refused before a slot is taken: paths, beats for the edit, the plan (422 for bad beats)."""
    hook, length_s, fmt = naming.parse_version(req.version)
    inputs = _inputs(req, fmt)
    edit = timeline.load_edit(ROOT / "config" / "edit.yaml")
    src_s = ffmpeg.duration(inputs[0])
    past = [b.name for b in req.beats if b.end > src_s + 0.05]
    if past:
        raise HTTPException(422, f"beat(s) {', '.join(past)} end after the source clip's {src_s:.2f} s")
    beats = {b.name: timeline.Beat(b.name, b.start, b.end, b.role, b.face, dict(b.x or {})) for b in req.beats}
    missing = [n for n in edit[hook][length_s] if not n.startswith("slot") and n not in beats]
    if missing:
        raise HTTPException(422, f"beats missing for {req.version}: {', '.join(missing)}")
    try:
        segs = timeline.plan(beats, str(inputs[0]), inputs[1], hook, length_s, edit, fps=FPS, fmt=fmt)
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    try:
        endcard.fit(fmt, req.disclaimer, inputs[3], req.brand or _rulebook_brand())
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return fmt, inputs, edit, segs


def _voice(req: RenderIn, run_dir: Path, work: Path, log: dict) -> Path:
    """The voice-over WAV, cached per run by a hash of script + voice sample + transcript + model + params: the 18 versions share 6 scripts and the voice seed is fixed.
    One lock per script, so two renders asking at once make one voice job; a failed job caches nothing.
    The log gets `vo_cached` and, for a job that ran, `vo_execution_ms` (RunPod's executionTime, to cost the voice)."""
    # the key covers everything that makes the audio: the script, the voice sample and what it says, the model and its params
    key = json.dumps([req.vo_text, os.environ.get("VOICE_SAMPLE_KEY"), os.environ.get("VOICE_SAMPLE_TRANSCRIPT"), voice.MODEL,
                      voice.PARAMS, os.environ.get("VOICE_EN_ENDPOINT")], sort_keys=True)
    sha = hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    cached = run_dir / "vo" / f"{sha}.wav"
    with _vo_guard:
        lock = _vo_locks.setdefault((req.run_id, sha), threading.Lock())
    with lock:
        log["vo_cached"] = cached.exists()
        if not log["vo_cached"]:
            sample_key, transcript = os.environ.get("VOICE_SAMPLE_KEY"), os.environ.get("VOICE_SAMPLE_TRANSCRIPT")
            if not sample_key or not transcript:
                raise RuntimeError("missing environment: " + ", ".join(
                    n for n in ("VOICE_SAMPLE_KEY", "VOICE_SAMPLE_TRANSCRIPT") if not os.environ.get(n)))
            r2 = voice.r2_from_env()
            tmp = work / "vo.wav"
            info = {}
            voice.speak(req.vo_text, tmp, sample_get=r2.presign_get(sample_key), transcript=transcript, run=req.run_id,
                        version=req.version, r2=r2, info=info)
            if "execution_ms" in info:
                log["vo_execution_ms"] = info["execution_ms"]
            cached.parent.mkdir(parents=True, exist_ok=True)
            os.replace(tmp, cached)
    return cached


def _reframe_key(source: Path, beat, fmt: str) -> str:
    """A suggestion is good for one source file (its path, size and mtime), one beat (name and span) and one format."""
    st = source.stat()
    return "|".join([rel(source), str(st.st_size), str(st.st_mtime_ns), beat.name, f"{beat.start:.3f}", f"{beat.end:.3f}", fmt])


def _suggestions(source: Path, run_dir: Path, beat, fmts) -> dict:
    """{fmt: suggestion} of a beat: from runs/<id>/reframe.json where the same source, beat and format was read before, else one pass
    over the frames for the rest (and into the cache). A cache that cannot be read is ignored."""
    path = run_dir / "reframe.json"
    with _reframe_lock:
        try:
            cache = json.loads(path.read_text())
            cache = cache if isinstance(cache, dict) else {}
        except (OSError, ValueError):
            cache = {}
        keys = {f: _reframe_key(source, beat, f) for f in fmts}
        todo = [f for f in fmts if keys[f] not in cache]
        if todo:
            cache.update({keys[f]: v for f, v in reframe.suggest_formats(str(source), beat.start, beat.end, todo).items()})
            run_dir.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f"reframe.{threading.get_ident()}.tmp")
            tmp.write_text(json.dumps(cache, indent=1, sort_keys=True))
            os.replace(tmp, path)
        return {f: cache[keys[f]] for f in fmts}


def _resolve_auto(segs, beats, source: Path, run_dir: Path, fmt: str, log: dict):
    """Each source segment whose face is "auto" gets reframe.suggest's x for its beat and this format; logged under "reframe",
    with a render-log reason when the action splits. A window as wide as the frame has nothing to place: centre, no suggestion."""
    out = []
    for s in segs:
        if s.kind != "src" or s.face != "auto":
            out.append(s)
            continue
        w, h = ffmpeg._size(str(source))
        if ffmpeg.crop_box(w, h, fmt)[0] >= w:
            out.append(dataclasses.replace(s, face="centre"))
            continue
        sug = _suggestions(source, run_dir, beats[s.beat], [fmt])[fmt]
        log.setdefault("reframe", {}).setdefault(s.beat, {})[fmt] = sug
        if sug["split"]:
            reason = f"crop split action in {s.beat} ({fmt})"
            if reason not in log["reasons"]:
                log["reasons"].append(reason)
        out.append(dataclasses.replace(s, face=sug["x"]))
    return out


def _render(req: RenderIn, prepared, run_dir: Path, work: Path, out_path: Path, log: dict) -> None:
    fmt, inputs, edit, segs = prepared
    _, _, music, logo = inputs
    source = inputs[0]
    threads = _env_int("X264_THREADS", 2)
    endcard_s = float(edit["endcard_s"])
    segs = _resolve_auto(segs, {b.name: b for b in req.beats}, source, run_dir, fmt, log)
    log["segments"] = [{"kind": s.kind, "slot": s.slot, "start": round(s.start, 3), "dur": round(s.dur, 4)} for s in segs]
    log["broll_match"] = {}
    segs, refs, max_diff = _grade(segs, str(source), edit, log)

    png = work / "endcard.png"
    log["endcard"] = endcard.make(fmt, req.disclaimer, png, logo=logo, brand=req.brand or _rulebook_brand())
    picture = work / "picture.mp4"
    ffmpeg.render_picture(segs, fmt, str(png), str(picture), fps=FPS, endcard_s=endcard_s, threads=threads)

    at = 0.0
    for s in segs:                                   # the cutaway as rendered, against the same reference
        if s.kind == "broll":
            after = match.distance(match.stats(str(picture), at, s.dur), refs[s.slot])
            log["broll_match"][s.slot]["after"] = after
            if after > max_diff:
                log["reasons"].append("b-roll colour")
        at += s.dur

    vo = _voice(req, run_dir, work, log)
    log["vo_s"] = round(ffmpeg.duration(vo), 3)
    before_card = naming.parse_version(req.version)[1] - endcard_s
    if log["vo_s"] > before_card:              # finish() trims the audio to the picture: the last word would be cut
        log["reasons"].append(f"voice-over {log['vo_s']:.1f} s, longer than the {before_card:.1f} s before the end card")
    heard = whisper.words(str(vo))
    words, wer = captions.from_script(req.vo_text, heard)      # the approved script on screen, timed by what Whisper heard
    log["caption_wer"] = round(wer, 2)
    if words is None:
        words = heard
        log["reasons"].append("captions from transcript (script alignment failed)")
    ass = work / "captions.ass"
    ass.write_text(captions.to_ass(words, fmt))
    room = work / "room.wav"
    ffmpeg.room_tone(segs, endcard_s, str(room))
    final = work / "final.mp4"
    log["finish"] = ffmpeg.finish(str(picture), str(vo), str(music), str(ass), str(final), room=str(room), threads=threads)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    os.replace(final, out_path)


@app.post("/render")
def render(req: RenderIn):
    log = {"version": req.version, "reasons": []}
    out_path, _ = resolve(f"runs/{req.run_id}/renders/{req.version}.mp4", must_exist=False)
    run_dir = data_root() / "runs" / req.run_id
    out_path.unlink(missing_ok=True)            # a failed or refused re-render must not leave the old file for /check to pass
    prepared = _prepare(req)                    # a bad path, bad beats: 400/422 before any slot is taken
    run_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix=f"work-{req.version}-", dir=run_dir))
    try:
        with RENDER_SLOTS:
            _render(req, prepared, run_dir, work, out_path, log)
    except HTTPException:
        raise
    except Exception as e:                      # n8n's error branch reads {"error", "log"}
        traceback.print_exc()
        return JSONResponse({"error": scrub_paths(f"{type(e).__name__}: {e}")[:600], "log": log}, status_code=500)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return {"file": rel(out_path), "log": log}


# ---- the other endpoints ----

def _frame(src: Path, t: float, out: Path):
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(src), "-frames:v", "1", "-q:v", "3", str(out)],
                   check=True, capture_output=True)


@app.post("/frames")
def frames(req: FramesIn):
    src, run_id = resolve(req.file)
    dur = ffmpeg.duration(src)
    last = max(dur - 1.5 / FPS, 0.0)
    if req.n is not None:
        times = [(f"n{i + 1}", dur * (i + 0.5) / req.n) for i in range(req.n)]
    elif req.at == "last":
        times = [("last", last)]
    else:
        far = [t for t in req.at if t > dur + 0.05]
        if far:
            raise HTTPException(422, f"at {far}: beyond the file's {dur:.2f} s")
        times = [(f"t{t:.2f}", min(t, last)) for t in req.at]
    out_dir = data_root() / "runs" / run_id / "frames"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    uid = hashlib.sha256(rel(src).encode("utf-8")).hexdigest()[:6]      # two clips of one name in different folders must not collide
    for tag, t in times:
        out = out_dir / f"{src.stem}_{uid}_{tag}.jpg"
        _frame(src, t, out)
        files.append(rel(out))
    return {"files": files}


@app.post("/check")
def check(req: CheckIn):
    path, _ = resolve(req.file)
    return qa.check(str(path), {"length_s": req.length_s, "fmt": req.fmt})


def _rulebook() -> rulebook.Rulebook:
    return rulebook.load(ROOT / "brand" / "rulebook.yaml")


@app.post("/transcribe")
def transcribe(req: TranscribeIn):
    """{"text", "banned"}; with a `script` also {"wer", "passed", "reasons"}: passed = word error rate <= qa.MAX_WER and no banned word."""
    path, _ = resolve(req.file)
    if not ffmpeg._has_audio(path):
        raise HTTPException(422, f"{req.file} has no audio stream")
    with RENDER_SLOTS:                           # the model's memory counts against the same cap as a render
        text = whisper.text(str(path))
    out = {"text": text, "banned": rulebook.hard_hits(text, _rulebook())}
    if req.script is not None:
        wer = qa.script_diff(req.script, text)
        reasons = []
        if wer > qa.MAX_WER:
            reasons.append(f"voice-over differs from the script (word error rate {wer:.2f}, over {qa.MAX_WER:g})")
        if out["banned"]:
            reasons.append(f"banned word(s) in the audio: {', '.join(out['banned'])}")
        out.update(wer=round(wer, 2), passed=not reasons, reasons=reasons)
    return out


@app.get("/rulebook")
def get_rulebook():
    return dataclasses.asdict(_rulebook())


@app.get("/edit")
def get_edit():
    """config/edit.yaml as JSON (the lengths 6/15/30 come as the strings "6"/"15"/"30", JSON keys being strings)."""
    return timeline.load_edit(ROOT / "config" / "edit.yaml")


@app.post("/lint")
def lint(req: LintIn):
    rb = _rulebook()
    return {"hits": [rulebook.hard_hits(t, rb) for t in req.texts]}


@app.post("/sheet")
def contact_sheet(req: SheetIn):
    files = [str(resolve(f, must_exist=False)[0]) for f in req.files]      # a failed render has no file: its cell says "no frame"
    out, _ = resolve(req.out, must_exist=False)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        sheet.make(files, str(out))
    except ValueError as e:
        raise HTTPException(422, str(e)) from None
    return {"file": rel(out)}


@app.post("/package")
def make_package(req: PackageIn):
    if req.upload and not req.approved:
        raise HTTPException(422, "nothing approved: there is no package to upload")
    renders = (data_root() / "runs" / req.run_id / "renders").resolve()
    approved = []
    for a in req.approved:
        path, _ = resolve(a.file, run=req.run_id)
        if path.parent != renders:
            raise HTTPException(400, f"file {a.file!r} is not in runs/{req.run_id}/renders/")
        if path.stem != a.version:
            raise HTTPException(422, f"file {a.file!r}: its name {path.stem!r} must equal the version {a.version!r}")
        approved.append({**a.model_dump(), "file": str(path)})
    out_dir = data_root() / "runs" / req.run_id / "package"
    with _vo_guard:
        lock = _pkg_locks.setdefault(req.run_id, threading.Lock())
    if not lock.acquire(blocking=False):
        raise HTTPException(409, f"a package for run {req.run_id!r} is already being built")
    try:                                         # the lock is held through the upload too: it reads out_dir
        try:
            res = package.build(req.run.model_dump(), approved, [r.model_dump() for r in req.rejected], out_dir)
        except (ValueError, KeyError) as e:      # a name part that cleans to nothing, a bad timestamp, a bad meta_columns.yaml
            raise HTTPException(422, str(e)) from None
        res["folder"] = rel(out_dir)
        if req.upload:
            try:
                bucket = os.environ.get("R2_BUCKET")
                if not bucket:
                    raise RuntimeError("missing environment: R2_BUCKET")
                res["link"] = package.upload(out_dir, bucket, f"runs/{req.run_id}/package")
            except Exception as e:
                traceback.print_exc()
                return JSONResponse({"error": scrub_paths(f"{type(e).__name__}: {e}")[:600], "folder": res["folder"]}, status_code=500)
    finally:
        lock.release()
    return res


@app.get("/health")
async def health():                              # async: it runs on the event loop, so queued renders in the threadpool cannot block it
    return {"ok": True}
