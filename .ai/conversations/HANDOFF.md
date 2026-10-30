# Agent Handoff
Last updated: 2026-10-30 16:53 CET by claude-code

## Active Work
- FSR 3.1 temporal path on Vulkan: WORKING and committed (SDK 05a33af; jitter hook in main).
  Verified on Intel: upscaled 720p->1080p dumps are sharper than bilinear with no ghost trails.
  - Last session: [sessions/2026-10-30/154429-claude-code-linux-upscalers-netplay.md]
  - Still to do: strict jitter-sign A/B, quality on a real GPU, D3D12 parity, FSR 4.1.2 / DLSS / XeSS.
  - Root causes found on the way: Presenter layout differed between libraries (ODR, SDK c1bf1cb);
    GPU plugin staging could go stale (SDK 90cf5b8).

## Resuming on the RX 9070 XT desktop (CachyOS)
This repo arrived as `GiantsRecomp-handoff-2026-10-30.zip` (working tree incl. uncommitted work,
both git repos with history, `rom/` game files, `generated/` code; no `out/` build dir).
1. Unzip to `~/Projects/GiantsRecomp` (same path keeps Claude Code's memory key and `codegen.d`).
2. `sudo pacman -S --needed clang cmake ninja git vulkan-headers shaderc glslang wine python`
   (`just` optional). Check `clang --version`; if there's no `clang-20`, pass the compilers:
   `cmake --preset linux-amd64-release -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++`
3. Build: `cmake --build out/build/linux-amd64-release` (if it complains about generated code:
   `--target giantsrecomp_codegen`, reconfigure, build). Then `ctest --test-dir out/build/linux-amd64-release`.
4. Restore Claude memory: `mkdir -p ~/.claude/projects/-home-$USER-Projects-GiantsRecomp/memory &&
   cp .ai/claude-memory/*.md ~/.claude/projects/-home-$USER-Projects-GiantsRecomp/memory/`
   (the laptop-specific notes there — Iris Xe, tmpfs /tmp — describe the OLD machine).
5. First jobs on this GPU (RADV, RDNA4):
   - Finish the FSR 3.1 path (see Active Work), compare `--present_effect=fsr3` vs `fsr` dumps.
   - Retry the README red tint: AMD/RADV may reproduce what Intel didn't.
   - Profile Linux performance with a real GPU (old laptop was GPU-bound at 4K internal).
   - FSR 4.1.2: hardware supports it, but AMD ships it as Windows DLLs only; on Linux expect the
     FSR 3.1 fallback. Wire the SDK 2.4 bump + version select anyway; verify on Windows/Proton.
   - DLSS and XeSS remain compile-only here.

## Recent Completions
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
- Upscale test: `--resolution=1920x1080 --resolution_scale=1 --present_effect=fsr3 --video_mode_width=1280 --video_mode_height=720`
  (+ `--present_temporal_dump_prefix=out/tmp/fsr --guest_output_dump_prefix=out/tmp/in` for images).
- Validation: `VK_INSTANCE_LAYERS=VK_LAYER_KHRONOS_validation`; errors appear as "Vulkan Error" in logs.
- Game logs: `out/build/linux-amd64-release/logs/` (newest file).
