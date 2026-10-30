# Agent Handoff
Last updated: 2026-10-30 22:35 CET by claude-code

## Active Work
CachyOS desktop (RX 9070 XT, RADV, 4K TV at 150%), repo at ~/Projects/GiantsRecomp.
Last session: [sessions/2026-10-30/223500-claude-code-backend-switch-windows-cross-upscalers.md]
- F4 → GPU → gpu_backend switches D3D12/Vulkan (Windows has both; Linux Vulkan only; D3D12 on
  Linux = the Windows build under Proton). temporal_upscaler = fsr | xess | dlss.
- The Windows build cross-compiles here (`tools/windows_cross.sh build`, needs the native release
  build first) and runs under Proton 10 (`SECONDS_TO_RUN=60 tools/windows_cross.sh run ...`).
- Windows FSR comes from AMD's SDK 2.4 DLLs (FSR 4.1.2 on Vulkan/RDNA4 under Proton). XeSS works on
  both APIs under Proton. DLSS falls back to FSR here (no RTX). dlss5_layer drives DLSS5VKLayer.
- Netplay: the user picked option 3 (compare gameplay state first); waiting for a recording.
- Not pushed: SDK e71c645..7c31e50, main 851cdf8..HEAD. Ask before pushing; SDK first.

## Next Up
1. The user's gameplay recording (`--clock_frame_locked=true --input_record=out/tmp/play.inputlog`,
   2-3 min in a level with a load). Then netplay option 3 (state_watch ranges, 3x replay) and a
   real-scene profile on native Vulkan and Proton D3D12/Vulkan.
2. Hands-on checks (user): Wayland mouse look / F4 at 150% / minimized; F4 Graphics API + Restart
   now; DLSS and DLSS 5 on an RTX card.
3. Minor: upscale_quality preset, active upscaler shown in F4, remove the fsr2 present path,
   find why frame-locked runs differ under Proton.

## Recent Completions
- [2026-10-30 claude-code] DLSS via NGX on D3D12+Vulkan, Linux+Windows (SDK 7c31e50, main 167ea1d) #feature
- [2026-10-30 claude-code] XeSS 3.0.2 on D3D12+Vulkan (SDK 7752196) #feature
- [2026-10-30 claude-code] dlss5_layer for DLSS5VKLayer (SDK 8fb185f) #feature
- [2026-10-30 claude-code] D3D12 temporal parity + FidelityFX SDK 2.4 on Windows (SDK 180cfaf, main a28fd5d) #feature
- [2026-10-30 claude-code] gpu_backend + Restart now, Windows cross-build + Proton runner (SDK e71c645..85d0fab, main 851cdf8) #feature #tooling
- [2026-10-30 claude-code] Swapchain retired while presenting: VUID-01282 gone (SDK eea516c) #bugfix
- [2026-10-30 claude-code] Netplay determinism rerun with frame-locked time + docs/netplay.md (f7dff47) #investigation
- [2026-10-30 claude-code] clock_frame_locked + perf_log_csv fix (SDK 36b3e1c, 360fc95; bump 19b08bc) #feature #bugfix
- [2026-10-30 claude-code] FSR 3.1 verified vs native 4K; x jitter flipped; tools/upscaler_ab.sh (3fedb34) #bugfix
- [2026-10-30 claude-code] Red tint retried on RADV: not reproducible (original maintainer's report) #investigation
- [2026-10-30 claude-code] FSR 3.1 real inputs + Presenter ODR fix + plugin staging fix (SDK c1bf1cb..05a33af) #feature #bugfix
- [2026-10-30 claude-code] FidelityFX Vulkan scratch-alignment fix (SDK ce2b55c) — FSR context no longer hangs #bugfix
- [2026-10-30 claude-code] video_mode 0=auto, window follows resolution (SDK 2c4437a) #bugfix
- [2026-10-30 claude-code] Wayland blank window: stop dlopen'ing RenderDoc (SDK a73acce) #bugfix
- [2026-10-30 claude-code] Netplay phase 0: input record/replay, state hashes, compare tool (65ddff2) #feature
- [2026-10-30 claude-code] Linux tarball + linux-setup.sh + udev rule (89987f8) #feature
- [2026-10-30 claude-code] Linux links again + FidelityFX builds on Arch (SDK 3928a28, f825115) #bugfix

## Pending Decisions
- `.gitmodules` points the SDK at the fork (mitch030504/rexglue-sdk) on this branch; switch it back
  to TheBiemGamer before any upstream PR.

## Key Context
- Branches are pushed to forks (remote `fork`): github.com/mitch030504/GiantsRecomp
  `feat/linux-upscalers-netplay`, github.com/mitch030504/rexglue-sdk `giants/linux-vulkan`.
  `origin` (TheBiemGamer) has no write access for account mitch030504. Push SDK first.
- Old laptop (where this started): Intel Iris Xe, clang 23, no `just`, `/tmp` a 7.7 GB tmpfs.
  Keep big files out of `/tmp` on any machine; use `out/tmp/` (git-ignored).
- Never screenshot the desktop; use `--guest_output_dump_prefix=` / `--present_temporal_dump_prefix=`.
- String cvars need `--flag=value` (space form sets them to "true").
- Upscale test: `tools/upscaler_ab.sh` (headless gamescope 4K, frame-locked, vs native 4K).
  Manual: `--present_effect=fsr3 --video_mode_width=1920 --video_mode_height=1080` in a 4K window.
- The Bash tool runs zsh: never pass several flags through one unquoted `$VAR` (they arrive as ONE
  argument). GPU tests while the TV may be off: `gamescope --backend headless -W 3840 -H 2160 --
  env -u WAYLAND_DISPLAY ...`. Compare runs frame by frame only with `--clock_frame_locked=true`.
- Perf counters (perf_log_csv, the F4 graph) exist only in non-Release builds.
- Validation: `VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation`; errors appear as "Vulkan Error" in logs.
- Proton runs: use Proton's wine with WINEDLLOVERRIDES for d3d12/dxgi (scripts do it); Wine's builtin
  vkd3d breaks resolves. Windows logs: out/build/win-amd64-release-xwin/logs/. Proton frame-locked
  runs are not bit-identical, so Windows A/B numbers are rough.
- Reconfiguring out/build/linux-amd64-release needs -DCMAKE_C_COMPILER=clang
  -DCMAKE_CXX_COMPILER=clang++ (no clang-20 on PATH). Never pipe cmake configure into head.
- Game logs: `out/build/linux-amd64-release/logs/` (newest file).
