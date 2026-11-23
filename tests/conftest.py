import subprocess
import pytest


def ff(*args):
    subprocess.run(["ffmpeg", "-v", "error", "-y", *args], check=True)


def has_filter(name):
    out = subprocess.run(["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True).stdout
    return any(line.split()[1:2] == [name] for line in out.splitlines() if len(line.split()) > 1)


@pytest.fixture(scope="session")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("media")
    ff("-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=24:duration=32", "-f", "lavfi", "-i", "sine=frequency=440:duration=32",
       "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "src.mp4"))
    for name, size in (("broll_wide.mp4", "1344x768"), ("broll_tall.mp4", "768x1344")):
        ff("-f", "lavfi", "-i", f"testsrc=size={size}:rate=24:duration=3", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / name))
    ff("-f", "lavfi", "-i", "color=c=navy:size=1080x1920:duration=1", "-frames:v", "1", str(d / "endcard.png"))
    ff("-f", "lavfi", "-i", "sine=frequency=220:duration=40", "-c:a", "aac", str(d / "music.m4a"))
    # colour-match media (Task 6 Step 8): a dark blue "source" and a bright amber "b-roll", same grain
    for name, colour in (("ref.mp4", "0x1c2a3a"), ("bright.mp4", "0xb08850")):
        ff("-f", "lavfi", "-i", f"color=c={colour}:s=320x180:d=2:r=24", "-vf", "noise=alls=20:allf=t",
           "-c:v", "libx264", "-pix_fmt", "yuv420p", str(d / name))
    ff("-f", "lavfi", "-i", "color=c=red:s=320x180:d=1:r=24", "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1:r=24",
       "-filter_complex", "[0:v][1:v]concat=n=2:v=1[v]", "-map", "[v]", "-c:v", "libx264", "-pix_fmt", "yuv420p",
       str(d / "redblue.mp4"))
    return d


@pytest.fixture
def beats():
    from media.timeline import Beat
    rows = [("S1", 0.2, 4.8, "opener-H1", "centre"), ("S2", 5.2, 9.8, "tension", "centre"),
            ("S3", 10.2, 14.8, "routine", "left"), ("S4", 15.2, 19.8, "opener-H2", "centre"),
            ("S5", 20.2, 25.8, "calm", "right"), ("S6", 26.2, 31.8, "close", "centre")]
    return {r[0]: Beat(*r) for r in rows}
