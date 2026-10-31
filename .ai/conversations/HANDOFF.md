# Agent Handoff
Last updated: 2026-10-31 22:05 CET by claude-code

## Active Work
CachyOS desktop (RX 9070 XT, RADV, 4K TV at 150%), repo at ~/Projects/GiantsRecomp.
Last session: [sessions/2026-10-31/220500-claude-code-deterministic-boot-interrupts.md]
- 2026-10-31 (5): scheduler step 3 done: SDK 8562ed2 (POSIX alertable 0 ms waits) + 4153831
  (host threads on fixed turns via SetDue, idle/busy virtual frames, NoSwitchScope, adaptive
  quanta); main eb847f0 (bump + docs/netplay.md step 2/3). q1/q2: 408/491 buckets always match,
  11 diverge for good (first b9200000 @7790; b5f00000 flips from 7110); picture splits ~7920.
  Stale PID 23867 (my deadlocked p0 run) may still be alive: ask the user to kill it.
- 2026-10-31 (1-4): sync trace found host-timed sleeps + lock races; step 1 clock_frame_locked_waits
  (SDK aaa81e0); step 2 sched_deterministic (SDK 00b0697) + clock_frame_locked_stall_ms (5752e48):
  split 7560 -> 7920, memory differs from read ~7020 (m1/m2). Details in the session files.
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
- Pushed to the forks: SDK 48aa917, main (latest docs: handoff commit).

## Next Up
**BLOCKED ON USER (2026-10-31 22:10):** netplay phase 1 needs ENet added as a submodule
(`git submodule add https://github.com/lsalzman/enet.git thirdparty/enet`, pin v1.3.18). Auto
mode refused it (third-party code); ask the user to approve or add it themselves. Nothing was
left half-added.

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

**MILESTONE (2026-10-31 21:35):** with SDK 20dda51 (pushed; main 61a3f9a) the whole Chapter 1
recording replays deterministically in picture: w1/w2 (560 s) identical frames through 23880
(recording ends at read 21897), u1/u2 identical sync traces to shutdown. Memory: only host-data
buckets differ (0x30000000-0x301FFFFF kernel objects, 0x70000000-0x705FFFFF stacks), plus
INTERMITTENTLY 0xB5F00000/0xBB200000/0xBB300000 split once at read ~8060-8070 (level start) and
stay different without visible effect (w and u pairs: yes; v pair: no). Unknown writer outside
the scheduler. x1/x2 caught it (21:50): at read 8060, 10 words differ in 0xBB200000 (runs at
0xBB239C50 x6, 0xBB27BB14, 0xBB27BB24 x3): ASCII path fragments ("s/ved/", "aine x/m") in one
run, small binary values in the other. Guest memory is MAP_SHARED shm, so decommit keeps content
(not MADV_FREE-style). Likely a kernel export copying a host struct with uninitialized bytes
(host stack garbage) into a guest buffer. Next: a debug watch (check a guest word after every
kernel export call, log the export that changed it) on 0xBB239C50 around read 8050-8060.
Tool committed: cvar debug_guest_watch=<hex> (SDK, see git log). y1/y2 with it on 0xBB239C50:
the split didn't happen; watch sequences identical (239 changes) until shutdown. Splits seen in
u, w, x pairs, not in v, y: intermittent, cause unknown. Extra idle virtual frames in some logs
are all after 'Window closing' (shutdown), harmless. Low priority: pictures stay identical.
Determinism is good enough to start -> next is netplay phase 1 (lockstep transport, see
.ai/plans/splendid-sprouting-goblet.md Part 3). Design note: under sched_deterministic the guest's
controller reads come in a fixed order, so lockstep input can hook where input_replay does
(Filter in src/netplay/determinism.cpp), exchanging inputs keyed by read index (+delay), not by
frame. Both peers must run clock_frame_locked + sched_deterministic + audio_guest_paced.
Remaining host data in memory (kernel objects 0x30xxxxxx, stacks 0x70xxxxxx) must be excluded
from desync hashes (phase 4). User: "continue until quota runs out; update handoff".
**Where it stands (2026-10-31 19:20):** step 3 committed. Next: find who writes 0xB5F00000 /
0xB9200000 differently (replay pair with --state_dump_address=B5F00000, diff the .bin dumps,
sync trace around read 7110). Stats line every 600 frames in the game log shows token time.
**Earlier (2026-10-31 08:49):** steps 1-2 built. Step 2 = GuestScheduler run token
(SDK src/system/guest_scheduler.cpp) + codegen REX_LOOP_BACKEDGE() preemption (needs a codegen
rerun: `cmake --build out/build/linux-amd64-release --target giantsrecomp_codegen`, then build).
Test with `replay.sh <n> 600 - --audio_guest_paced=true --sched_deterministic=true` twice, then
cmp.py / hash_onset.py (state hashes: `--state_hash_log=... --state_hash_interval=10
--state_hash_start=7000`, delete old .hash first). Stuck runs: out/tmp/bot/helpers/gdbstack.sh.
Quanta (guest_scheduler.cpp): 16 sync ops / 1024 back-edges; larger is much slower.
Step 3 next: GPU interrupts and host completions at fixed guest points.
- Other open items: real-scene profile + upscaler A/B with the recording; upscale_quality / F4
  active-upscaler display / remove the fsr2 path; why Proton frame-locked runs differ; hands-on
  checks for the user (Wayland, F4 Graphics API + Restart now, DLSS/DLSS 5 on an RTX card).

## Recent Completions
- [2026-10-31 claude-code] Scheduler step 3: host threads on fixed turns, virtual frames by guest state (SDK 8562ed2, 4153831; main eb847f0) #feature
- [2026-10-31 claude-code] sched_deterministic run token + loop back-edge preemption (SDK 00b0697) #feature
- [2026-10-31 claude-code] clock_frame_locked_waits: guest-time sleeps/timeouts (SDK aaa81e0, main 97fb72d) #feature
- [2026-10-31 claude-code] Heap allocation order found as first gameplay divergence (32133d4) #investigation
- [2026-10-31 claude-code] audio_guest_paced + sync_trace_log (SDK, staged); divergence causes found #investigation
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
