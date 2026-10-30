# Agent Handoff
Last updated: 2026-10-30 23:50 CET by claude-code

## Active Work
CachyOS desktop (RX 9070 XT, RADV, 4K TV at 150%), repo at ~/Projects/GiantsRecomp.
Last session: [sessions/2026-10-30/223500-claude-code-backend-switch-windows-cross-upscalers.md]
- F4 → GPU → gpu_backend switches D3D12/Vulkan (Windows has both; Linux Vulkan only; D3D12 on
  Linux = the Windows build under Proton). temporal_upscaler = fsr | xess | dlss.
- The Windows build cross-compiles here (`tools/windows_cross.sh build`, needs the native release
  build first) and runs under Proton 10 (`SECONDS_TO_RUN=60 tools/windows_cross.sh run ...`).
- Windows FSR comes from AMD's SDK 2.4 DLLs (FSR 4.1.2 on Vulkan/RDNA4 under Proton). XeSS works on
  both APIs under Proton. DLSS falls back to FSR here (no RTX). dlss5_layer drives DLSS5VKLayer.
- Netplay option 3 done: identical inputs diverge ~10 s into Chapter 1 play, so input lockstep
  needs determinism first. Decision made: determinism route (see Next Up).
- Gameplay recordings are automated: `tools/bot_record.sh` (headless, ~7 min) writes
  out/tmp/play.inputlog; replay with --portal_bot_figure=Spyro --clock_frame_locked=true.
- Pushed: SDK up to 7c31e50, main up to 69e4baf. Not pushed: main 33bc5e8..HEAD (bot, netplay
  result, handoff). Ask before pushing.

## Next Up
The user chose the **determinism route** ("whatever you think is best"): they want netplay that
is as insensitive to lag as possible. The plan agreed in chat:
1. Make the game deterministic (option 2): fixed guest thread order, and audio and timers paced by
   guest time. The goal is two replays of out/tmp/play.inputlog staying pixel-identical for the
   full ~2.5 min.
2. Delay-based lockstep (input delay adapted to ping) as the first playable online version.
3. Rollback (save/restore guest memory + threads + GPU state) on top, if it can be made fast enough.
The Unraid server (ssh root@192.168.8.2; i9 Alder Lake-HX, UHD 770 iGPU, already runs Nakama for
another project + Nginx Proxy Manager) only does lobby/join codes, NAT traversal, relay and a
shared clock. It does NOT run the game (no netcode in the game; the iGPU is too weak). The user
asked why a dedicated server wouldn't fix sync; the answer was that the game has no netcode, so
the server would have to stream (lag) or need the same determinism anyway.

**Where the time-boxed experiments stand (none run yet):**
- Baseline: two replays split ~10 s into play (frame ~7680; they match through the level load).
- Helpers (git-ignored, in out/tmp/bot/helpers/): `replay.sh <name> <secs> <cores|-> [args]`
  replays play.inputlog headless with dumps every 120 frames; `cmp.py <dirA> <dirB>` prints the
  first frame where two runs split; `bot_run.sh`/`sheet.py` run a bot script and make contact
  sheets (view sheets with the Read tool; never screenshot the desktop).
- Experiment 1 (next): pin each replay to one core (`replay.sh e1a 300 2`, `replay.sh e1b 300 3`,
  then `python3 -I out/tmp/bot/helpers/cmp.py out/tmp/bot/e1a out/tmp/bot/e1b`) to see how much
  thread parallelism matters.
- Experiment 2: a guest-time audio driver. The SDL driver (SDK src/audio/sdl/sdl_audio_driver.cpp)
  releases the game's audio semaphore from SDL's callback, so the guest audio callback
  (AudioSystem::WorkerThreadMain, which runs guest code) is paced by the sound card. With
  clock_frame_locked, release 48000/60/256 = 3.125 buffers per VdSwap instead
  (rex::ui::temporal::OnGuestSwap is the hook point). The null backend can't be used: its
  CreateDriver fails.
- Then: cooperative scheduling of guest threads (one runs at a time, switching at blocking kernel
  calls), the real fix, if 1+2 don't get far.
- Other open items: real-scene profile + upscaler A/B with the recording; upscale_quality / F4
  active-upscaler display / remove the fsr2 path; why Proton frame-locked runs differ; hands-on
  checks for the user (Wayland, F4 Graphics API + Restart now, DLSS/DLSS 5 on an RTX card).

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
