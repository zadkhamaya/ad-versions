"""qa.py — the technical QA of a finished version (spec §6 layer 3) and the final-audio script comparison (layer 4)."""
import json
import re
import subprocess

from media.ffmpeg import measure_output
from media.naming import FORMATS
from media.safe import scrub_paths

LENGTH_TOL_S = 0.1
LOUD_TARGET, LOUD_TOL = -14.0, 1.0
TP_MAX = -1.0
WANT_FPS = "24/1"
WANT_CODECS = ("h264", "aac")
WANT_PIX_FMT = "yuv420p"
MAX_WER = 0.15           # the final-audio check (transcript vs approved script) fails above this


def loudness(path):
    """(integrated LUFS, true peak dBTP) of a file's audio."""
    m = measure_output(path)
    return m["i"], m["tp"]


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise ValueError(r.stderr.strip() or "ffprobe failed")
    return json.loads(r.stdout)


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def check(path, expected):
    """Technical QA of one file. expected = {"length_s": 6|15|30, "fmt": "9X16"|"1X1"|"16X9"}.
    Returns {"passed", "reasons": [short strings], "measured": {...}}; every failing check adds one reason."""
    reasons, measured = [], {}
    try:
        info = _probe(path)
    except ValueError as e:
        return {"passed": False, "reasons": [f"unreadable file ({scrub_paths(e)})"[:200]], "measured": measured}
    video = [s for s in info.get("streams", []) if s.get("codec_type") == "video"]
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    if len(video) != 1:
        reasons.append(f"{len(video)} video streams (want 1)")
    if len(audio) != 1:
        reasons.append(f"{len(audio)} audio streams (want 1)")

    want_s = expected["length_s"]
    dur = _float(info.get("format", {}).get("duration"))
    measured["length_s"] = dur
    if dur is None or abs(dur - want_s) > LENGTH_TOL_S:
        reasons.append(f"length {'?' if dur is None else f'{dur:.2f}'} s (want {want_s:g} ± {LENGTH_TOL_S:g})")

    if video:
        v = video[0]
        w, h = FORMATS[expected["fmt"]]
        measured.update(size=f"{v.get('width')}x{v.get('height')}", fps=v.get("r_frame_rate"),
                        pix_fmt=v.get("pix_fmt"), vcodec=v.get("codec_name"))
        v_dur = _float(v.get("duration")) if v.get("duration") not in (None, "N/A") else dur   # the picture's own length
        measured["video_s"] = v_dur
        if v_dur is None or abs(v_dur - want_s) > LENGTH_TOL_S:
            reasons.append(f"video {'?' if v_dur is None else f'{v_dur:.2f}'} s (want {want_s:g} ± {LENGTH_TOL_S:g})")
        if (v.get("width"), v.get("height")) != (w, h):
            reasons.append(f"size {measured['size']} (want {w}x{h})")
        if v.get("r_frame_rate") != WANT_FPS:
            reasons.append(f"frame rate {v.get('r_frame_rate')} (want {WANT_FPS})")
        if v.get("pix_fmt") != WANT_PIX_FMT:
            reasons.append(f"pixel format {v.get('pix_fmt')} (want {WANT_PIX_FMT})")
    if video and audio:
        got = (video[0].get("codec_name"), audio[0].get("codec_name"))
        measured["codec"] = "/".join(str(c) for c in got)
        if got != WANT_CODECS:
            reasons.append(f"codec {measured['codec']} (want {'/'.join(WANT_CODECS)})")

    if audio:
        a_dur = _float(audio[0].get("duration"))
        measured["audio_s"] = a_dur
        if a_dur is None or abs(a_dur - want_s) > LENGTH_TOL_S:
            reasons.append(f"audio {'?' if a_dur is None else f'{a_dur:.2f}'} s (want {want_s:g} ± {LENGTH_TOL_S:g})")
        try:
            lufs, tp = loudness(path)
        except (subprocess.CalledProcessError, RuntimeError):
            reasons.append("loudness could not be measured")
        else:
            measured.update(lufs=lufs, tp=tp)
            if abs(lufs - LOUD_TARGET) > LOUD_TOL:
                reasons.append(f"loudness {lufs:.1f} LUFS (want {LOUD_TARGET:g} ± {LOUD_TOL:g})")
            if tp > TP_MAX:
                reasons.append(f"true peak {tp:.1f} dBTP (want ≤ {TP_MAX:g})")
    return {"passed": not reasons, "reasons": reasons, "measured": measured}


_ONES = ("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
         "seventeen eighteen nineteen").split()
_TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
# Contractions: every one is expanded to its full form ("it's" -> "it is", "don't" -> "do not", "we'll" -> "we will"), and the bare
# homophones a transcriber writes for them are mapped to the same full form ("its" -> "it is", "well" -> "we will", "were" ->
# "we are", "dont" -> "do not"). Both texts go through the same mapping, so an ordinary word ("sleep well", "you were") changes on
# both sides and still matches; only a real difference is left. An expanded form is never mapped again ("weren't" -> "were not",
# while a bare "were" -> "we are"), so a tense change stays visible.
_APOS_IRREGULAR = {"won't": ["will", "not"], "can't": ["can", "not"], "shan't": ["shall", "not"], "let's": ["let", "us"],
                   "ain't": ["is", "not"]}
_APOS_SUFFIX = (("'re", "are"), ("'ve", "have"), ("'ll", "will"), ("'d", "would"), ("'m", "am"))
_S_HEADS = {"it", "that", "there", "here", "what", "who", "where", "how", "he", "she"}     # X's -> X is (is/has read the same)
_BARE = {                                       # a contraction without its apostrophe, as a transcript has it
    "wont": "will not", "cant": "can not", "cannot": "can not", "shant": "shall not", "dont": "do not", "doesnt": "does not",
    "didnt": "did not", "isnt": "is not", "arent": "are not", "wasnt": "was not", "werent": "were not", "havent": "have not",
    "hasnt": "has not", "hadnt": "had not", "wouldnt": "would not", "couldnt": "could not", "shouldnt": "should not",
    "mustnt": "must not", "lets": "let us",
    "its": "it is", "well": "we will", "were": "we are", "wed": "we would", "id": "i would", "ill": "i will", "hell": "he will",
    "shell": "she will", "youre": "you are", "theyre": "they are", "im": "i am", "ive": "i have", "youll": "you will",
    "youve": "you have", "youd": "you would", "weve": "we have", "theyve": "they have", "theyll": "they will",
    "theyd": "they would", "itll": "it will", "thatll": "that will", "hes": "he is", "shes": "she is", "thats": "that is",
    "theres": "there is", "whats": "what is", "whos": "who is", "heres": "here is"}
_BARE = {k: v.split() for k, v in _BARE.items()}
_HEADS = {"i", "you", "he", "she", "it", "we", "they", "that", "there", "here", "what", "who", "where", "how"}
_HAS_HAD = {"has": "is", "had": "would"}


def _expand(w):
    """One word as written (apostrophes kept) -> its tokens: contractions expanded, bare homophones mapped."""
    if "'" not in w:
        return _BARE.get(w, [w])
    if w in _APOS_IRREGULAR:
        return _APOS_IRREGULAR[w]
    if w.endswith("n't") and len(w) > 3:
        return [w[:-3], "not"]
    for suffix, full in _APOS_SUFFIX:
        if w.endswith(suffix) and len(w) > len(suffix):
            return [w[:-len(suffix)], full]
    if w.endswith("'s") and w[:-2] in _S_HEADS:
        return [w[:-2], "is"]
    return [w.replace("'", "")]                  # a possessive or a stray apostrophe


def _number_words(n):
    if n < 20:
        return _ONES[n]
    return _TENS[n // 10] + (" " + _ONES[n % 10] if n % 10 else "")


def _words(s):
    """Lowercased words with the spoken/written differences removed: contractions expanded and their homophones mapped (see above),
    a.m./p.m. unified, % -> percent, & -> and, 3.5 -> three point five, 0-99 and 24/7 and 9am as number words, hyphens and
    punctuation gone."""
    s = s.lower().replace("\u2019", "'")
    s = re.sub(r"\b([ap])\.m\b\.?", r"\1m", s)
    s = s.replace("%", " percent ").replace("&", " and ")
    s = re.sub(r"(\d)\.(\d)", r"\1 point \2", s)
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)
    s = re.sub(r"([a-z])(\d)", r"\1 \2", s)
    s = re.sub(r"\bwere\s+not\b", "weren't", s)       # "were not" is the past tense, whatever the bare "were" is mapped to
    out = []
    for raw in re.findall(r"[\w']+", s):
        w = raw.strip("'")
        for t in _expand(w) if w else []:
            out.extend(_number_words(int(t)).split() if t.isascii() and t.isdigit() and int(t) < 100 else [t])
    # "'s" always expands to "is" and "'d" to "would", but a transcript cannot tell "he's gone" (has) from "he is gone", nor "we'd
    # left" (had) from "we would left": after a pronoun or a wh-/demonstrative head, "has" and "had" read as "is" and "would" too.
    return [_HAS_HAD[t] if i and t in _HAS_HAD and out[i - 1] in _HEADS else t for i, t in enumerate(out)]


def _edit_matrix(ref, hyp):
    """The Levenshtein table of two token lists: d[i][j] = edits to turn ref[:i] into hyp[:j]."""
    d = [list(range(len(hyp) + 1))]
    for i, r in enumerate(ref, 1):
        cur = [i]
        for j, h in enumerate(hyp, 1):
            cur.append(min(d[-1][j] + 1, cur[j - 1] + 1, d[-1][j - 1] + (r != h)))
        d.append(cur)
    return d


def _join_hotwords(tokens, hotwords):
    """A run of tokens whose letters, joined, spell a hotword becomes that one word ("rest field" -> "restfield" for the brand
    Restfield): a transcriber splits a made-up name. `hotwords` is a comma or line separated list; case and non-letters do not count."""
    keys = {re.sub(r"[^a-z]", "", h.lower()) for h in re.split(r"[,\n]", hotwords or "")} - {""}
    if not keys:
        return tokens
    letters = [re.sub(r"[^a-z]", "", t) for t in tokens]
    out, i = [], 0
    while i < len(tokens):
        for j in range(len(tokens), i, -1):                 # the longest run first
            joined = "".join(letters[i:j])
            if joined in keys and (j - i > 1 or tokens[i] != joined):
                out.append(joined)
                i = j
                break
        else:
            out.append(tokens[i])
            i += 1
    return out


def script_diff(script, transcript, hotwords=None):
    """Word error rate of `transcript` against `script` (Levenshtein over lowercased words without punctuation), 0..1. With
    `hotwords`, a run of words that spells one of them counts as that one word, in both texts, before the rate is computed."""
    ref, hyp = _words(script), _words(transcript)
    if hotwords:
        ref, hyp = _join_hotwords(ref, hotwords), _join_hotwords(hyp, hotwords)
    if not ref:
        return 0.0 if not hyp else 1.0
    return min(1.0, _edit_matrix(ref, hyp)[-1][-1] / len(ref))


def align(ref, hyp):
    """The Levenshtein alignment of two token lists: for each ref token, the index of the hyp token it was matched or
    substituted with, or None where it was dropped. (Extra hyp tokens, the insertions, belong to no ref token.)"""
    d = _edit_matrix(ref, hyp)
    out, i, j = [None] * len(ref), len(ref), len(hyp)
    while i > 0 and j > 0:
        if d[i][j] == d[i - 1][j - 1] + (ref[i - 1] != hyp[j - 1]):       # match or substitution first: it keeps neighbours together
            out[i - 1] = j - 1
            i, j = i - 1, j - 1
        elif d[i][j] == d[i - 1][j] + 1:
            i -= 1
        else:
            j -= 1
    return out
