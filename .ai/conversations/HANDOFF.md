# Agent Handoff
Last updated: 2026-10-30 18:45 CET by claude-code

## Active Work
Now on the CachyOS desktop (RX 9070 XT, RADV, 4K TV at 150%), repo at ~/Projects/GiantsRecomp.
Last session: [sessions/2026-10-30/184130-claude-code-desktop-fsr-verify-netplay-time.md]
- FSR 3.1 (Vulkan) is verified on RDNA4 against native 4K, and the x jitter sign is fixed (main
  3fedb34). Method and numbers: docs/upscaling.md; rerun with `tools/upscaler_ab.sh`.
- Netplay is at the plan's STOP point. `clock_frame_locked` (SDK 36b3e1c) makes rendering repeatable,
  but guest memory diverges from boot (heap allocation order, host audio). The user has to pick
  an option from docs/netplay.md (host streaming / deterministic scheduling / gameplay-state check
  first, which is recommended).
- Local commits NOT pushed: SDK 360fc95, 36b3e1c, eea516c; main 3fedb34..d6b8f9b. Ask before
  pushing; SDK first.

## Next Up
1. The user's netplay decision (above).
2. A gameplay input recording (`--input_record=out/tmp/play.inputlog`, 2-3 min in a level). Then
   rerun the FSR A/B on hard edges and profile a real scene (the title screen uses only 0.2 CPU
   cores and 28% GPU).
3. Hands-on Wayland checks: mouse look (relative mode), F4/F6 overlays at 150%, game running while
   minimized. The code paths look right (IMMEDIATE present on RADV, so no FIFO stall).
4. Upscaler interface + D3D12 parity, FSR SDK 2.4 (FSR 4 falls back to 3.1 on Linux), NGX DLSS,
   XeSS. Windows presets need a Windows host; a clang-cl + ~/opt/xwin toolchain would be new work.

## Recent Completions
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
- Game logs: `out/build/linux-amd64-release/logs/` (newest file).
