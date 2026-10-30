---
agent: claude-code
started: 2026-10-30 13:40 CET
task: Linux/Wayland port, temporal upscalers (FSR 3/4, DLSS, XeSS), online netplay — first pass
tags: [#feature, #bugfix, #setup]
---

## Summary
Planned the work (`.ai/plans/splendid-sprouting-goblet.md`), made Linux build and present
correctly (incl. Wayland), added Linux packaging, netplay determinism tooling, and started the real
FSR 3.1 temporal path on Vulkan. Game files are reflink-copied into `rom/` (git-ignored).

## Files Created/Modified
Committed (main `feat/linux-upscalers-netplay`: 4563c31, 89987f8, 65ddff2, 0b74700;
SDK `giants/linux-vulkan`: 3928a28, f825115, 4988e27, 951084a, a73acce, 2c4437a, ce2b55c).
Uncommitted (in progress, builds, validation-clean):
- SDK: `include/rex/ui/temporal_jitter.h`, `src/ui/temporal_jitter.cpp` — jitter + guest swap count
- SDK: `include/rex/ui/vulkan/temporal_inputs.h`, `src/ui/vulkan/vulkan_temporal_inputs.cpp`,
  `src/ui/shaders/temporal_motion_cs.glsl` (+ SPIR-V header) — motion estimation, const depth
- SDK: `vulkan_presenter.cpp/.h` — real FSR dispatch inputs, repaint skip, LDR/jitter-cancel flags,
  `present_temporal_dump_prefix` readback dump, dispatch failure warning
- SDK: `presenter.h/.cpp`, `command_processor.cpp`, `xboxkrnl_video.cpp` — guest frame index plumbing
- SDK: `vulkan_device.cpp`, `device.h` — enable shaderInt16/shaderFloat16 (FFX 16-bit shaders)
- SDK: `device_1_0.inc` — vkCmdCopyImage
- `src/hooks/ultrawide_hook.cpp` — projection jitter while the temporal upscaler is active

## Decisions Made
- Optical-flow MVs at present time, constant depth 0.5; HUD protected by zero-motion bias.
- Jitter every perspective build per frame by the same NDC offset (~9 builds/frame observed).
- video_mode_* default 0 = follow window; window follows `resolution`.

## Issues/Blockers Found
- FIXED (SDK c1bf1cb): frame index stuck at 0 because Presenter's layout differed between rexui
  and the GPU plugin (REX_HAS_FIDELITYFX_SDK-guarded members). Original note:
- was OPEN: with `--present_effect=fsr3 --video_mode_width=1280 --video_mode_height=720` the FSR 3.1.4
  context is created but no `present_temporal_dump_prefix` images appear and no "dispatch failed"
  warning is logged — find out whether `DispatchTemporalUpscaler` reaches ffxDispatch (Record
  failing? `TemporalUpscalerHasFrame` true too often?). Jitter sign is unverified until images exist.
- Pre-existing: VUID-vkDestroySwapchainKHR-swapchain-01282 at window setup.
- Red tint not reproducible on Intel/Mesa; Windows perf comparison not possible here.
- Netplay: idle record/replay shows heap-pointer, time-counter and audio-buffer divergence.

## What's Next
- Debug the missing FSR output dumps, then visually verify jitter sign/ghosting; commit + bump SDK.
- Wayland items (present stall, relative mouse, fractional scaling); D3D12 temporal parity.
- FSR SDK 2.4 bump (FSR 4.1.2), NGX DLSS and XeSS backends behind one interface.
- Netplay: virtualize guest time, rerun determinism; push branches when the user says so.

## References
- `.ai/plans/splendid-sprouting-goblet.md`, `config/default.toml` (camera RE notes)
- FFX source under `out/build/linux-amd64-release/_deps/fidelityfx-src` (MV convention: uv + mv)
