# Upscaler backends and testing Windows on Linux (2026-10-30)

- **Graphics API switch:** `gpu_backend` in F4, restart-only, with fallback to the other compiled
  backend. Linux has no native D3D12, so "D3D12 on Linux" is the Windows build under Proton
  (vkd3d-proton); the native Linux binary stays Vulkan-only.
- **Windows build from Linux:** clang + xwin cross toolchain, run under Proton's wine with
  vkd3d-proton/DXVK overrides. This is how D3D12 and the Windows-only SDKs are tested here.
- **FidelityFX:** Linux keeps the source-built FSR 3.1 (AMD ships no .so). Windows uses AMD's
  signed SDK 2.4 DLLs (FSR 4.1.2 + 3.1) for both APIs, loaded per API through `ffx_runtime` because
  both loaders export the same symbols.
- **XeSS / DLSS:** one `temporal_upscaler` cvar (fsr/xess/dlss), restart-only because Vulkan needs
  their extensions at device creation. Both are fed the same temporal inputs as FSR and fall back to
  FSR on any failure. XeSS is loaded dynamically (no import lib); NGX is a static lib, as NVIDIA
  requires.
- **DLSS 5:** no official SDK; DLSS5VKLayer (AGPL, community) is used as an external, user-installed
  Vulkan layer toggled by `dlss5_layer`. Not vendored or linked.
