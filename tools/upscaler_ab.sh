#!/bin/bash
# Temporal-upscaler A/B on the title screen, measured against a native-4K render of the same frames.
#
# Usage (from the repo root, after a linux-amd64-release build):
#   tools/upscaler_ab.sh [seconds per run] [extra args for every FSR run...]
#
# Every run is a headless gamescope session at 3840x2160 (independent of the real display, and
# safe to run while nobody is watching) with clock_frame_locked, so the same guest frame shows the
# same camera and sky in every run. Runs: a native-4K reference (resolution_scale=2), the
# unjittered native 1080p frame (twice, as an identity check), and FSR 3.1 1080p->4K with each
# temporal_jitter_flip. Dumps go to out/tmp/upscaler_ab/<run>/ (about 1.5 GB per FSR run);
# tools/upscaler_ab_analyze.py then prints PSNR and edge-weighted error per run. See
# docs/upscaling.md.
set -euo pipefail
secs=${1:-36}
shift || true
out=$PWD/out/tmp/upscaler_ab
exe=./out/build/linux-amd64-release/giantsrecompiled

run() {
  local name=$1
  shift
  local dir=$out/$name
  rm -rf "$dir"
  mkdir -p "$dir"
  timeout -s INT "$secs" gamescope --backend headless -W 3840 -H 2160 -w 3840 -h 2160 -- \
    env -u WAYLAND_DISPLAY LD_LIBRARY_PATH=thirdparty/rexglue-sdk/out/linux-amd64 \
    "$exe" --resolution=3840x2160 --vsync=false --clock_frame_locked=true \
    --guest_output_dump_interval=30 --present_temporal_dump_interval=30 \
    --guest_output_dump_prefix="$dir/in" --present_temporal_dump_prefix="$dir/out" "$@" \
    >"$dir/stdout.log" 2>&1 || true
  cp "$(ls -t out/build/linux-amd64-release/logs/* | head -1)" "$dir/game.log"
}

run L_ref4k --resolution_scale=2
run L_native --resolution_scale=1
run L_native2 --resolution_scale=1
for flip in 0 1 2 3; do
  run "L_flip$flip" --resolution_scale=1 --present_effect=fsr3 --temporal_jitter_flip=$flip "$@"
done
python3 -I tools/upscaler_ab_analyze.py "$out"
