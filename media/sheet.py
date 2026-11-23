"""sheet.py — the contact sheet: one frame at 40 % of each version, 6 x 3, labelled with the file name.
A file that cannot be read gets a labelled "no frame" cell; it never stops the others (spec §6)."""
import os
import subprocess
import tempfile

from media.ffmpeg import duration, filter_path

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
COLS, ROWS = 6, 3
CELL_W, CELL_H = 320, 320      # every cell the same size; 9:16, 1:1 and 16:9 frames are letterboxed into it
AT = 0.4
FONT_PX = 16                   # Telegram downscales the 1920 px sheet: smaller labels are unreadable there
LINE_CHARS = 30                # DejaVu Sans at 16 px is ~9.6 px a character: 30 fit a 320 px cell


def _wrap(name):
    """The file name on lines of at most LINE_CHARS, broken after underscores where it can."""
    lines, cur = [], ""
    for part in name.replace("_", "_\n").split("\n"):
        while len(part) > LINE_CHARS:
            lines.append(part[:LINE_CHARS])
            part = part[LINE_CHARS:]
        if cur and len(cur) + len(part) > LINE_CHARS:
            lines.append(cur)
            cur = ""
        cur += part
    return "\n".join(lines + ([cur] if cur else []))


def _text(textfile, extra):
    return (f"drawtext=fontfile={filter_path(FONT)}:textfile={filter_path(textfile)}:expansion=none:fontsize={FONT_PX}:"
            f"fontcolor=white:{extra}")


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:      # a text file, so the name needs no drawtext escaping
        fh.write(text)
    return path


def _frame(f, label, out_png):
    """The 40 % frame of f, letterboxed into a cell with its label, written to out_png."""
    vf = (f"scale={CELL_W}:{CELL_H}:force_original_aspect_ratio=decrease,pad={CELL_W}:{CELL_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
          + _text(label, "box=1:boxcolor=black@0.7:boxborderw=3:text_align=C:x=(w-text_w)/2:y=h-text_h-4"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{duration(f) * AT:.3f}", "-i", str(f), "-frames:v", "1", "-vf", vf,
                    out_png], check=True, capture_output=True)
    if not os.path.exists(out_png) or os.path.getsize(out_png) == 0:     # ffmpeg can exit 0 with no frame written
        raise RuntimeError("no frame")


def _placeholder(label, note, out_png):
    vf = (_text(note, "fontsize=20:x=(w-text_w)/2:y=(h-text_h)/2") + ","
          + _text(label, "text_align=C:x=(w-text_w)/2:y=h-text_h-4"))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x202020:s={CELL_W}x{CELL_H}", "-vf", vf,
                    "-frames:v", "1", out_png], check=True, capture_output=True)


def make(files, out_jpg):
    if not files:
        raise ValueError("no files for the contact sheet")
    if len(files) > COLS * ROWS:
        raise ValueError(f"{len(files)} files (a sheet holds {COLS * ROWS})")
    with tempfile.TemporaryDirectory() as tmp:
        cells = []
        for n, f in enumerate(files, 1):
            label = _write(os.path.join(tmp, f"label{n:02d}.txt"), _wrap(os.path.basename(str(f))))
            png = os.path.join(tmp, f"cell{n:02d}.png")
            try:
                _frame(f, label, png)
            except (subprocess.CalledProcessError, ValueError, RuntimeError):
                if os.path.exists(png):
                    os.remove(png)
                _placeholder(label, _write(os.path.join(tmp, f"note{n:02d}.txt"), "no frame"), png)
            cells.append(png)
        # explicit inputs: every file has a cell, so a gap cannot cut the sequence short
        inputs = [a for c in cells for a in ("-i", c)]
        graph = "".join(f"[{i}:v]" for i in range(len(cells))) + f"concat=n={len(cells)}:v=1:a=0,tile={COLS}x{ROWS}:color=black"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-frames:v", "1", "-q:v", "3",
                        str(out_jpg)], check=True)
