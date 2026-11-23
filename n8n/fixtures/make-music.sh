#!/bin/bash
# Makes the dry-run music bed stand-in: a quiet 35 s pad (A minor triad, low, slow tremolo), 48 kHz AAC.
# The real bed (an ACE-Step render) replaces it later at the same place: brand/music.m4a under the data root.
# Usage: n8n/fixtures/make-music.sh <output.m4a>      e.g. ~/.ad-versions/data/brand/music.m4a
set -euo pipefail
out="${1:?usage: make-music.sh <output.m4a>}"
mkdir -p "$(dirname "$out")"
ffmpeg -y -v error \
  -f lavfi -i "sine=frequency=110:sample_rate=48000:duration=35" \
  -f lavfi -i "sine=frequency=130.81:sample_rate=48000:duration=35" \
  -f lavfi -i "sine=frequency=164.81:sample_rate=48000:duration=35" \
  -filter_complex "[0][1][2]amix=inputs=3:normalize=0,tremolo=f=0.4:d=0.5,lowpass=f=900,volume=0.12,afade=t=in:d=1.5,afade=t=out:st=32:d=3[a]" \
  -map "[a]" -ac 2 -ar 48000 -c:a aac -b:a 128k "$out"
