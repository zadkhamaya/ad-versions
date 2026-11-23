from types import SimpleNamespace as NS

from media import whisper


class FakeModel:
    calls = 0

    def transcribe(self, path, **kw):
        segs = [NS(text=" Sleep better", words=[NS(word=" Sleep", start=0.0, end=0.4), NS(word=" better", start=0.4, end=0.9)]),
                NS(text=" tonight.", words=[NS(word=" tonight.", start=1.0, end=1.6)])]
        return iter(segs), NS()


def test_words_and_text(monkeypatch):
    monkeypatch.setattr(whisper, "_model", FakeModel())
    assert whisper.words("x.wav") == [{"w": "Sleep", "start": 0.0, "end": 0.4}, {"w": "better", "start": 0.4, "end": 0.9},
                                      {"w": "tonight.", "start": 1.0, "end": 1.6}]
    assert whisper.text("x.mp4") == "Sleep better tonight."


def test_model_loads_once(monkeypatch):
    loads = []
    monkeypatch.setattr(whisper, "_model", None)
    monkeypatch.setattr(whisper, "WhisperModel", lambda *a, **k: loads.append((a, k)) or FakeModel())
    whisper.words("a.wav"); whisper.words("b.wav")
    assert loads == [(("small",), {"device": "cpu", "compute_type": "int8"})]


def test_concurrent_first_use_loads_one_model(monkeypatch):
    import threading
    import time
    loads = []

    def slow_loader(*a, **k):
        loads.append(1)
        time.sleep(0.3)
        return FakeModel()

    monkeypatch.setattr(whisper, "_model", None)
    monkeypatch.setattr(whisper, "WhisperModel", slow_loader)
    ts = [threading.Thread(target=whisper.words, args=("a.wav",)) for _ in range(3)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(loads) == 1


def test_dockerfile_keeps_the_model_cache_on_the_data_volume():
    from pathlib import Path
    assert "ENV HF_HOME=/data/hf" in (Path(__file__).resolve().parents[1] / "docker" / "media.Dockerfile").read_text()


def test_dockerfile_installs_under_the_constraints_file():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    text = (root / "docker" / "media.Dockerfile").read_text()
    assert "COPY pyproject.toml constraints.txt" in text and "-c constraints.txt" in text
    pins = (root / "constraints.txt").read_text().split()
    assert all("==" in p for p in pins) and not any(p.lower().startswith(("pytest", "httpx==")) for p in pins)


def test_preload_loads_the_model_once(monkeypatch):
    loads = []
    monkeypatch.setattr(whisper, "_model", None)
    monkeypatch.setattr(whisper, "WhisperModel", lambda *a, **k: loads.append(1) or FakeModel())
    whisper.preload()
    whisper.words("a.wav")
    assert loads == [1]
