"""captions.py — word timings to an ASS subtitle file: groups of up to 3 words, centred, inside the platform safe zones."""
import re

from media import qa
from media.naming import FORMATS

MAX_SCRIPT_WER = 0.3      # above this the transcript is too far from the script to borrow its timings

FONT = "DejaVu Sans"


def _ts(t: float) -> str:
    cs = int(round(max(t, 0.0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02d}:{cs // 100 % 60:02d}.{cs % 100:02d}"


def _clean(word: str) -> str:
    """Word text safe inside a Dialogue line: no override blocks ({...}), no backslash escapes (\\N, \\h), no line breaks."""
    return " ".join(re.sub(r"[{}\\]", "", word).split())


def from_script(script, heard):
    """The approved script's words with Whisper's timings: (words, wer). `heard` is whisper.words()'s [{"w", "start", "end"}].
    Each script word (as written, punctuation kept) takes the timing of the Whisper word it aligns with on normalised tokens
    (a misheard word keeps its slot, so the screen shows the script's word); words the transcript dropped are spread evenly
    over the gap between their neighbours. wer is the alignment's word error rate; above MAX_SCRIPT_WER, or with nothing
    heard, words is None and the caller falls back to the transcript."""
    shown = []
    ref, owner = [], []                      # the script's normalised tokens, and the shown word each belongs to
    lead = ""                                # a tokenless word (an em dash) before any real word waits for the next one
    for w in script.split():
        toks = qa._words(w)
        if not toks:                         # nothing to say or time: it rides on the previous word, not a caption of its own
            if shown:
                shown[-1] += " " + w
            else:
                lead = (lead + " " + w).strip()
            continue
        shown.append((lead + " " + w).strip())
        lead = ""
        for t in toks:
            ref.append(t)
            owner.append(len(shown) - 1)
    hyp, source = [], []                     # Whisper's normalised tokens, and the heard word each belongs to
    for j, w in enumerate(heard):
        for t in qa._words(w["w"]):
            hyp.append(t)
            source.append(j)
    if not ref or not hyp:
        return None, 1.0
    wer = min(1.0, qa._edit_matrix(ref, hyp)[-1][-1] / len(ref))
    if wer > MAX_SCRIPT_WER:
        return None, wer
    span = [None] * len(shown)               # (start, end) of the heard words a shown word aligned with
    for k, h in enumerate(qa.align(ref, hyp)):
        if h is None:
            continue
        w = heard[source[h]]
        a, b = span[owner[k]] or (w["start"], w["end"])
        span[owner[k]] = (min(a, w["start"]), max(b, w["end"]))
    lo, hi = min(w["start"] for w in heard), max(w["end"] for w in heard)
    out, i = [], 0
    while i < len(shown):
        if span[i] is not None:
            out.append({"w": shown[i], "start": span[i][0], "end": span[i][1]})
            i += 1
            continue
        j = i                                 # a run of words with no timing: spread over the gap before the next timed word
        while j < len(shown) and span[j] is None:
            j += 1
        t0 = out[-1]["end"] if out else lo
        t1 = span[j][0] if j < len(shown) else hi
        t1 = max(t1, t0)
        step = (t1 - t0) / (j - i)
        out += [{"w": shown[i + n], "start": t0 + n * step, "end": t0 + (n + 1) * step} for n in range(j - i)]
        i = j
    return out, wer


def to_ass(words, fmt, max_words=3) -> str:
    w, h = FORMATS[fmt]
    margin_v = round((0.28 if fmt == "9X16" else 0.12) * h)   # clear of the app's buttons (9:16) and the title-safe edge
    size = round((0.04 if fmt == "16X9" else 0.06) * w)
    head = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 0\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{FONT},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,0,2,"
        f"{round(0.06 * w)},{round(0.06 * w)},{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    words = [{**x, "w": c} for x in words if (c := _clean(x["w"]))]
    lines = []
    for i in range(0, len(words), max_words):
        g = words[i:i + max_words]
        text = " ".join(x["w"] for x in g)
        lines.append(f"Dialogue: 0,{_ts(g[0]['start'])},{_ts(g[-1]['end'])},Default,,0,0,0,,{text}")
    return head + "\n".join(lines) + ("\n" if lines else "")
