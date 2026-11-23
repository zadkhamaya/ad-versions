#!/bin/bash
# Makes the dry-run b-roll stand-ins: three dark, low-contrast 3 s test clips (A, B, C), each at both delivery sizes
# (1344x768 wide, 768x1344 tall), 24 fps, no audio. Tiny on purpose: the repo ships them (.gitignore allows this folder).
# Usage: n8n/fixtures/make-broll.sh      (run from anywhere; writes next to this script)
set -euo pipefail
out="$(cd "$(dirname "$0")" && pwd)/broll"
mkdir -p "$out"
# per clip: testsrc2 pattern speed/hue, then pulled dark and flat (low brightness, low contrast, muted saturation)
make() { # name size hue
  ffmpeg -y -v error -f lavfi -i "testsrc2=size=$2:rate=24:duration=3" \
    -vf "eq=brightness=-0.38:contrast=0.45:saturation=0.35,hue=h=$3,format=yuv420p" \
    -c:v libx264 -preset veryfast -crf 34 -pix_fmt yuv420p -an -movflags +faststart "$out/$1_$2.mp4"
}
i=0
for clip in A B C; do
  hue=$((i * 40 + 200)); i=$((i + 1))
  make "$clip" 1344x768 "$hue"
  make "$clip" 768x1344 "$hue"
done
ls -l "$out"
