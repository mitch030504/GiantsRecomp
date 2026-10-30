#!/usr/bin/env bash
# Plays the game with a scripted "bot" in a headless gamescope (nothing on screen, safe to run in
# the background) and records every controller read, for the netplay determinism check and the
# upscaler A/B in a real scene. See docs/netplay.md.
#
#   tools/bot_record.sh [script] [output] [seconds] [extra game args...]
#
# Defaults: tools/bot/chapter1.txt (title -> new game -> Chapter 1 -> ~2.5 min of play),
# out/tmp/play.inputlog, 480 s. Frames are dumped every 120 guest frames to out/tmp/bot/frames/ so
# the run can be checked afterwards. Replay with:
#   giantsrecompiled --clock_frame_locked=true --portal_bot_figure=Spyro --input_replay=<output>
set -euo pipefail
cd "$(dirname "$0")/.."
script=${1:-tools/bot/chapter1.txt}
output=${2:-out/tmp/play.inputlog}
seconds=${3:-480}
shift $(( $# < 3 ? $# : 3 ))
frames=out/tmp/bot/frames
rm -rf "$frames"
mkdir -p "$frames" "$(dirname "$output")"
timeout -k 5 -s INT "$seconds" gamescope --backend headless -W 1280 -H 720 -- \
  env -u WAYLAND_DISPLAY LD_LIBRARY_PATH=thirdparty/rexglue-sdk/out/linux-amd64 \
  ./out/build/linux-amd64-release/giantsrecompiled --resolution=1280x720 --resolution_scale=1 \
  --clock_frame_locked=true --portal_bot_figure=Spyro --input_script="$script" \
  --input_record="$output" --guest_output_dump_prefix="$frames/f" \
  --guest_output_dump_interval=120 "$@" >"$frames/stdout.log" 2>&1 || true
cp "$(ls -t out/build/linux-amd64-release/logs/* | head -1)" "$frames/game.log"
echo "recorded $output ($(stat -c %s "$output") bytes); $(ls "$frames" | grep -c '\.ppm$') frames in $frames"
grep -E 'input_script: script ended' "$frames/game.log" || echo "warning: the script didn't finish"
