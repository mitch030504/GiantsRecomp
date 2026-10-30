# Project: Giants Recompiled

## Overview
Unofficial PC port of the Xbox 360 game Skylanders Giants (v1.0 USA/EU only), made by static
recompilation with the ReXGlue SDK (Xenia-derived runtime). Supports a virtual (software) or real
USB Portal of Power. The repo ships no game data: users supply their own disc in `rom/`.

## Tech Stack
- **Language:** C++23 (clang 20+; this laptop has clang 23), CMake 3.25+, Ninja, optional `just`
- **Runtime:** ReXGlue SDK fork `thirdparty/rexglue-sdk` (TheBiemGamer/rexglue-sdk) — kernel/XAM
  emulation, Xenos GPU plugin (D3D12 on Windows, Vulkan on Linux), SDL3 window/input, ImGui overlays
- **Key packages:** `thirdparty/skylanders-portal` (portal emulation, hidapi), FidelityFX SDK
  (fetched at configure time, patched in `rexglue_fidelityfx.cmake`), glslang
- **Recompiled code:** `generated/default/` (from the user's own `rom/default.xex`, git-ignored)

## Architecture
Single executable `giantsrecompiled`. Game-specific hooks in `src/hooks/` patch recompiled
functions (`REX_HOOK_RAW`), settings are SDK cvars in `giantsrecomp.toml` / `--flag=value`.
Most engine work (GPU, presenter, input, kernel) happens in the SDK fork, then the submodule is
bumped here.

## Key Documents
| Document | Path | Purpose |
|----------|------|---------|
| Current plan | `.ai/plans/splendid-sprouting-goblet.md` | Linux/Wayland, upscalers, netplay plan |
| Readme | `README.md` | User-facing setup, settings, known issues |
| Architecture | `docs/architecture.md` | Layout, portal layering, settings |
| Development | `docs/development.md` | Building from source (Windows/Linux) |
| Reverse engineering | `docs/reverse-engineering.md` | Naming functions with Ghidra |
| Function names | `config/default.toml` | Codegen fixes + RE'd function names |

## Conventions
- Conventional commits (`feat:`, `fix:`, `build:`, `docs:`, `re:`); SDK changes committed in the
  submodule first, then `build: bump rexglue-sdk to <hash>` here.
- Comments explain the why; match surrounding style. String cvars need `--flag=value`.

## How to Run
- Build: `cmake --preset linux-amd64-release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++`
  (first time: `--target giantsrecomp_codegen`, reconfigure, build), or `just build-release`
- Run (Linux, from repo root): `LD_LIBRARY_PATH=thirdparty/rexglue-sdk/out/linux-amd64 ./out/build/linux-amd64-release/giantsrecompiled --resolution_scale=1`
- Tests: `ctest --test-dir out/build/linux-amd64-release`

## How to Deploy
`just package-installer` (Windows Inno Setup) / `just package-linux` (tarball). See `docs/releasing.md`.
