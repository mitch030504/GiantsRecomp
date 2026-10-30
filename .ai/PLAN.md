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
| FSR 4.1.2 (SDK 2.4 bump), DLSS 4/5 (NGX), XeSS 3.0.2 backends | pending | — | — |
| D3D12 temporal path parity | pending | — | — |
| Netplay phase 0 determinism tools + first idle test | done | claude-code | 2026-10-30 |
| Netplay: virtualize guest time, rerun determinism | done: clock_frame_locked; rendering repeatable, memory still diverges from boot (heap order, audio) → STOP: user picks an option in docs/netplay.md | claude-code | 2026-10-30 |
| Netplay phases 1–4 (ENet, input sync, portal sync, desync UI) | blocked on the determinism decision | — | — |

## Blockers
- Netplay: guest memory diverges between identical runs from boot (heap allocation order follows
  thread scheduling; host audio writes). Needs the user's choice in docs/netplay.md.
- D3D12 parity / FSR 4 / XeSS can't be built or run on Linux: the Windows presets need a Windows
  host (a clang-cl + ~/opt/xwin toolchain file would be new work).
- FSR 4.1.2 needs RDNA2+ but ships as Windows DLLs only (no native Linux .so): on Linux it falls
  back to FSR 3.1; testing FSR 4 needs a Windows build on that card (or Proton).
- DLSS (RTX) and XeSS (Windows SDK) still can't be run on either machine: compile-check only.

## Key Decisions
- Motion vectors from present-time block matching (game-agnostic) first; game camera MVs later.
- Linux package is a tarball, not AppImage (game writes its toml next to the exe).
- DLSS via NGX directly (Linux-capable), not Streamline. Frame generation out of scope.
