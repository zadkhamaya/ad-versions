#!/usr/bin/env bash
# The Restfield wordmark: brand/logo.png, warm off-white (#F3EEE6) on transparent, 1600 px wide, trimmed to the letters.
# Needs only ffmpeg (drawtext). Usage: brand/make-logo.sh [out.png]   (default: brand/logo.png next to this script)
# Font: Lora (upstream Lora[wght].ttf renamed, default weight 400; SIL OFL 1.1, licence in brand/fonts/): a calm, well-balanced serif with a soft
# calligraphic stroke. Change NAME, FONT or WIDTH to remake it.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
out="${1:-$here/logo.png}"
NAME="Restfield"
FONT="$here/fonts/Lora-Variable.ttf"
COLOR="F3EEE6"
WIDTH=1600
tmp="$(mktemp -d)"; trap 'rm -rf "$tmp"' EXIT
# drawtext's filter string has its own syntax (: , ' \\ [ ]): the font is used through a copy in the temp folder, and a temp folder
# whose name would need escaping is refused, so a checkout path with a colon or a space cannot break the filter
case "$tmp" in *[!A-Za-z0-9_./-]*) echo "make-logo.sh: temp folder name needs escaping, set TMPDIR to a plain path: $tmp" >&2; exit 1;; esac
[ -f "$FONT" ] || { echo "make-logo.sh: font not found: $FONT" >&2; exit 1; }
cp "$FONT" "$tmp/font.ttf"

# 1. the letters in white on black, large and with room round them
printf '%s' "$NAME" > "$tmp/name.txt"
ffmpeg -v error -y -f lavfi -i "color=c=black:s=3600x900" \
  -vf "drawtext=fontfile=$tmp/font.ttf:textfile=$tmp/name.txt:expansion=none:fontsize=480:fontcolor=white:x=150:y=150" \
  -frames:v 1 "$tmp/mask.png"

# 2. the box round the letters (numbers read from ffmpeg's metadata lines, never evaluated)
x1=""; y1=""; x2=""; y2=""
while IFS== read -r k v; do
  case "$k" in
    lavfi.bbox.x1) x1="$v";; lavfi.bbox.y1) y1="$v";; lavfi.bbox.x2) x2="$v";; lavfi.bbox.y2) y2="$v";;
  esac
done < <(ffmpeg -v error -i "$tmp/mask.png" -vf "bbox=min_val=24,metadata=print:file=-" -f null -)
for n in "$x1" "$y1" "$x2" "$y2"; do
  case "$n" in ''|*[!0-9]*) echo "make-logo.sh: no letters found in the mask" >&2; exit 1;; esac
done
w=$((x2 - x1 + 1)); h=$((y2 - y1 + 1))

# 3. trim, scale to WIDTH, and use the letters as the alpha of a flat colour
ffmpeg -v error -y -i "$tmp/mask.png" \
  -vf "crop=$w:$h:$x1:$y1,scale=$WIDTH:-2:flags=lanczos:in_range=full:out_range=full,format=gray" -frames:v 1 "$tmp/alpha.png"
ffmpeg -v error -y -i "$tmp/alpha.png" \
  -filter_complex "[0:v]split[a][b];[a]geq=r=$((16#${COLOR:0:2})):g=$((16#${COLOR:2:2})):b=$((16#${COLOR:4:2})),format=rgb24[c];[c][b]alphamerge" \
  -frames:v 1 "$out"
echo "$out"
