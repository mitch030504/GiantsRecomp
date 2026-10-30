# Session: graphics API switch, Windows build on Linux, D3D12/FSR 4/XeSS/DLSS, DLSS 5 layer
Agent: claude-code (Opus 5.5)
Date: 2026-10-30 21:00–22:35 CET

## Done (all committed; SDK pushed up to eea516c, main up to 044a0c2 before this work)
- SDK e71c645: `gpu_backend` cvar (auto/d3d12/vulkan; falls back to the other one) and F4
  "Restart now" (`rex::LaunchCopyOfSelf`, baseline of restart-only cvars in ReXApp).
- SDK 009c400: Windows builds Vulkan too. SDK 85d0fab + main 851cdf8: Windows cross-build on Linux
  (`cmake/toolchains/windows-xwin-clang.cmake`, preset win-amd64-release-xwin,
  `tools/windows_cross.sh build|run`), host rexglue for codegen in cross builds (template change).
- SDK 180cfaf: D3D12 temporal inputs (HLSL motion shader, fxc via wine), D3D12 temporal dump,
  FidelityFX SDK 2.4 prebuilt DLLs on Windows behind `ffx_runtime` (loader per API).
- SDK 8fb185f: `dlss5_layer` (DLSS5VKLayer, Linux). SDK 7752196: XeSS 3.0.2 on D3D12 + Vulkan.
  SDK 7c31e50: DLSS (NGX 310.9.16) on D3D12 + Vulkan, Linux + Windows. Main a28fd5d, 167ea1d.

## Results (RX 9070 XT)
- Windows build under Proton: D3D12 (vkd3d-proton) and Vulkan both run, 0 errors.
- D3D12 FSR 3.1 vs native 4K: 36.24 dB / edge 6.46 (Vulkan native 36.08 / 6.32).
- FidelityFX 2.4 picks FSR 4.1.2 on Vulkan under Proton; FSR 3.1.6 on D3D12 (vkd3d).
- XeSS runs on both APIs under Proton (libxess 2.0.2, "ultra quality" preset for 720p->1080p).
- DLSS: NGX refuses on AMD (0xBAD00001) and the game falls back to FSR, natively and under Proton.

## Gotchas
- Running Proton's `wine` directly loads Wine's builtin vkd3d, not vkd3d-proton: every RT resolve
  failed (MSAA dump shaders don't compile). WINEDLLOVERRIDES="d3d12=n;d3d12core=n;dxgi=n;..." fixes
  it; both scripts set it.
- Under Proton, clock_frame_locked runs aren't bit-identical (native Linux runs are), so Proton A/B
  numbers swing (FSR 4: 36.4 vs 42.6 dB in two runs). The shipped jitter sign still wins for FSR 4.
- xwin needs: -idirafter (not -isystem, or MSVC intrin.h shadows clang's), llvm-ar (not llvm-lib),
  release CRT only, case symlinks for ObjBase.h / SDKDDKVer.h and ffx_FrameInterpolation.h.
- XeSS VK rejects VK_REMAINING_* in subresource ranges and wants mutable-descriptor-type features.
- NGX: use lib/Windows_x86_64/x64/nvsdk_ngx_d.lib (the vs2010 one fails /failifmismatch).
- `cmake ... | head` kills configure mid-way (SIGPIPE). The linux build dir needs
  -DCMAKE_C_COMPILER=clang -DCMAKE_CXX_COMPILER=clang++ when reconfigured (no clang-20 on PATH).

## Not done / next
- Netplay option 3 and the real-scene profile: need the user's gameplay recording.
- Hands-on: Wayland checks, F4 Graphics API dropdown + Restart now, DLSS / DLSS 5 on an RTX card.
- Small leftovers: `upscale_quality` preset and active-provider display in F4; remove the old
  fsr2 present path; Proton frame-lock determinism.
- Not pushed: SDK e71c645..7c31e50, main 851cdf8..167ea1d (+ this docs commit). Ask first.
