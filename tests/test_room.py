import numpy as np
import subprocess
from conftest import ff
from media.ffmpeg import duration, room_tone
from media.timeline import Segment


def _rms_per_second(path, secs):
    raw = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le", "-ac", "1", "-ar", "8000", "-"],
                         capture_output=True, check=True).stdout
    a = np.frombuffer(raw, np.int16).astype(float)
    return [float(np.sqrt(np.mean(a[i * 8000:(i + 1) * 8000] ** 2))) if len(a[i * 8000:(i + 1) * 8000]) else 0.0 for i in range(secs)]


def test_room_tone_is_the_sources_own_audio_and_silence_elsewhere(media, tmp_path):
    segs = [Segment("src", str(media / "src.mp4"), 2.0, 1.0, "centre"),
            Segment("broll", str(media / "broll_wide.mp4"), 0.0, 1.0, "centre", slot="slot1"),   # no audio stream at all
            Segment("src", str(media / "src.mp4"), 5.0, 1.0, "centre")]
    out = tmp_path / "room.wav"
    room_tone(segs, 1.0, str(out))                    # + a 1 s end card
    assert abs(duration(out) - 4.0) < 0.05
    r = _rms_per_second(out, 4)
    assert r[0] > 1000 and r[2] > 1000                # the source's tone, where the picture is the source
    assert r[1] < 5 and r[3] < 5                      # silence under the b-roll and the end card


def test_room_tone_of_a_source_without_audio_is_silence(media, tmp_path):
    silent = tmp_path / "silent.mp4"
    ff("-f", "lavfi", "-i", "testsrc=size=320x180:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(silent))
    out = tmp_path / "room.wav"
    room_tone([Segment("src", str(silent), 0.0, 2.0, "centre")], 1.0, str(out))
    assert abs(duration(out) - 3.0) < 0.05 and max(_rms_per_second(out, 3)) < 5
