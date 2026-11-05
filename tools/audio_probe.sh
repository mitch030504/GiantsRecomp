#!/usr/bin/env bash
# Frame-locked audio producer capture harness.
#
# Records what the game actually sends to an audio device while the guest clock is free-running
# (wall mode) or frame-locked at 60/60 with host-paced or guest-paced audio. The analysis in
# tools/audio_probe_analyze.py then looks for exact repeats, zero runs and clicks in the raw
# capture. See tools/audio_probe.md for the method, the buffer sweep and the ambiguities.
#
# Usage (from anywhere; paths are resolved against the repo root):
#   tools/audio_probe.sh [--seconds=N] [--frames=LIST] [--modes=LIST]
#                        [--input-script=FILE] [--maxqframes=N] [--out=DIR] [--analyze]
#
# Defaults: 60 s per run, SAMPLE_FRAMES=1024, all four modes, output under
# out/tmp/audio-probe/. The decisive test is a device-buffer sweep:
#   tools/audio_probe.sh --seconds=60 --frames=256,1024,2048
#   python3 -I tools/audio_probe_analyze.py out/tmp/audio-probe/<run>
#
# Safety: this creates one uniquely named PulseAudio null sink for the whole invocation and
# records only its monitor. It never changes the default sink, mute or volume, and it unloads
# only the module it loaded. A stream is moved (never the default sink) only when it is provably
# ours: its application name is the unique SDL_APP_NAME set below, or its process is a
# descendant of the gamescope process this script started. Every run gets private saves and a
# private figures directory.
#
# Environment:
#   SECONDS_PER_RUN   default 60
#   SAMPLE_FRAMES     default 1024; comma/space list, e.g. "256 1024 2048"
#   MODES             default wall,host,guest,net
#   INPUT_SCRIPT      optional bot script passed as --input_script
#   MAXQFRAMES        default 8 (audio_maxqframes)
#   PROBE_SO          optional LD_PRELOAD probe; preloaded only if the file exists
#   GAME              override the game binary
#   AUDIO_PROBE_OUT   override the output root
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo"

run_seconds=${SECONDS_PER_RUN:-60}
frames_csv=${SAMPLE_FRAMES:-1024}
modes_csv=${MODES:-wall,host,guest,net}
maxqframes=${MAXQFRAMES:-8}
input_script=${INPUT_SCRIPT:-}
probe_so=${PROBE_SO:-}
game=${GAME:-$repo/out/build/linux-amd64-release/giantsrecompiled}
out_root=${AUDIO_PROBE_OUT:-$repo/out/tmp/audio-probe}
do_analyze=0

usage() {
  cat <<'EOF'
Frame-locked audio producer capture harness. Records the game's mixed output from a private
PulseAudio null sink and stores a raw float32 stereo capture plus a metadata JSON per run.

Usage:
  tools/audio_probe.sh [options]

Options:
  --seconds=N          capture time per run (default 60, minimum 5)
  --frames=LIST        SDL_AUDIO_DEVICE_SAMPLE_FRAMES values, comma or space separated
                       (default 1024, the SDL 48 kHz default). The decisive test is
                       256,1024,2048.
  --modes=LIST         wall,host,guest,net (default all four)
  --input-script=FILE  pass --input_script to the game (optional)
  --maxqframes=N       audio_maxqframes, 4..64 (default 8)
  --out=DIR            output root (default out/tmp/audio-probe)
  --analyze            run tools/audio_probe_analyze.py on the output when done
  -h, --help           this text

Environment: SECONDS_PER_RUN, SAMPLE_FRAMES, MODES, INPUT_SCRIPT, MAXQFRAMES, PROBE_SO,
GAME, AUDIO_PROBE_OUT. The harness never builds and never changes the default sink, its mute,
or its volume. See tools/audio_probe.md.
EOF
}

die() {
  echo "audio_probe: $*" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --seconds=*) run_seconds=${1#*=} ;;
    --seconds) (($# >= 2)) || die "--seconds needs a value"; run_seconds=$2; shift ;;
    --frames=*) frames_csv=${1#*=} ;;
    --frames) (($# >= 2)) || die "--frames needs a value"; frames_csv=$2; shift ;;
    --modes=*) modes_csv=${1#*=} ;;
    --modes) (($# >= 2)) || die "--modes needs a value"; modes_csv=$2; shift ;;
    --input-script=*) input_script=${1#*=} ;;
    --input-script) (($# >= 2)) || die "--input-script needs a value"; input_script=$2; shift ;;
    --maxqframes=*) maxqframes=${1#*=} ;;
    --maxqframes) (($# >= 2)) || die "--maxqframes needs a value"; maxqframes=$2; shift ;;
    --out=*) out_root=${1#*=} ;;
    --out) (($# >= 2)) || die "--out needs a value"; out_root=$2; shift ;;
    --analyze) do_analyze=1 ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (try --help)" ;;
  esac
  shift
done

[[ $run_seconds =~ ^[0-9]+$ ]] || die "--seconds must be a whole number"
((run_seconds >= 5)) || die "--seconds must be at least 5"
[[ $maxqframes =~ ^[0-9]+$ ]] || die "--maxqframes must be an integer"
((maxqframes >= 4 && maxqframes <= 64)) || die "--maxqframes must be in 4..64"

read -ra modes <<< "${modes_csv//,/ }"
read -ra frame_list <<< "${frames_csv//,/ }"
((${#modes[@]})) || die "no modes given"
((${#frame_list[@]})) || die "no sample-frame sizes given"
for mode in "${modes[@]}"; do
  [[ $mode =~ ^(wall|host|guest|net)$ ]] || die "unknown mode '$mode' (wall, host, guest, net)"
done
for sf in "${frame_list[@]}"; do
  [[ $sf =~ ^[0-9]+$ ]] || die "sample frames must be integers: '$sf'"
  ((sf >= 64 && sf <= 8192)) || die "sample frames '$sf' outside 64..8192"
done

for tool in pactl parec gamescope python3 date ps pkill setsid; do
  command -v "$tool" >/dev/null 2>&1 || die "missing required tool: $tool"
done
[[ -x $game ]] || die "game binary not found or not executable: $game (this harness never builds)"
game=$(realpath "$game")

if [[ -n $probe_so ]]; then
  if [[ -f $probe_so ]]; then
    probe_so=$(realpath "$probe_so")
  else
    echo "audio_probe: PROBE_SO=$probe_so does not exist; continuing without the probe" >&2
    probe_so=""
  fi
fi
if [[ -n $input_script ]]; then
  [[ -f $input_script ]] || die "--input-script not found: $input_script"
  input_script=$(realpath "$input_script")
fi

mkdir -p "$out_root"
out_root=$(realpath "$out_root")
run_root=$(mktemp -d "$out_root/run-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")
stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ)
run_id=${run_root##*/}
sink_name="giants_audio_probe_$$_${RANDOM}"
app_name="GiantsAudioProbe_${run_id//[^A-Za-z0-9_]/_}"
module_id=""
game_pgid=""
rec_pid=""
cleanup_done=0
had_failure=0

default_sink_name() {
  local value=""
  value=$(pactl get-default-sink 2>/dev/null || true)
  if [[ -z $value ]]; then
    value=$(pactl info 2>/dev/null | sed -n 's/^Default Sink: //p' | head -n1 || true)
  fi
  printf '%s' "$value"
}

sink_mute() { pactl get-sink-mute "$1" 2>/dev/null | tr '\n' ' ' || true; }
sink_volume() { pactl get-sink-volume "$1" 2>/dev/null | tr '\n' ' ' || true; }

default_sink_before=$(default_sink_name)
[[ -n $default_sink_before ]] || die "cannot determine the default sink; is the sound server reachable?"
default_mute_before=$(sink_mute "$default_sink_before")
default_volume_before=$(sink_volume "$default_sink_before")

echo "audio_probe: run directory $run_root"
echo "audio_probe: default sink '$default_sink_before' will not be touched"

stop_recorder() {
  local pid=${rec_pid:-}
  [[ -z $pid ]] && return 0
  rec_pid=""
  kill -INT "$pid" 2>/dev/null || true
  for _ in {1..20}; do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.05
  done
  kill -TERM "$pid" 2>/dev/null || true
  wait "$pid" 2>/dev/null || true
}

signal_game() {
  local sig=$1 pid=${game_pgid:-}
  [[ -z $pid ]] && return 0
  local pgid
  pgid=$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)
  if [[ $pgid == "$pid" ]]; then
    kill -"$sig" -- "-$pid" 2>/dev/null || true
  else
    kill -"$sig" "$pid" 2>/dev/null || true
    pkill -"$sig" -P "$pid" 2>/dev/null || true
  fi
}

stop_game() {
  local pid=${game_pgid:-}
  [[ -z $pid ]] && return 0
  if kill -0 "$pid" 2>/dev/null; then
    signal_game INT
    for _ in {1..100}; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null; then
      signal_game TERM
      sleep 1
    fi
    if kill -0 "$pid" 2>/dev/null; then
      signal_game KILL
    fi
  fi
  wait "$pid" 2>/dev/null || true
  game_pgid=""
}

verify_default_sink() {
  local status=0 now mute volume
  now=$(default_sink_name)
  if [[ $now != "$default_sink_before" ]]; then
    echo "audio_probe: ERROR default sink changed: '$default_sink_before' -> '$now'" >&2
    status=1
  fi
  mute=$(sink_mute "$default_sink_before")
  if [[ $mute != "$default_mute_before" ]]; then
    echo "audio_probe: ERROR default-sink mute changed: '$default_mute_before' -> '$mute'" >&2
    status=1
  fi
  volume=$(sink_volume "$default_sink_before")
  if [[ $volume != "$default_volume_before" ]]; then
    echo "audio_probe: ERROR default-sink volume changed: '$default_volume_before' -> '$volume'" >&2
    status=1
  fi
  if ((status == 0)); then
    echo "audio_probe: default sink '$default_sink_before' unchanged (name, mute, volume)"
  fi
  return "$status"
}

cleanup() {
  local status=${1:-$?}
  ((cleanup_done == 0)) || exit "$status"
  cleanup_done=1
  trap - EXIT INT TERM HUP
  stop_game
  stop_recorder
  if [[ -n $module_id ]]; then
    if pactl unload-module "$module_id" 2>/dev/null; then
      echo "audio_probe: unloaded private module $module_id"
    else
      echo "audio_probe: warning: private module $module_id was already gone" >&2
      status=1
    fi
  fi
  verify_default_sink || status=1
  exit "$status"
}
trap 'cleanup' EXIT
trap 'cleanup 130' INT
trap 'cleanup 143' TERM
trap 'cleanup 129' HUP

# Load the private sink only after the trap exists, so every failure below still unloads it.
module_id=$(pactl load-module module-null-sink \
  sink_name="$sink_name" rate=48000 channels=2 \
  "sink_properties=device.description=GiantsAudioProbe" 2>/dev/null) \
  || { module_id=""; die "failed to load the private null sink"; }
[[ -n $module_id ]] || die "the sound server returned an empty module id"
sink_index=$(pactl list short sinks 2>/dev/null | awk -v want="$sink_name" '$2 == want { print $1; exit }')
[[ -n $sink_index ]] || die "the private sink '$sink_name' did not appear"
sink_spec=$(pactl list short sinks 2>/dev/null | awk -v want="$sink_name" '$2 == want { $1=""; $2=""; sub(/^  */, ""); print; exit }')
echo "audio_probe: private sink '$sink_name' (module $module_id, index $sink_index)"

descendant_pids() {
  python3 - "$1" <<'PY' 2>/dev/null || true
import os
import sys

root = int(sys.argv[1])
children = {}
for entry in os.listdir('/proc'):
    if not entry.isdigit():
        continue
    try:
        with open(f'/proc/{entry}/stat', encoding='utf-8', errors='replace') as handle:
            stat = handle.read()
    except OSError:
        continue
    # The comm field can contain spaces and parentheses; fields continue after the last ')'.
    rest = stat.rsplit(')', 1)[1].split()
    if len(rest) < 2:
        continue
    try:
        children.setdefault(int(rest[1]), []).append(int(entry))
    except ValueError:
        continue
out = []
stack = [root]
while stack:
    parent = stack.pop()
    for child in children.get(parent, []):
        out.append(child)
        stack.append(child)
print(' '.join(str(pid) for pid in out))
PY
}

# Move/unmute only a stream that is demonstrably ours: the unique SDL_APP_NAME, or a process
# descended from this run's gamescope. The default sink stays untouched even if routing is odd.
route_owned_stream() {
  local dir=$1
  local pids idx current mute app
  pids=$(descendant_pids "$game_pgid")
  while IFS=$'\t' read -r idx current mute app; do
    [[ -z $idx ]] && continue
    routed=1
    if [[ $current != "$sink_index" ]]; then
      if pactl move-sink-input "$idx" "$sink_name" 2>>"$dir/routing.log"; then
        echo "$(date -u +%H:%M:%S) moved owned sink-input $idx ($app) from sink $current to $sink_name" \
          >>"$dir/routing.log"
      fi
    fi
    if [[ $mute == "true" ]]; then
      if pactl set-sink-input-mute "$idx" 0 2>>"$dir/routing.log"; then
        echo "$(date -u +%H:%M:%S) unmuted owned sink-input $idx ($app)" >>"$dir/routing.log"
      fi
    fi
  done < <(pactl -f json list sink-inputs 2>/dev/null | \
    python3 - "$app_name" "$sink_index" $pids <<'PY' 2>/dev/null || true
import json
import sys

name = sys.argv[1]
sink = str(sys.argv[2])
ours = set(sys.argv[3:])
try:
    streams = json.load(sys.stdin)
except (ValueError, OSError):
    raise SystemExit(0)
for stream in streams:
    props = stream.get('properties') or {}
    pid = str(props.get('application.process.id', ''))
    app = props.get('application.name', '?')
    if app == name or (pid and pid in ours):
        print(f"{stream.get('index')}\t{stream.get('sink')}\t"
              f"{str(stream.get('mute', False)).lower()}\t{app}")
PY
  )
}

write_metadata() {
  local dir=$1 mode=$2 sf=$3 fps_limit=$4 lock_hz=$5 guest_paced=$6 sched=$7
  local csv=$8 started=$9 ended=${10} early=${11} deadline=${12} game_exit=${13} routed=${14}
  MODE="$mode" SAMPLE_FRAMES="$sf" FPS_LIMIT="$fps_limit" LOCK_HZ="$lock_hz" \
    GUEST_PACED="$guest_paced" SCHED_DETERMINISTIC="$sched" MAXQFRAMES="$maxqframes" \
    CAPTURE="$dir/capture.f32" GAME_LOG="$dir/game.log" PROBE_CSV="$csv" \
    COMMAND_JSON="$dir/command.json" META="$dir/metadata.json" \
    REQUESTED_SECONDS="$run_seconds" EARLY_EXIT="$early" DEADLINE_HIT="$deadline" \
    GAME_EXIT="$game_exit" SINK_NAME="$sink_name" SINK_INDEX="$sink_index" \
    APP_NAME="$app_name" RATE=48000 CHANNELS=2 ROUTED="$routed" \
    STARTED_UTC="$started" ENDED_UTC="$ended" \
    python3 - <<'PY'
import csv
import json
import os
from pathlib import Path

def env(name):
    return os.environ.get(name, "")

def as_bool(name):
    return env(name) in ("1", "true", "yes")

def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None

meta = {
    "mode": env("MODE"),
    "fps": int(env("FPS_LIMIT")),
    "fps_measured": None,
    "lock_hz": int(env("LOCK_HZ")) if env("LOCK_HZ") else None,
    "guest_paced": as_bool("GUEST_PACED"),
    "sched_deterministic": as_bool("SCHED_DETERMINISTIC"),
    "maxqframes": int(env("MAXQFRAMES")),
    "sample_frames": int(env("SAMPLE_FRAMES")),
    "requested_seconds": int(env("REQUESTED_SECONDS")),
    "started_utc": env("STARTED_UTC"),
    "ended_utc": env("ENDED_UTC"),
    "early_exit": as_bool("EARLY_EXIT"),
    "deadline_hit": as_bool("DEADLINE_HIT"),
    "game_exit": int(env("GAME_EXIT")),
    "routed_owned_stream": as_bool("ROUTED"),
    "sink": env("SINK_NAME"),
    "sink_index": int(env("SINK_INDEX")) if env("SINK_INDEX") else None,
    "app_name": env("APP_NAME"),
    "capture": env("CAPTURE"),
    "capture_bytes": None,
    "capture_seconds": None,
    "game_log": env("GAME_LOG"),
    "probe_csv": env("PROBE_CSV") or None,
    "swaps": None,
    "submits": None,
    "command": load_json(env("COMMAND_JSON")),
}
capture = Path(env("CAPTURE"))
if capture.is_file():
    size = capture.stat().st_size
    frame_bytes = int(env("RATE")) * int(env("CHANNELS")) * 4
    meta["capture_bytes"] = size
    meta["capture_seconds"] = round(size / frame_bytes, 3) if frame_bytes else None

probe_path = Path(env("PROBE_CSV")) if env("PROBE_CSV") else None
if probe_path and probe_path.is_file():
    try:
        rows = list(csv.DictReader(probe_path.open(newline="")))
    except OSError:
        rows = []

    def number(row, name):
        try:
            return float(row.get(name, "0"))
        except (TypeError, ValueError):
            return 0.0

    rows = [row for row in rows if row.get("seconds") not in (None, "")]
    if rows:
        first, last = rows[0], rows[-1]
        elapsed = number(last, "seconds") - number(first, "seconds")

        def delta(name):
            return number(last, name) - number(first, name)

        def rate(name):
            return round(delta(name) / elapsed, 3) if elapsed > 0 else None

        swaps = int(number(last, "swaps"))
        submits = int(number(last, "submits"))
        meta["swaps"] = swaps
        meta["submits"] = submits
        meta["fps_measured"] = rate("swaps")
        meta["probe"] = {
            "rows": len(rows),
            "probe_seconds": round(elapsed, 3),
            "swaps": swaps,
            "submits": submits,
            "callbacks": int(number(last, "callbacks")),
            "chunks": int(number(last, "chunks")),
            "zero_chunks": int(number(last, "zero_chunks")),
            "bytes": int(number(last, "bytes")),
            "swaps_per_second": rate("swaps"),
            "submits_per_second": rate("submits"),
            "callbacks_per_second": rate("callbacks"),
            "chunks_per_second": rate("chunks"),
            "submits_per_swap": (
                round(delta("submits") / delta("swaps"), 3) if delta("swaps") > 0 else None
            ),
            "zero_chunk_fraction": (
                round(delta("zero_chunks") / delta("chunks"), 5) if delta("chunks") > 0 else None
            ),
        }

Path(env("META")).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
PY
}

run_one() {
  local mode=$1 sf=$2
  local dir="$run_root/$mode-f$sf"
  local fps_limit lock_hz guest_paced sched
  local csv="" started ended game_exit=0 early=0 deadline=0 routed=0

  case "$mode" in
    wall)
      # Unlocked wall-clock reference: no guest-time locking and host-paced audio.
      fps_limit=-1; lock_hz=""; guest_paced=false; sched=false
      ;;
    host)
      # Positive control: 60/60 guest clock, but the device still releases buffers.
      fps_limit=60; lock_hz=60; guest_paced=false; sched=false
      ;;
    guest)
      # The producer under test: 60/60 guest clock, audio released per guest frame.
      fps_limit=60; lock_hz=60; guest_paced=true; sched=false
      ;;
    net)
      # Netplay's audio-relevant configuration, without peers (no netplay_host/connect).
      fps_limit=60; lock_hz=60; guest_paced=true; sched=true
      ;;
  esac

  rm -rf "$dir"
  mkdir -p "$dir/saves" "$dir/figures"
  : >"$dir/routing.log"
  started=$(date -u +%Y-%m-%dT%H:%M:%SZ)

  parec --device="$sink_name.monitor" --format=float32le --rate=48000 --channels=2 \
    --raw --latency-msec=20 --process-time-msec=20 \
    --client-name="audio-probe-recorder-$run_id-$mode-$sf" \
    >"$dir/capture.f32" 2>"$dir/recorder.log" &
  rec_pid=$!
  sleep 0.4
  if ! kill -0 "$rec_pid" 2>/dev/null; then
    echo "audio_probe: $mode-f$sf: parec failed to start" >&2
    cat "$dir/recorder.log" >&2 || true
    rec_pid=""
    had_failure=1
    return 1
  fi

  local args=(
    --resolution=1280x720 --resolution_scale=1 --vsync=false
    --frame_rate_limit="$fps_limit"
    --audio_mute=false --audio_maxqframes="$maxqframes"
    --clock_frame_locked_waits=true
    --bot_skip_movies=true --portal_bot_figure=Spyro --portal_mode=software
    --user_data_root="$dir/saves" --portal_figures_dir="$dir/figures"
    --log_file="$dir/game.log"
  )
  if [[ $mode == wall ]]; then
    args+=(--clock_frame_locked=false --audio_guest_paced=false)
  else
    args+=(--clock_frame_locked=true --clock_frame_locked_hz="$lock_hz"
           --audio_guest_paced="$guest_paced")
  fi
  if [[ $mode == net ]]; then
    # The rest of what netplay locks when it configures a session.
    args+=(--sched_deterministic=true
           --video_mode_width=1280 --video_mode_height=720 --video_mode_refresh_rate=60
           --present_effect=bilinear)
  fi
  [[ -n $input_script ]] && args+=(--input_script="$input_script")

  local env_args=(
    -u WAYLAND_DISPLAY
    "LD_LIBRARY_PATH=$repo/thirdparty/rexglue-sdk/out/linux-amd64"
    SDL_AUDIO_DRIVER=pulseaudio
    "PULSE_SINK=$sink_name"
    "SDL_AUDIO_DEVICE_SAMPLE_FRAMES=$sf"
    "SDL_APP_NAME=$app_name"
  )
  if [[ -n $probe_so ]]; then
    csv="$dir/probe.csv"
    env_args+=("LD_PRELOAD=$probe_so" "GIANTS_AUDIO_PROBE=$csv")
  fi

  local command=(gamescope --backend headless -W 1280 -H 720 -w 1280 -h 720 --
                 env "${env_args[@]}" "$game" "${args[@]}")
  python3 - "$dir/command.json" "${command[@]}" <<'PY'
import json
import sys
from pathlib import Path

Path(sys.argv[1]).write_text(json.dumps(sys.argv[2:], indent=2) + "\n")
PY

  echo "audio_probe: $mode-f$sf: starting ($run_seconds s)"
  (cd "$repo" && exec setsid "${command[@]}") >"$dir/stdout.log" 2>&1 &
  game_pgid=$!

  local deadline_at=$(( $(date +%s) + run_seconds ))
  # SDL asks for the default playback device by path, so PULSE_SINK can be bypassed; the first
  # poll happens immediately and then often enough that only a short routing gap is possible.
  route_owned_stream "$dir"
  while kill -0 "$game_pgid" 2>/dev/null; do
    if (( $(date +%s) >= deadline_at )); then
      break
    fi
    route_owned_stream "$dir"
    sleep 0.2
  done

  if kill -0 "$game_pgid" 2>/dev/null; then
    deadline=1
    stop_game
  else
    wait "$game_pgid" 2>/dev/null || game_exit=$?
    game_pgid=""
    early=1
    echo "audio_probe: $mode-f$sf: game exited before the deadline (exit $game_exit)" >&2
    had_failure=1
  fi

  stop_recorder
  ended=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  pactl -f json list sink-inputs >"$dir/sink-inputs-after.json" 2>/dev/null || true

  write_metadata "$dir" "$mode" "$sf" "$fps_limit" "$lock_hz" "$guest_paced" "$sched" \
    "$csv" "$started" "$ended" "$early" "$deadline" "$game_exit" "$routed"

  local capture_seconds=0
  if [[ -s $dir/capture.f32 ]]; then
    capture_seconds=$(python3 -c 'import os,sys; print(round(os.path.getsize(sys.argv[1])/(48000*2*4),3))' \
      "$dir/capture.f32")
  fi
  echo "audio_probe: $mode-f$sf: capture ${capture_seconds}s, log $dir/game.log"
  python3 -c 'import sys; sys.exit(0 if float(sys.argv[1]) >= max(5.0, float(sys.argv[2]) * 0.5) else 1)' \
    "$capture_seconds" "$run_seconds" || {
      echo "audio_probe: $mode-f$sf: capture is too short to judge" >&2
      had_failure=1
    }
}

for mode in "${modes[@]}"; do
  for sf in "${frame_list[@]}"; do
    if ! run_one "$mode" "$sf"; then
      had_failure=1
    fi
  done
done

RUN_ROOT="$run_root" SINK_NAME="$sink_name" MODULE_ID="$module_id" \
  DEFAULT_SINK="$default_sink_before" SINK_SPEC="$sink_spec" STAMP="$stamp" \
  MODES="$modes_csv" FRAMES="$frames_csv" MAXQFRAMES="$maxqframes" GAME="$game" \
  PROBE_SO="$probe_so" INPUT_SCRIPT="$input_script" \
  python3 - <<'PY'
import json
import os
from pathlib import Path

meta = {
    "started_utc": os.environ["STAMP"],
    "sink": os.environ["SINK_NAME"],
    "sink_spec": os.environ["SINK_SPEC"],
    "module_id": os.environ["MODULE_ID"],
    "default_sink": os.environ["DEFAULT_SINK"],
    "modes": os.environ["MODES"],
    "sample_frames": os.environ["FRAMES"],
    "maxqframes": int(os.environ["MAXQFRAMES"]),
    "game": os.environ["GAME"],
    "probe_so": os.environ["PROBE_SO"] or None,
    "input_script": os.environ["INPUT_SCRIPT"] or None,
}
Path(os.environ["RUN_ROOT"], "harness.json").write_text(
    json.dumps(meta, indent=2, sort_keys=True) + "\n")
PY

echo "audio_probe: captured runs in $run_root"
if ((do_analyze)); then
  python3 -I "$repo/tools/audio_probe_analyze.py" "$run_root" || exit $?
fi
echo "audio_probe: analyze with: python3 -I tools/audio_probe_analyze.py $run_root"
((had_failure == 0)) || exit 1
