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
| Wayland: present-wait/FIFO stall, relative mouse, fractional scaling | pending | — | — |
| Red tint on Vulkan | blocked (not reproducible on Intel/Mesa) | — | — |
| Linux vs Windows performance parity | pending (needs Windows box) | — | — |
| video_mode honours explicit 720p (render < window) | done | claude-code | 2026-10-30 |
| FSR 3.1 on Vulkan: context hang fixed (FFX scratch alignment) | done | claude-code | 2026-10-30 |
| FSR 3.1 real inputs: jitter, optical-flow MVs, const depth, once-per-frame | done (verify on 9070 XT) | claude-code | 2026-10-30 |
| FSR 4.1.2 (SDK 2.4 bump), DLSS 4/5 (NGX), XeSS 3.0.2 backends | pending | — | — |
| D3D12 temporal path parity | pending | — | — |
| Netplay phase 0 determinism tools + first idle test | done | claude-code | 2026-10-30 |
| Netplay: virtualize guest time, rerun determinism | pending | — | — |
| Netplay phases 1–4 (ENet, input sync, portal sync, desync UI) | pending | — | — |

## Blockers
- Work moves to a CachyOS desktop with an AMD RX 9070 XT (RDNA4, RADV). It can test FSR 3.1 on
  Vulkan properly (speed + visuals), retry the red tint on AMD, and profile Linux performance.
- FSR 4.1.2 needs RDNA2+ but ships as Windows DLLs only (no native Linux .so): on Linux it falls
  back to FSR 3.1; testing FSR 4 needs a Windows build on that card (or Proton).
- DLSS (RTX) and XeSS (Windows SDK) still can't be run on either machine: compile-check only.

## Key Decisions
- Motion vectors from present-time block matching (game-agnostic) first; game camera MVs later.
- Linux package is a tarball, not AppImage (game writes its toml next to the exe).
- DLSS via NGX directly (Linux-capable), not Streamline. Frame generation out of scope.
