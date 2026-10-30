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
#
# TARGET=windows-d3d12 or windows-vulkan runs the cross-compiled Windows build under Proton instead
# (tools/windows_cross.sh build first), with only the default jitter (L_flip0; FLIPS="0 1 2 3" for
# all four), into
# out/tmp/upscaler_ab_<target>/.
set -euo pipefail
secs=${1:-36}
shift || true
target=${TARGET:-linux}
proton_dir="${PROTON_DIR:-$HOME/.steam/steam/steamapps/common/Proton 10.0}"
case "$target" in
  linux)
    out=out/tmp/upscaler_ab
    logs=out/build/linux-amd64-release/logs
    game=(env LD_LIBRARY_PATH=thirdparty/rexglue-sdk/out/linux-amd64
          ./out/build/linux-amd64-release/giantsrecompiled)
    flips=(0 1 2 3)
    ;;
  windows-d3d12 | windows-vulkan)
    out=out/tmp/upscaler_ab_$target
    logs=out/build/win-amd64-release-xwin/logs
    mkdir -p out/tmp/vkd3d-cache
    # The same DLL overrides as tools/windows_cross.sh: vkd3d-proton and DXVK, not Wine's builtins.
    game=(env WINEPREFIX="$PWD/out/tmp/proton-prefix/pfx" WINEDEBUG=-all
          WINEDLLOVERRIDES="d3d12=n;d3d12core=n;dxgi=n;d3d11=n;d3d10core=n"
          VKD3D_SHADER_CACHE_PATH="$PWD/out/tmp/vkd3d-cache" "$proton_dir/files/bin/wine"
          out/build/win-amd64-release-xwin/giantsrecompiled.exe --game_data_root=rom
          --gpu_backend="${target#windows-}")
    read -ra flips <<< "${FLIPS:-0}"
    ;;
  *)
    echo "TARGET must be linux, windows-d3d12 or windows-vulkan" >&2
    exit 1
    ;;
esac

run() {
  local name=$1
  shift
  # Relative, so the Windows build under Wine gets a path it can open.
  local dir=$out/$name
  rm -rf "$dir"
  mkdir -p "$dir"
  timeout -k 5 -s INT "$secs" gamescope --backend headless -W 3840 -H 2160 -w 3840 -h 2160 -- \
    env -u WAYLAND_DISPLAY "${game[@]}" --resolution=3840x2160 --vsync=false \
    --clock_frame_locked=true --guest_output_dump_interval=30 --present_temporal_dump_interval=30 \
    --guest_output_dump_prefix="$dir/in" --present_temporal_dump_prefix="$dir/out" "$@" \
    >"$dir/stdout.log" 2>&1 || true
  if [[ "$target" != linux ]]; then
    WINEPREFIX="$PWD/out/tmp/proton-prefix/pfx" "$proton_dir/files/bin/wineserver" -k 2>/dev/null || true
  fi
  cp "$(ls -t "$logs"/* | head -1)" "$dir/game.log"
}

run L_ref4k --resolution_scale=2
run L_native --resolution_scale=1
run L_native2 --resolution_scale=1
for flip in "${flips[@]}"; do
  run "L_flip$flip" --resolution_scale=1 --present_effect=fsr3 --temporal_jitter_flip=$flip "$@"
done
python3 -I tools/upscaler_ab_analyze.py "$out"
