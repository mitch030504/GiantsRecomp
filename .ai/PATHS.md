# Key Paths

## Application Code
- `src/giantsrecomp_app.h` — ReXApp subclass: setup hooks, fonts, overlays, portal + netplay tools install
- `src/hooks/ultrawide_hook.cpp` — projection hooks (ultrawide FOV/HUD, temporal-upscaler jitter)
- `src/hooks/intro_skip_hook.cpp` — skips logo movies
- `src/netplay/` — input record/replay (`input_log.*`) and determinism tools (`determinism.*`)
- `src/xex_verify.*`, `src/tools/xex_check_cli.cpp` — pinned default.xex SHA-256 check
- `generated/` — recompiled code (git-ignored except `rexglue.cmake`)

## SDK fork (thirdparty/rexglue-sdk, branch giants/linux-vulkan)
- `src/ui/vulkan/vulkan_presenter.cpp` — Vulkan presenter, effect chain, FSR dispatch, temporal dump
- `src/ui/vulkan/vulkan_temporal_inputs.cpp` + `include/rex/ui/vulkan/temporal_inputs.h` — motion/depth/color history
- `src/ui/shaders/temporal_motion_cs.glsl` (+ `vulkan_spirv/temporal_motion_cs.h`) — block-matching motion estimation
- `include/rex/ui/temporal_jitter.h`, `src/ui/temporal_jitter.cpp` — shared jitter + guest swap count
- `src/core/clock.cpp`, `include/rex/chrono/clock.h` — guest clock; `clock_frame_locked` (deterministic time)
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
- `tools/compare_state_hashes.py` — diff two `state_hash_log` runs
- `tools/upscaler_ab.sh`, `tools/upscaler_ab_analyze.py` — headless frame-locked FSR A/B vs native 4K
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
- `docs/upscaling.md` — FSR 3.1 temporal path, A/B method, 9070 XT results
- `docs/netplay.md` — determinism tools, frame-locked time, findings + options
### Agent & Project Config
- `CLAUDE.md`, `AGENTS.md`, `codex.md` — agent handoff snippet
- `README.md` — project readme

## Feature Specs / Plans
No formal spec system. Active plan: `.ai/plans/splendid-sprouting-goblet.md` (copy of the approved Claude Code plan).

## Agent Handoff
- `.ai/claude-memory/` — copy of Claude Code's project memory (restore into ~/.claude on a new machine)
- `.ai/` — cross-agent bridge; `.ai/conversations/HANDOFF.md` state; `.ai/conversations/LOG.md` history
