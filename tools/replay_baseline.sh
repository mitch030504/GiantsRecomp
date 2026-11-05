#!/usr/bin/env bash
# Two replays of the same input log with isolated, reset saves; compare guest state hashes.
#
# The 2026-10-31 runs shared the default user-data root and wrote it concurrently, which can change
# guest-visible path/save bytes between runs. This runs both replays against the SAME path, reset to
# identical contents before each, then reports the first checkpoint where a previously matching
# 1 MiB bucket starts differing. See docs/netplay.md and .ai/conversations/sessions/2026-11-05/.
#
# Usage: tools/replay_baseline.sh [--input FILE] [--seconds N] [--name NAME] [--interval N] [-- EXTRA...]
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
# Accept --name=value as well as --name value.
normalized=()
for arg in "$@"; do
  case $arg in
    --input=*|--seconds=*|--name=*|--interval=*) normalized+=("${arg%%=*}" "${arg#*=}") ;;
    *) normalized+=("$arg") ;;
  esac
done
set -- "${normalized[@]}"
input=$root/out/tmp/play.inputlog
seconds=300
name=replay-baseline
interval=300
extra=()
while [[ $# -gt 0 ]]; do
  case $1 in
    --input) input=$2; shift 2 ;;
    --seconds) seconds=$2; shift 2 ;;
    --name) name=$2; shift 2 ;;
    --interval) interval=$2; shift 2 ;;
    --) shift; extra=("$@"); break ;;
    -h|--help) sed -n '1,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
[[ -f $input ]] || { echo "missing input log: $input" >&2; exit 2; }
[[ $seconds -ge 10 && $interval -ge 1 ]] || { echo "seconds >= 10 and interval >= 1" >&2; exit 2; }

out=$root/out/tmp/$name
rm -rf "$out"
mkdir -p "$out"

run_one() {
  local suffix=$1 dir=$out/$1
  mkdir -p "$dir"
  # Same path, reset contents: the two runs see identical save and figure directories.
  rm -rf "$out/run"
  mkdir -p "$out/run/saves" "$out/run/figures"
  timeout -k 5 -s INT "$seconds" gamescope --backend headless -W 1280 -H 720 -- \
    env -u WAYLAND_DISPLAY SDL_AUDIO_DRIVER=dummy \
    LD_LIBRARY_PATH="$root/thirdparty/rexglue-sdk/out/linux-amd64" \
    "$root/out/build/linux-amd64-release/giantsrecompiled" \
    --resolution=1280x720 --resolution_scale=1 --frame_rate_limit=60 \
    --clock_frame_locked=true --clock_frame_locked_waits=true --clock_frame_locked_hz=60 \
    --sched_deterministic=true --audio_guest_paced=true --audio_mute=true \
    --present_effect=bilinear --portal_bot_figure=Spyro --portal_mode=software \
    --bot_skip_movies=true \
    --user_data_root="$out/run/saves" --portal_figures_dir="$out/run/figures" \
    --input_replay="$input" \
    --state_hash_log="$dir/hash" --state_hash_start=0 --state_hash_interval="$interval" \
    --sync_trace_log="$dir/sync" --log_file="$dir/game.log" "${extra[@]}" \
    >"$dir/stdout.log" 2>&1 || true
  if grep -q "input_replay: recording ended at read" "$dir/game.log"; then
    grep "input_replay: recording ended at read" "$dir/game.log" | tail -n 1
  fi
}

echo "replay baseline: input=$input seconds=$seconds interval=$interval output=$out"
run_one a
run_one b
echo "--- compare_state_hashes ---"
python3 "$root/tools/compare_state_hashes.py" "$out/a/hash" "$out/b/hash"
echo "logs retained: $out"
