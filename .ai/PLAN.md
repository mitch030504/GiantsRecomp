# Current Plan

## Active Work
**Linux/Wayland, temporal upscalers, online netplay** — branch `feat/linux-upscalers-netplay`
(SDK: `giants/linux-vulkan`). Full plan: `.ai/plans/splendid-sprouting-goblet.md`.

## Task Status
| Task | Status | Agent | Date |
|------|--------|-------|------|
| Linux builds (frame_rate_limit link fix, FidelityFX brace fix) | done | claude-code | 2026-10-30 |
| Linux tarball + `linux-setup.sh` + udev rule | done (ISO path untested) | claude-code | 2026-10-30 |
| Wayland: blank window (RenderDoc injection) | done | claude-code | 2026-10-30 |
| Wayland: present-wait/FIFO stall, relative mouse, fractional scaling | code checked; needs hands-on test (IMMEDIATE chosen on RADV/KDE so no FIFO stall; relative mode already used; 150% renders at full density) | claude-code | 2026-10-30 |
| Red tint on Vulkan | not reproducible on Intel ANV or AMD RADV; reported by the original maintainer (user confirms it wasn't theirs) | claude-code | 2026-10-30 |
| Linux vs Windows performance parity | pending (needs Windows box) | — | — |
| video_mode honours explicit 720p (render < window) | done | claude-code | 2026-10-30 |
| FSR 3.1 on Vulkan: context hang fixed (FFX scratch alignment) | done | claude-code | 2026-10-30 |
| FSR 3.1 real inputs: jitter, optical-flow MVs, const depth, once-per-frame | done | claude-code | 2026-10-30 |
| FSR 3.1 verified on RX 9070 XT: jitter sign A/B vs native 4K (x flipped), quality + GPU cost (docs/upscaling.md) | done | claude-code | 2026-10-30 |
| Vulkan validation: swapchain retired while presenting (VUID-01282) | done | claude-code | 2026-10-30 |
| Linux performance profile on the 9070 XT | title screen only: 0.2 CPU cores, 28% GPU at 60 fps; needs a gameplay input recording to go further | claude-code | 2026-10-30 |
| Graphics API switch (gpu_backend in F4 + Restart now), Vulkan in Windows builds | done | claude-code | 2026-10-30 |
| Windows cross-build on Linux (xwin) + Proton runner (tools/windows_cross.sh) | done | claude-code | 2026-10-30 |
| D3D12 temporal path parity (real inputs, dump) | done: 36.24 dB vs Vulkan 36.08 | claude-code | 2026-10-30 |
| FSR 4.1.2 (FidelityFX SDK 2.4 prebuilt, Windows, D3D12 + Vulkan) | done: FSR 4.1.2 on Vulkan under Proton, 3.1.6 on vkd3d | claude-code | 2026-10-30 |
| XeSS 3.0.2 SR on D3D12 + Vulkan (Windows) | done, runs under Proton | claude-code | 2026-10-30 |
| DLSS SR (NGX 310.9.16), Linux + Windows | done, fallback verified; needs RTX test | claude-code | 2026-10-30 |
| DLSS 5 via DLSS5VKLayer (dlss5_layer, Linux) | done, needs NVIDIA test | claude-code | 2026-10-30 |
| upscale_quality preset, active provider in F4, drop fsr2 path | pending (minor) | — | — |
| Proton frame-lock determinism (A/B numbers swing) | pending | — | — |
| Netplay phase 0 determinism tools + first idle test | done | claude-code | 2026-10-30 |
| Netplay: virtualize guest time, rerun determinism | done: clock_frame_locked; rendering repeatable, memory still diverges from boot (heap order, audio) → STOP: user picks an option in docs/netplay.md | claude-code | 2026-10-30 |
| Bot recording (input_script + portal_bot_figure + tools/bot_record.sh) | done: ~2.5 min Chapter 1 with level load, headless | claude-code | 2026-10-30 |
| Netplay option 3 (gameplay-state compare), chosen by the user | done: gameplay diverges ~10 s into play with identical inputs | claude-code | 2026-10-30 |
| Netplay determinism exp. 1-2 (single core, audio_guest_paced) + sync_trace_log | done: neither fixes it; causes = host-timed sleeps/timeouts + lock races (docs/netplay.md) | claude-code | 2026-10-31 |
| Deterministic scheduler step 1: sleeps/timeouts in guest time (clock_frame_locked_waits) | done: pollers match per frame; split remains (heap allocation order) | claude-code | 2026-10-31 |
| Deterministic scheduler step 2: run token, fixed handoff order (sched_deterministic) | built, full speed (SDK 00b0697); determinism result pending (k1/k2 run) | claude-code | 2026-10-31 |
| Deterministic scheduler step 3: host events (GPU interrupts, I/O, notify) at fixed guest points | pending | — | — |
| Netplay phases 1–4 (ENet, input sync, portal sync, desync UI) | blocked on the determinism decision | — | — |

## Blockers
- Netplay: guest memory diverges between identical runs from boot (heap allocation order follows
  thread scheduling; host audio writes). Needs the user's choice in docs/netplay.md.
- Netplay: needs a deterministic game first (chosen route); Unraid = lobby/relay only.
- FSR 4.1.2 and XeSS ship Windows DLLs only: native Linux keeps FSR 3.1; the Windows build under
  Proton gets them (FSR 4 on Vulkan).
- DLSS / DLSS 5 can't run here (no RTX card): fallback paths verified only.

## Key Decisions
- Motion vectors from present-time block matching (game-agnostic) first; game camera MVs later.
- Linux package is a tarball, not AppImage (game writes its toml next to the exe).
- DLSS via NGX directly (Linux-capable), not Streamline. Frame generation out of scope.
