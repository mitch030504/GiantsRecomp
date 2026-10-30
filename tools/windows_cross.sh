#!/usr/bin/env bash
# Build the Windows version on Linux and run it under Proton's Wine, so D3D12 (through
# vkd3d-proton) and the Windows-only upscaler SDKs can be tested without a Windows PC.
#
#   tools/windows_cross.sh build          # cross-compile (needs the native linux-amd64-release build)
#   tools/windows_cross.sh run [ARGS...]  # run it, e.g. --gpu_backend=vulkan
#
# Needs clang, lld, llvm-rc/-mt/-ar, wine (for the FidelityFX shader compiler), an `xwin splat` in
# XWIN_DIR (default ~/opt/xwin) and Proton in PROTON_DIR (default Steam's "Proton 10.0").
# run defaults to a headless 1920x1080 gamescope; set HEADLESS=0 to open a normal window, and
# SECONDS_TO_RUN=N to stop the game after N seconds.
set -euo pipefail

cd "$(dirname "$0")/.."
preset=win-amd64-release-xwin
build_dir="out/build/$preset"
proton_dir="${PROTON_DIR:-$HOME/.steam/steam/steamapps/common/Proton 10.0}"
prefix="$PWD/out/tmp/proton-prefix"

case "${1:-}" in
  build)
    if [[ ! -x thirdparty/rexglue-sdk/out/linux-amd64/rexglue ]]; then
      echo "Build the native release first (cmake --build --preset linux-amd64-release):" >&2
      echo "codegen runs its rexglue." >&2
      exit 1
    fi
    [[ -f "$build_dir/build.ninja" ]] || cmake --preset "$preset"
    WINEPREFIX="$PWD/out/tmp/wine-build" WINEDEBUG=-all cmake --build --preset "$preset"
    # The SDK stages its runtime DLL and the GPU plugin next to the exe, but not Tracy's.
    cp --remove-destination thirdparty/rexglue-sdk/out/win-amd64/TracyClient.dll "$build_dir/"
    ;;
  run)
    shift
    if [[ ! -d "$prefix/pfx" ]]; then
      # Proton sets up the prefix (vkd3d-proton, DXVK) the first time it runs something.
      mkdir -p "$prefix"
      STEAM_COMPAT_DATA_PATH="$prefix" STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.steam/steam" \
        timeout -k 5 300 "$proton_dir/proton" run wineboot -u || true
    fi
    mkdir -p out/tmp/vkd3d-cache
    # Running Proton's wine directly skips the proton script, which is what makes Wine load
    # vkd3d-proton and DXVK instead of its own builtin d3d12/dxgi; ask for them the same way.
    cmd=(env WINEPREFIX="$prefix/pfx" WINEDEBUG="${WINEDEBUG:--all}"
         WINEDLLOVERRIDES="d3d12=n;d3d12core=n;dxgi=n;d3d11=n;d3d10core=n${WINEDLLOVERRIDES:+;$WINEDLLOVERRIDES}"
         VKD3D_SHADER_CACHE_PATH="$PWD/out/tmp/vkd3d-cache"
         "$proton_dir/files/bin/wine" "$build_dir/giantsrecompiled.exe" --game_data_root=rom "$@")
    if [[ "${HEADLESS:-1}" == 1 ]]; then
      cmd=(gamescope --backend headless -W 1920 -H 1080 -- env -u WAYLAND_DISPLAY "${cmd[@]}")
    fi
    if [[ -n "${SECONDS_TO_RUN:-}" ]]; then
      cmd=(timeout -k 5 "$SECONDS_TO_RUN" "${cmd[@]}")
    fi
    "${cmd[@]}" || true
    WINEPREFIX="$prefix/pfx" "$proton_dir/files/bin/wineserver" -k 2>/dev/null || true
    # Wine's device services can outlive the server when gamescope is torn down; stop anything
    # still running in this prefix so runs don't pile up.
    for pid in $(pgrep -f 'wine|\.exe' || true); do
      if tr '\0' '\n' < "/proc/$pid/environ" 2>/dev/null | grep -qx "WINEPREFIX=$prefix/pfx"; then
        kill -9 "$pid" 2>/dev/null || true
      fi
    done
    ;;
  *)
    sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
