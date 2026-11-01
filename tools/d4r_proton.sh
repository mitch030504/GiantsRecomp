#!/usr/bin/env bash
# Test an extracted d4r Linux release with a staged Windows build; see docs/upscaling.md.
# Usage: tools/d4r_proton.sh STAGED_DIRECTORY [SECONDS] [GAME_ARGS...]
# shortcut: extra arguments must not repeat fixed flags below; add override handling if needed.
set -euo pipefail
root=$(cd "$(dirname "$0")/.." && pwd)
stage=$(realpath "${1:?Pass the directory containing the staged exe and d4r Linux release}")
shift
seconds=${1:-180}
shift || true
[[ "$seconds" =~ ^[1-9][0-9]*$ ]] || { echo 'SECONDS must be positive' >&2; exit 1; }
proton=${PROTON_DIR:-$HOME/.steam/steam/compatibilitytools.d/Proton-GE Latest}
[[ -f "$stage/giantsrecompiled.exe" && -x "$proton/proton" ]] || {
  echo 'Missing staged Windows executable or Proton; see docs/upscaling.md' >&2; exit 1;
}
mkdir -p "$root/out/tmp"
out=$(mktemp -d "$root/out/tmp/d4r-run-XXXXXXXX")
mkdir -p "$out/compat" "$out/saves" "$out/figures"
winpath() { printf 'Z:%s' "${1//\//\\}"; }
cleanup() {
  WINEPREFIX="$out/compat/pfx" "$proton/files/bin/wineserver" -k 2>/dev/null || true
}
trap cleanup EXIT
echo "Artifacts: $out"
cd "$stage"
# A global GE OptiScaler override otherwise intercepts NGX before the bundled d4r proxy.
set +e
started=$SECONDS
env -u PROTON_USE_OPTISCALER -u PROTON_OPTISCALER_CONFIG \
  STEAM_COMPAT_DATA_PATH="$out/compat" STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.steam/steam" \
  PROTON_FORCE_NVAPI=1 DXVK_NVAPI_GPU_ARCH=AD100 \
  WINEDLLOVERRIDES='dxgi=n;d3d12=n;d3d12core=n' SDL_AUDIO_DRIVER=dummy \
  WINEDEBUG="${WINEDEBUG:--all}" D4R_PROFILE=1 D4R_SHIM_LOG="$(winpath "$out/d4r.log")" \
  timeout -k 10 -s INT "$seconds" \
  gamescope --backend headless -W 3840 -H 2160 -w 3840 -h 2160 -- \
  env -u WAYLAND_DISPLAY "$proton/proton" run "$stage/giantsrecompiled.exe" \
  --game_data_root="$(winpath "$root/rom")" --user_data_root="$(winpath "$out/saves")" \
  --portal_figures_dir="$(winpath "$out/figures")" --portal_ipc=false \
  --gpu_backend=d3d12 --present_effect=fsr3 --temporal_upscaler=dlss \
  --resolution=3840x2160 --resolution_scale=1 --vsync=false --clock_frame_locked=true \
  --frame_rate_limit=60 --clock_frame_locked_hz=60 --audio_guest_paced=true --audio_mute=true \
  --log_file="$(winpath "$out/game.log")" \
  --guest_output_dump_prefix="$(winpath "$out/in")" \
  --present_temporal_dump_prefix="$(winpath "$out/out")" \
  --guest_output_dump_interval=120 --present_temporal_dump_interval=120 "$@" \
  >"$out/stdout.log" 2>&1
result=$?
elapsed=$((SECONDS - started))
set -e
echo "Runner exit: $result (124/137 means the time limit ended the run)"
if [[ "$result" != 0 && "$result" != 124 && "$result" != 137 ]]; then exit "$result"; fi
if (( elapsed < seconds )); then
  echo "Game exited early after ${elapsed}s; inspect stdout.log and game.log" >&2; exit 1;
fi
[[ -s "$out/game.log" ]] || { echo 'Game did not start; inspect stdout.log' >&2; exit 1; }
samples=$(grep -c 'D4R_PROFILE frame=' "$out/d4r.log" 2>/dev/null || true)
if [[ ${samples:-0} -lt 2 ]] || ! grep -q 'D3D12Presenter: DLSS [0-9]' "$out/game.log" ||
   grep -q 'using FidelityFX instead' "$out/game.log"; then
  echo 'DLSS validation failed: require repeated d4r evaluations and no FSR fallback' >&2
  exit 1
fi
echo "DLSS validated: $samples d4r evaluation samples; inspect output PPMs for image quality"
