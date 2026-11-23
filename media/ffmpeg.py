"""ffmpeg.py — builds and runs the ffmpeg commands: crop, cut, picture render, end card, finish (mix + captions)."""
import json
import re
import subprocess

from media.naming import FORMATS


def crop_box(src_w, src_h, fmt):
    """(cw, ch, y) of the crop window for `fmt` on a src_w x src_h frame: the widest/tallest window of the format's shape, centred on the cropped axis."""
    W, H = FORMATS[fmt]
    target = W / H
    if src_w / src_h > target:                      # source wider: crop width
        return int(round(src_h * target / 2) * 2), src_h, 0
    ch = int(round(src_w / target / 2) * 2)         # source taller: crop height
    return src_w, ch, (src_h - ch) // 2


def crop_x(src_w, cw, face):
    """Left edge of a cw-wide window in a src_w-wide frame. `face`: "left" | "centre" | "right", or a number 0..1 = the window's
    centre as a fraction of the width, clamped so the window stays inside the frame. Even (4:2:0 chroma), so the number gives an exact crop."""
    if face == "auto":
        raise ValueError('face "auto" must be resolved to a number (media.reframe.suggest) before the crop')
    if isinstance(face, str):
        return {"left": 0, "centre": (src_w - cw) // 2, "right": src_w - cw}[face]
    x = int(round((float(face) * src_w - cw / 2) / 2) * 2)
    return max(0, min(src_w - cw, x))


def crop_filter(src_w, src_h, fmt, face):
    W, H = FORMATS[fmt]
    cw, ch, y = crop_box(src_w, src_h, fmt)
    x = crop_x(src_w, cw, face) if cw < src_w else 0     # a window as wide as the frame has nowhere to move
    return f"crop={cw}:{ch}:{x}:{y},scale={W}:{H},setsar=1"


def _size(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", path], capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(",")[:2]
    return int(w), int(h)


def render_picture(segments, fmt, endcard_png, out_path, fps=24, endcard_s=1.5, threads=None):
    """Silent picture: every segment cropped/graded onto the 24 fps grid, then the end card. `threads` caps x264 (-threads N)."""
    W, H = FORMATS[fmt]
    args, chains = [], []
    for i, s in enumerate(segments):
        args += ["-ss", f"{s.start:.3f}", "-t", f"{s.dur:.3f}", "-i", s.path]
        sw, sh = _size(s.path)
        grade = f",{s.grade}" if s.grade else ""
        chains.append(f"[{i}:v]{crop_filter(sw, sh, fmt, s.face)}{grade},fps={fps},tpad=stop_mode=clone:stop_duration={s.dur:.3f},trim=duration={s.dur:.3f},setpts=PTS-STARTPTS[v{i}]")
    n = len(segments)
    args += ["-loop", "1", "-t", f"{endcard_s:.3f}", "-i", endcard_png]
    chains.append(f"[{n}:v]scale={W}:{H},setsar=1,fps={fps},trim=duration={endcard_s:.3f},setpts=PTS-STARTPTS[v{n}]")
    concat = "".join(f"[v{i}]" for i in range(n + 1)) + f"concat=n={n + 1}:v=1:a=0[out]"
    total = sum(s.dur for s in segments) + endcard_s
    cmd = ["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", ";".join(chains + [concat]), "-map", "[out]",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps), "-frames:v", str(int(round(total * fps))),
           *(["-threads", str(threads)] if threads else []), out_path]
    subprocess.run(cmd, check=True)
    return cmd


def _has_audio(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0",
                          str(path)], capture_output=True, text=True).stdout
    return "audio" in out


def room_tone(segments, endcard_s, out_wav):
    """The source clip's own sound cut to the picture's timeline (the file `finish` takes as `room`): each source segment
    contributes its span of the source audio, b-roll cutaways and the end card are silence. 48 kHz stereo WAV as long as the picture."""
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    args, chains, labels = [], [], []
    for i, s in enumerate(segments):
        if s.kind == "src" and _has_audio(s.path):
            n = len([a for a in args if a == "-i"])
            args += ["-ss", f"{s.start:.3f}", "-t", f"{s.dur:.3f}", "-i", s.path]
            chains.append(f"[{n}:a:0]{fmt},apad=whole_dur={s.dur:.3f},atrim=duration={s.dur:.3f},asetpts=PTS-STARTPTS[a{i}]")
        else:
            chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={s.dur:.3f},asetpts=PTS-STARTPTS[a{i}]")
        labels.append(f"[a{i}]")
    n = len(segments)
    chains.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={endcard_s:.3f},asetpts=PTS-STARTPTS[a{n}]")
    labels.append(f"[a{n}]")
    chains.append("".join(labels) + f"concat=n={n + 1}:v=0:a=1[out]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args, "-filter_complex", ";".join(chains), "-map", "[out]", "-c:a", "pcm_s16le",
                    str(out_wav)], check=True)


def duration(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True, check=True).stdout.strip()
    return float(out)


SILENCE_DB = -80.0           # a mean level under this is silence (digital zeros read about -91 dB)


def _mean_volume(path, pre=""):
    """Mean level of an audio file in dB (volumedetect, after the optional filter chain `pre`), or None for silence
    (nothing measured, -inf, or at volumedetect's digital-silence floor of about -91 dB: below SILENCE_DB)."""
    af = f"{pre},volumedetect" if pre else "volumedetect"
    r = subprocess.run(["ffmpeg", "-hide_banner", "-i", str(path), "-vn", "-af", af, "-f", "null", "-"],
                       capture_output=True, text=True)
    if "mean_volume:" not in r.stderr:
        return None
    v = r.stderr.split("mean_volume:")[1].split("dB")[0].strip()
    return None if v in ("-inf", "") or float(v) < SILENCE_DB else float(v)


# the speech part only: drops the silent stretches, so a voice with long pauses is not read as quieter than it is
_SPEECH_ONLY = ("silenceremove=start_periods=1:start_silence=0.1:start_threshold=-50dB"
                ":stop_periods=-1:stop_silence=0.3:stop_threshold=-50dB")

def filter_path(p):
    """A path as a filter option value: escaped for the option parser, then again for the filtergraph parser."""
    def esc(s, chars):
        return "".join("\\" + c if c in chars else c for c in s)
    return esc(esc(str(p), "\\:'"), "\\',;[]=")


ROOM_BELOW_VOICE_DB = 30.0   # the source clip's room tone sits this far under the voice's speech level (spec 4.3)
LRA_MIN = 11
TP_TARGET = -1.5
MAX_ATTEMPTS = 3            # first render plus at most two re-renders for a true peak over the limit
TP_LIMIT = -1.0              # the file must not exceed this after the AAC encode (spec 4.2)
LIMITER_FLOOR_DB = -24.0     # alimiter takes limits down to 1/16 of full scale
_JSON = re.compile(r"\{[^{}]*\}")


def _last_json(text, key):
    blocks = [m for m in _JSON.findall(text) if f'"{key}"' in m]
    if not blocks:
        raise RuntimeError(f"ffmpeg printed no loudnorm measurement:\n{text[-600:]}")
    return json.loads(blocks[-1])


def measure_output(path):
    """Integrated loudness and true peak of a finished file (what QA will see)."""
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af", "loudnorm=print_format=json",
                        "-f", "null", "-"], capture_output=True, text=True, check=True)
    j = _last_json(r.stderr, "input_i")
    return {"i": float(j["input_i"]), "tp": float(j["input_tp"])}


def finish(picture, vo_wav, music, ass_path, out_path, loud=-14.0, room=None, threads=None):
    """Burn the captions into the picture and mix voice + music (ducked under the voice) + optional room tone,
    then normalise to `loud` LUFS (linear loudnorm from a measuring pass) with the true peak checked on the encoded file.
    One video encode (re-rendered, at most twice, while the file's true peak is over -1 dBTP). Every input is padded to the
    picture's length, so the audio runs as long as the picture (sidechaincompress stops when its key ends).
    Room tone, when given, is set to `ROOM_BELOW_VOICE_DB` under the voice's mean level over its non-silent part, measured
    as stereo (as it is in the mix); a silent voice or a silent room tone drops the room, and the log says so.
    `threads` (None = ffmpeg's default) caps the x264 encode with -threads N.
    Returns, for the render log: "cmds" (every ffmpeg command run), "measured" (the final pass's loudnorm input measurement),
    "mode" (loudnorm's normalization_type, "linear" expected), "limiter_db" (peak limit used, None = no limiter), "tp_target"
    (the true-peak target of the final attempt), "output" ({"i", "tp"} measured on the encoded file), "room" ("mixed", "not given",
    "skipped: silent voice" or "skipped: silent room tone"), "attempts" (1-3) and "history" (one such result per attempt)."""
    dur = duration(picture)
    inputs = ["-i", str(picture), "-i", str(vo_wav), "-i", str(music)]
    fmt = "aformat=sample_rates=48000:channel_layouts=stereo"
    pad = f"apad=whole_dur={dur:.3f}"
    graph = (f"[1:a]{fmt},{pad},asplit=2[vo][key];"
             f"[2:a]{fmt},{pad},volume=0.25[m];"
             "[m][key]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[duck]")
    mix_in, n = "[vo][duck]", 2
    room_log = "not given"
    if room is not None:
        # the voice's level as it is in the mix: measured after the stereo upmix (a mono file loses 3 dB to it)
        up = "aformat=channel_layouts=stereo"
        room_mean = _mean_volume(room, up)
        vo_mean = _mean_volume(vo_wav, f"{up},{_SPEECH_ONLY}")
        if vo_mean is None:
            vo_mean = _mean_volume(vo_wav, up)
        if vo_mean is None:
            room_log = "skipped: silent voice"
        elif room_mean is None:
            room_log = "skipped: silent room tone"
        else:
            inputs += ["-i", str(room)]
            graph += f";[3:a]{fmt},{pad},volume={vo_mean - ROOM_BELOW_VOICE_DB - room_mean:.2f}dB[rm]"
            mix_in, n = mix_in + "[rm]", 3
            room_log = "mixed"
    head = graph

    def mix(limit_db):
        lim = "" if limit_db is None else f",alimiter=limit={10 ** (limit_db / 20):.5f}:level=0:attack=5:release=150"
        return f"{head};{mix_in}amix=inputs={n}:duration=longest:normalize=0,atrim=duration={dur:.3f},asetpts=PTS-STARTPTS{lim}[mix]"

    cmds = []

    def run_measure(limit_db, target):
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-y", *inputs, "-filter_complex",
               f"{mix(limit_db)};[mix]loudnorm={target}:print_format=json[a]", "-map", "[a]", "-f", "null", "-"]
        cmds.append(cmd)
        return _last_json(subprocess.run(cmd, capture_output=True, text=True, check=True).stderr, "input_i")

    def attempt(tp):
        target = f"I={loud}:TP={tp}:LRA={LRA_MIN}"
        limit_db, m = None, run_measure(None, target)
        for _ in range(6):   # linear needs measured TP + gain <= target TP: limit the peaks until it does
            over = float(m["input_tp"]) + (loud - float(m["input_i"])) - (tp - 0.2)
            if over <= 0:
                break
            if limit_db is None:
                limit_db = tp - (loud - float(m["input_i"])) - 0.5
            else:
                limit_db -= over + 0.3
            limit_db = max(limit_db, LIMITER_FLOOR_DB)
            m = run_measure(limit_db, target)
        lra = min(max(LRA_MIN, int(float(m["input_lra"])) + 2), 50)    # a higher LRA target keeps loudnorm linear
        ln = (f"I={loud}:TP={tp}:LRA={lra}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
              f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true:print_format=json")
        vf = f"[0:v]subtitles={filter_path(ass_path)}:fontsdir=/usr/share/fonts[v]"
        cmd = ["ffmpeg", "-hide_banner", "-nostats", "-y", *inputs, "-filter_complex", f"{mix(limit_db)};[mix]loudnorm={ln}[a];{vf}",
               "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
               *(["-threads", str(threads)] if threads else []), "-t", f"{dur:.3f}", "-movflags", "+faststart", str(out_path)]
        cmds.append(cmd)
        r = subprocess.run(cmd, capture_output=True, text=True, check=True)
        mode = _last_json(r.stderr, "normalization_type")["normalization_type"]
        return {"measured": {k: m[k] for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")},
                "mode": mode, "limiter_db": limit_db, "tp_target": tp, "output": measure_output(out_path)}

    res, attempts, history = attempt(TP_TARGET), 1, []
    history.append(res)
    while res["output"]["tp"] > TP_LIMIT and attempts < MAX_ATTEMPTS:
        # the 48 kHz AAC encode overshoots the filter's peak: aim lower by the overshoot seen, back to -1.5 dBTP in the file
        tp = max(res["tp_target"] - (res["output"]["tp"] - TP_TARGET), -9.0)
        res, attempts = attempt(round(tp, 2)), attempts + 1
        history.append(res)
    return {"cmds": cmds, **res, "room": room_log, "attempts": attempts, "history": history}
