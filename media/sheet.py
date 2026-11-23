"""sheet.py — the contact sheet: one frame at 40 % of each version, 6 x 3, labelled with the file name.
A file that cannot be read gets a labelled "no frame" cell; it never stops the others (spec §6).
Also the reframe sheet (`reframe`): a row per beat, its middle frame with the crop windows drawn on it."""
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


MARKS = {"pass": ("0x2ecc40", "PASS"), "fail": ("0xff3b30", "FAIL")}      # outline colour and badge text
MARK_PX = 6


def _mark(status, tmp, n):
    """drawbox + badge filters for a cell with a pass/fail mark ("" for none): a coloured outline and PASS/FAIL top left."""
    if status not in MARKS:
        return ""
    colour, word = MARKS[status]
    badge = _write(os.path.join(tmp, f"mark{n:02d}.txt"), word)
    return (f",drawbox=x=0:y=0:w=iw:h=ih:color={colour}:t={MARK_PX},"
            f"drawtext=fontfile={filter_path(FONT)}:textfile={filter_path(badge)}:expansion=none:fontsize=22:fontcolor=white:"
            f"box=1:boxcolor={colour}:boxborderw=4:x={MARK_PX + 4}:y={MARK_PX + 4}")


def _frame(f, label, out_png, mark=""):
    """The 40 % frame of f, letterboxed into a cell with its label (and a pass/fail mark), written to out_png."""
    vf = (f"scale={CELL_W}:{CELL_H}:force_original_aspect_ratio=decrease,pad={CELL_W}:{CELL_H}:(ow-iw)/2:(oh-ih)/2:black,setsar=1,"
          + _text(label, "box=1:boxcolor=black@0.7:boxborderw=3:text_align=C:x=(w-text_w)/2:y=h-text_h-4") + mark)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{duration(f) * AT:.3f}", "-i", str(f), "-frames:v", "1", "-vf", vf,
                    out_png], check=True, capture_output=True)
    if not os.path.exists(out_png) or os.path.getsize(out_png) == 0:     # ffmpeg can exit 0 with no frame written
        raise RuntimeError("no frame")


def _placeholder(label, note, out_png, mark=""):
    vf = (_text(note, "fontsize=20:x=(w-text_w)/2:y=(h-text_h)/2") + ","
          + _text(label, "text_align=C:x=(w-text_w)/2:y=h-text_h-4") + mark)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x202020:s={CELL_W}x{CELL_H}", "-vf", vf,
                    "-frames:v", "1", out_png], check=True, capture_output=True)


def make(files, out_jpg, status=None):
    """status: optional, one entry per file: "pass", "fail" or None. A marked cell gets a green or red outline and a PASS/FAIL badge."""
    if not files:
        raise ValueError("no files for the contact sheet")
    if status is not None and len(status) != len(files):
        raise ValueError(f"{len(status)} marks for {len(files)} files")
    if len(files) > COLS * ROWS:
        raise ValueError(f"{len(files)} files (a sheet holds {COLS * ROWS})")
    with tempfile.TemporaryDirectory() as tmp:
        cells = []
        for n, f in enumerate(files, 1):
            label = _write(os.path.join(tmp, f"label{n:02d}.txt"), _wrap(os.path.basename(str(f))))
            png = os.path.join(tmp, f"cell{n:02d}.png")
            mark = _mark(status[n - 1] if status else None, tmp, n)
            try:
                _frame(f, label, png, mark)
            except (subprocess.CalledProcessError, ValueError, RuntimeError):
                if os.path.exists(png):
                    os.remove(png)
                _placeholder(label, _write(os.path.join(tmp, f"note{n:02d}.txt"), "no frame"), png, mark)
            cells.append(png)
        # explicit inputs: every file has a cell, so a gap cannot cut the sequence short
        inputs = [a for c in cells for a in ("-i", c)]
        graph = "".join(f"[{i}:v]" for i in range(len(cells))) + f"concat=n={len(cells)}:v=1:a=0,tile={COLS}x{ROWS}:color=black"
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-frames:v", "1", "-q:v", "3",
                        str(out_jpg)], check=True)


ROW_W = 640
BOX_COLOURS = {"9X16": "yellow", "1X1": "cyan"}      # drawbox colour names; any other format is drawn in white
BOX_PX = 3


def _label(textfile, text, x, y, colour="white", size=FONT_PX):
    return (f"drawtext=fontfile={filter_path(FONT)}:textfile={filter_path(_write(textfile, text))}:expansion=none:fontsize={size}:"
            f"fontcolor={colour}:box=1:boxcolor=black@0.7:boxborderw=3:x={x}:y={y}")


def _reframe_row(row, tmp, n, out_png):
    """One beat: its frame at row["t"] scaled to ROW_W px wide, a box per window (centre `cx`, width `wf`, both as fractions of the
    frame's width), the beat name, a label per box ("9X16 x0.17", or "9X16 set 0.50" for a position that was given), SPLIT (red) and REVIEW (orange)."""
    vf = [f"scale={ROW_W}:-2", "setsar=1"]
    for k, b in enumerate(row["boxes"]):
        x = min(max(round((b["cx"] - b["wf"] / 2) * ROW_W), 0), ROW_W - 2 * BOX_PX)
        w = min(round(b["wf"] * ROW_W), ROW_W - x)
        colour = BOX_COLOURS.get(b["fmt"], "white")
        vf.append(f"drawbox=x={x}:y=0:w={w}:h=ih:color={colour}:t={BOX_PX}")
        text = f"{b['fmt']} set {b['cx']:.2f}" if b.get("set") else f"{b['fmt']} x{b['cx']:.2f}"
        vf.append(_label(os.path.join(tmp, f"box{n}_{k}.txt"), text, x + BOX_PX + 2, 30 + 22 * k, colour))
    vf.append(_label(os.path.join(tmp, f"name{n}.txt"), row["name"], 6, 6, size=20))
    if row.get("split"):
        vf.append(_label(os.path.join(tmp, f"split{n}.txt"), "SPLIT", "w-text_w-8", 6, "red", 20))
    if row.get("review"):
        vf.append(_label(os.path.join(tmp, f"review{n}.txt"), "REVIEW", "w-text_w-8", 36 if row.get("split") else 6, "orange", 20))
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{row['t']:.3f}", "-i", str(row["src"]), "-frames:v", "1", "-vf", ",".join(vf),
                    out_png], check=True, capture_output=True)
    if not os.path.exists(out_png) or os.path.getsize(out_png) == 0:
        raise RuntimeError(f"no frame of {row['name']} at {row['t']:.2f} s")


def reframe(rows, out_jpg):
    """The reframe sheet: one row per beat (dicts name, src, t, boxes [{fmt, cx, wf, set}], split), stacked, 640 px wide."""
    if not rows:
        raise ValueError("no beats for the reframe sheet")
    with tempfile.TemporaryDirectory() as tmp:
        pngs = []
        for n, row in enumerate(rows, 1):
            png = os.path.join(tmp, f"row{n:02d}.png")
            _reframe_row(row, tmp, n, png)
            pngs.append(png)
        inputs = [a for p in pngs for a in ("-i", p)]
        graph = "".join(f"[{i}:v]" for i in range(len(pngs))) + (f"vstack=inputs={len(pngs)}" if len(pngs) > 1 else "null")
        subprocess.run(["ffmpeg", "-v", "error", "-y", *inputs, "-filter_complex", graph, "-frames:v", "1", "-q:v", "3", str(out_jpg)],
                       check=True)
