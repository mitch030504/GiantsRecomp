# Key Paths

## Application Code
- `src/giantsrecomp_app.h` — ReXApp subclass: setup hooks, fonts, overlays, portal + netplay tools install
- `src/hooks/ultrawide_hook.cpp` — projection hooks (ultrawide FOV/HUD, temporal-upscaler jitter)
- `src/hooks/intro_skip_hook.cpp` — skips logo movies
- `src/netplay/` — ENet `session.*`, `lockstep.*`, `netplay.*` app integration, `save_snapshot.*`, `figure_sync.*`, plus input record/replay and determinism tools
- `src/xex_verify.*`, `src/tools/xex_check_cli.cpp` — pinned default.xex SHA-256 check
- `generated/` — recompiled code (git-ignored except `rexglue.cmake`)

## SDK fork (thirdparty/rexglue-sdk, branch giants/linux-vulkan)
- `src/ui/vulkan/vulkan_presenter.cpp` — Vulkan presenter, effect chain, FSR dispatch, temporal dump
- `src/ui/vulkan/vulkan_temporal_inputs.cpp` + `include/rex/ui/vulkan/temporal_inputs.h` — motion/depth/color history
- `src/ui/shaders/temporal_motion_cs.glsl` (+ `vulkan_spirv/temporal_motion_cs.h`) — block-matching motion estimation
- `include/rex/ui/temporal_jitter.h`, `src/ui/temporal_jitter.cpp` — shared jitter + guest swap count
- `src/core/clock.cpp`, `include/rex/chrono/clock.h` — guest clock; `clock_frame_locked` (deterministic time), guest-time waits
- `src/system/guest_scheduler.cpp`, `include/rex/system/guest_scheduler.h` — `sched_deterministic` run token
- `resources/templates/codegen/pch_h.inja`, `src/codegen/builders/{control_flow,context}.cpp` — REX_LOOP_BACKEDGE preemption
- `src/system/xobject.cpp` — guest waits (guest-time deadlines, scheduler path, sync_trace_log)
- `src/ui/d3d12/d3d12_presenter.cpp` — D3D12 presenter (temporal path not yet updated)
- `src/input/input_system.cpp` — device assignment, state/keystroke filter hook
- `src/kernel/xboxkrnl/xboxkrnl_video.cpp` — VdSwap, guest video mode
- `cmake/rexglue_fidelityfx.cmake` — FidelityFX fetch + Linux patches (brace escape, scratch alignment)

## Configuration
- `giantsrecomp_manifest.toml`, `config/default.toml` — codegen project + fixes/names
- `giantsrecomp.toml.example` — settings template; `installer/settings_template.toml` for installers
- `CMakePresets.json`, `justfile`

## Tests
- `tests/` — `xex_verify_test`, `input_log_test`, CLI tests (ctest)

## Tools
- `tools/netplay_smoke.py`, `tools/bot/netplay-story.txt` — isolated headless two-peer validation (actual loading blocker documented)
- `tasks/plan.md`, `tasks/todo.md` — netplay implementation checklist
- `thirdparty/enet` — ENet1.3.18 pinned UDP transport
- `tools/compare_state_hashes.py` — diff two `state_hash_log` runs
- `out/tmp/bot/helpers/` (git-ignored) — replay.sh, cmp.py, sync_frames.py, hash_onset.py, gdbstack.sh, threads.sh
- `tools/upscaler_ab.sh`, `tools/upscaler_ab_analyze.py` — headless frame-locked FSR A/B vs native 4K (TARGET=windows-d3d12|windows-vulkan for the Proton build)
- `tools/windows_cross.sh`, `cmake/toolchains/windows-xwin-clang.cmake` — Windows build on Linux (xwin) and Proton runner
- SDK `src/ui/{ffx,xess,dlss}_runtime.*` — upscaler runtimes; `src/ui/d3d12/d3d12_temporal_inputs.cpp` — D3D12 motion/depth/history
- SDK `cmake/rexglue_{fidelityfx_prebuilt,xess,dlss}.cmake` — pinned vendor downloads (FidelityFX 2.4, XeSS 3.0.2, DLSS 310.9.16)
- `tools/ghidra_*.py`, `tools/rexglue_toml_sync.py` — RE workflow

## Installer / packaging
- `installer/GiantsRecomp.iss` — Windows installer
- `installer/linux-setup.sh`, `installer/70-portal-of-power.rules` — Linux tarball setup + udev rule

## Reference Documents (current)
### Design / Architecture
- `docs/architecture.md` — layout, portal layering, settings
- `docs/portal-protocol.md`, `docs/figures.md` — portal wire format, figures
### Guides
- `docs/development.md` — build from source; `docs/build.md` — codegen fixes, testing
- `docs/releasing.md` — release packaging (Windows + Linux)
- `docs/reverse-engineering.md` — Ghidra workflow
- `docs/upscaling.md` — temporal path (FSR 3.1/4.1.2, XeSS, DLSS), A/B method, 9070 XT + Proton results
- `docs/netplay.md` — determinism tools, frame-locked time, findings + options
### Agent & Project Config
- `CLAUDE.md`, `AGENTS.md`, `codex.md` — agent handoff snippet
- `README.md` — project readme

## Feature Specs / Plans
- `.ai/conversations/decisions/2026-11-01-netplay-session-boundaries.md` — implemented session/save/figure authority decisions and validation limits
No formal spec system. Active plan: `.ai/plans/splendid-sprouting-goblet.md` (copy of the approved Claude Code plan).

## Agent Handoff
- `.ai/claude-memory/` — copy of Claude Code's project memory (restore into ~/.claude on a new machine)
- `.ai/` — cross-agent bridge; `.ai/conversations/HANDOFF.md` state; `.ai/conversations/LOG.md` history
