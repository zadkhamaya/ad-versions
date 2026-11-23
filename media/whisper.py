"""whisper.py — word timings and transcripts from faster-whisper (CPU, small model, int8), loaded once per process."""
import threading

from faster_whisper import WhisperModel

_model = None
_lock = threading.Lock()      # two first requests at once must not load two models


def _get():
    global _model
    with _lock:
        if _model is None:
            _model = WhisperModel("small", device="cpu", compute_type="int8")
        return _model


def words(wav) -> list[dict]:
    """[{"w", "start", "end"}] for every spoken word in the file, in order."""
    segments, _info = _get().transcribe(str(wav), language="en", word_timestamps=True)
    return [{"w": w.word.strip(), "start": float(w.start), "end": float(w.end)}
            for seg in segments for w in (seg.words or []) if w.word.strip()]


def text(path, hotwords=None) -> str:
    """The transcript of an audio or video file as one line. `hotwords`: words the model should expect (names), faster-whisper's own
    hint; never the script the transcript is compared with."""
    kw = {"hotwords": hotwords} if hotwords else {}
    segments, _info = _get().transcribe(str(path), language="en", **kw)
    return " ".join(seg.text.strip() for seg in segments if seg.text.strip())


def preload():
    """Load the model now (PRELOAD_WHISPER=1 at startup), so the first render does not pay the download/load inside its slot."""
    _get()
