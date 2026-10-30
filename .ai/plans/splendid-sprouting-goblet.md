# Plan: Online netplay, proper Linux/Vulkan support, FSR 3 + FSR 4.1.2

## Context

Giants Recompiled currently plays well on Windows/D3D12 in single player. You asked for three things:

1. **Online multiplayer** as true netplay: two PCs each run the game and stay in sync, with no video streaming.
2. **Linux on Vulkan** as a real target. It already builds and runs, but it has a red tint, it is slower than D3D12, `present_effect` cas/fsr is unavailable because the `linux-base` preset doesn't enable FidelityFX, and there is no package.
3. **Temporal upscalers, implemented properly:** FSR 3.1, FSR 4.1.2, DLSS 4, DLSS 5 and the latest XeSS (SDK 3.0.2, Super Resolution).

What exploring the code showed:
- **Local 2P co-op is mostly done already.** `SlotAssignment` in `rexglue-sdk/src/input/input_system.cpp` sends physical pad N to guest user N. Synthetic devices (keyboard/MnK) always go to user 0. Netplay will reuse this: the remote peer's input is fed in as guest user 1.
- **The SDK's "FSR2/FSR3" option is not really temporal.** `VulkanPresenter::DispatchTemporalUpscaler` (`src/ui/vulkan/vulkan_presenter.cpp`) passes the colour image in as depth and as motion vectors, uses zero jitter, sets `reset=true` every frame and hardcodes the camera values. It runs at present time, on the final image with the HUD already in it. A real implementation has to replace this.
- **The game hooks we need already exist.** `src/hooks/ultrawide_hook.cpp` hooks `BuildPerspectiveProjectionMatrix` (where jitter goes in, and where the camera matrices get captured) and `BuildOrthographicProjectionMatrix` (the first HUD/2D draw in a frame, which is where to cut off the 3D scene).
- All five upscalers need the **same inputs**: the colour image before the HUD, depth, motion vectors, the projection jitter, and camera values. The expensive part is producing those inputs, and it only has to be built once. After that, each vendor is a small backend behind one interface.
- What each SDK supports on each platform:

  | Upscaler | SDK | Windows D3D12 | Windows Vulkan | Linux Vulkan | GPU |
  | --- | --- | --- | --- | --- | --- |
  | FSR 3.1 | FidelityFX `ffx-api` (open source) | yes | yes | yes (built from source) | any |
  | FSR 4.1.2 | AMD FSR SDK 2.4 (prebuilt DLLs) | yes | yes | no native `.so`, so it falls back to 3.1 | RDNA2+ |
  | DLSS 4 SR | NVIDIA DLSS SDK (NGX) 310.x | yes | yes | yes (the DLSS SDK officially supports Linux) | RTX |
  | DLSS 5 | not found as an official developer SDK yet, only community bridges | gated | gated | gated | RTX |
  | XeSS 3.0.2 SR | Intel XeSS SDK (prebuilt DLLs) | yes | yes | no native `.so` | any (XMX on Arc) |

- Use the NGX DLSS SDK directly, not Streamline. Streamline is Windows-only, while NGX gives one code path that also runs on Linux.
- Leave frame generation out (FSR FG, DLSS FG, XeSS-FG). It wasn't asked for, and XeSS-FG is DirectX-only.
- All SDK work goes into the fork `TheBiemGamer/rexglue-sdk`, followed by a submodule bump here (the pattern in commits `be8cbda` and `02fc93f`). The submodules aren't checked out in this clone yet, so the first step is `git submodule update --init --recursive`.

Order: Linux first (it's the smallest piece, and FSR on Vulkan needs the red tint fixed first), then FSR, then netplay (the largest and riskiest).

---

## Part 1: Linux / Vulkan

1. **Enable FidelityFX on Linux.** Add `"REXGLUE_ENABLE_FIDELITYFX": "ON"` to `linux-base` in `CMakePresets.json`. `cmake/rexglue_fidelityfx.cmake` already checks for Vulkan SDK ≥ 1.3.250 and glslc/glslangValidator. Add both to the Linux prerequisites in `docs/development.md`.
2. **Fix the red tint.** Take a RenderDoc capture of the title screen on Vulkan and compare it with the same frame on D3D12 (or PIX), pass by pass. Likely causes, in order:
   - the gamma ramp / PWL apply pass (`apply_gamma_*`)
   - the swizzle or format of guest output `k_2_10_10_10` / `k_2_10_10_10_FLOAT` (7e3) on Vulkan
   - BGRA vs RGBA in the Vulkan texture cache or presenter

   Fix it once, in the fork, then remove the README "red tint" row.
3. **Performance parity.** Profile Release on Linux and Windows with the same scene and hardware, using Tracy if the SDK has it and `perf` otherwise. Usual suspects in the Vulkan backend are pipeline cache misses or stalls (`src/graphics/vulkan/pipeline_cache.cpp`), submission/fence waits (`submission_tracker`) and descriptor allocation. Fix the top 2–3 hotspots in the fork. Check whether `vulkan_device` auto-pick prefers the discrete GPU, and fix that so the README laptop workaround can go.
4. **Packaging.** Add a `just appimage` recipe that builds `linux-amd64-release` and bundles it with `linuxdeploy`. That puts the binary, the SDL/hidapi `.so`s and `giantsrecomp.toml.example` in the AppImage, with no game data.
   - Add `installer/linux-setup.sh`, the Linux counterpart of the Inno Setup steps in `installer/GiantsRecomp.iss`. It asks for the ISO or folder, extracts it with `xdvdfs`/`extract-xiso` if one is on PATH, runs `giantsrecomp_xexcheck`, and writes `giantsrecomp.toml` from `installer/settings_template.toml`.
   - Add a udev rule snippet so the USB portal works without root, and document it.
   - Add the Linux artifact to `docs/releasing.md`.

5. **Wayland compatibility.** SDL already picks its native Wayland driver (the log says `SDL video driver: wayland`), so this is about making that path solid, with XWayland as a fallback:
   - **Presentation:** under Wayland, FIFO present can block indefinitely while the window is hidden, minimized or on another workspace, which would stall the guest. Prefer `VK_PRESENT_MODE_MAILBOX_KHR`, or FIFO plus `VK_EXT_present_wait`/`fifo_latest_ready` with a timeout, and confirm the game keeps running while the window is hidden.
   - **Mouse:** Wayland doesn't allow warping the pointer, which `MnkInputDriver::RecenterCursorFromUIThread` relies on. Use SDL relative mouse mode (`zwp_relative_pointer` + `pointer_constraints`), which `relative_mouse_mode_` already models, and check camera look with `mnk_mode`. The cursor auto-hide must also work.
   - **Window:** `resolution` sizing, fullscreen/borderless toggling, server-side vs. libdecor decorations, and resizing (swapchain recreation).
   - **Fractional scaling:** at 125%/150%, check the swapchain extent against the window's pixel size so the image isn't blurry or cropped. Also check ImGui overlay scaling and mouse coordinates in F4/F6.
   - **Other:** keyboard focus and the `OnLostFocus` input release, and controller hotplug (SDL, display-independent).
   - **Escape hatch:** document `SDL_VIDEODRIVER=x11` (XWayland) as a fallback, and log which driver is active (this already happens).
   - **Verify on:** KDE Plasma and GNOME (Mutter), each with native Wayland and with `SDL_VIDEODRIVER=x11`.

## Part 2: Temporal upscaling (FSR 3.1 / 4.1.2, DLSS 4 / 5, XeSS 3)

The current present-time path stays as the fallback. The new path runs **before the HUD is drawn**, inside the GPU command processor. FSR 3.1 on Vulkan is the first backend, because it's open source and works on every GPU and on Linux, which makes it the easiest to debug. The others come after the shared inputs are proven.

1. **SDK dependencies** (fork, `cmake/`):
   - Bump `rexglue_fidelityfx.cmake` (currently `rexglue/FidelityFX-SDK@eee08db`) to FSR SDK 2.4.
   - Add `rexglue_dlss.cmake` for the NGX headers and libs, shipping `nvngx_dlss.dll` / `libnvidia-ngx-dlss.so.*`.
   - Add `rexglue_xess.cmake` for the XeSS 3.0.2 headers, import lib and `libxess.dll`. It is Windows-only and behind `REXGLUE_ENABLE_XESS`.
   - Each backend gets its own `REXGLUE_ENABLE_*` option, so a build without one still works.
   - Ship the vendor DLLs next to the exe. List them in `installer/GiantsRecomp.iss`, the AppImage, and the README credits and licences: NVIDIA's DLSS licence and Intel's XeSS licence both allow redistribution with attribution.
2. **Lower render resolution.** Add a cvar `upscaler` (`off|fsr3|fsr4|dlss|xess`) and an `upscale_quality` preset. The scene renders at `output / ratio`. The render target cache already scales per `resolution_scale`; extend it to non-integer draw resolution, or set the internal scale so the scene RT is smaller than the window.
   `upscale_quality` covers native AA (DLAA / FSR Native AA) through performance. `auto` picks DLSS on RTX, FSR 4 on RDNA2+, XeSS on Arc, and FSR 3.1 everywhere else.
3. **Jitter.** In `BuildPerspectiveProjectionMatrix` (`src/hooks/ultrawide_hook.cpp`), add a Halton(2,3) sub-pixel offset to the projection's [2][0]/[2][1] terms. While doing that, store the unjittered view-projection, near/far and FOV for this frame and the previous one in a small struct the SDK can read (a setter on the GPU plugin). That replaces the hardcoded 0.1/1000/1.0472.
4. **Find the scene/HUD split.** The first `BuildOrthographicProjectionMatrix` call each frame (the same caller-LR check used for `ultrawide_hud`) sets a "HUD begins" flag. The command processor reads it at the next draw. At that point the current colour and depth RTs in the render target cache are the finished 3D scene.
5. **Motion vectors.**
   - **Camera MVs:** a compute pass (SPIR-V and DXBC) rebuilds each pixel's position from depth using inv(VP_cur) and reprojects it with VP_prev into an RG16F MV texture. Static geometry, which is most of the screen, comes out right.
   - **Per-object MVs**, needed because characters ghost without them: in `spirv_translator`/`dxbc_translator`, give non-ortho vertex shaders a second clip-space output computed from the previous frame's VS float constants for the same draw. The draw key is (VS hash, vertex fetch address, index base). The pixel shader writes the difference to an extra MV render target. This is the riskiest graphics step, so get it working on Vulkan first.
6. **One upscaler interface, one backend per vendor.** At the split point the command processor fills a single `UpscaleInputs` struct:
   - colour, depth and MV images
   - jitter, frame delta and camera values
   - `reset`, which is set only on cuts (a large VP change)
   - the reactive mask

   It then calls `Upscaler::Dispatch(cmd, inputs, output)`. Put the interface in `include/rex/ui/upscaler.h` with one small file per backend for each graphics API: `upscaler_ffx.cpp`, `upscaler_dlss.cpp`, `upscaler_xess.cpp`.
   - **FFX:** `ffxCreateContext` / `ffxDispatchDescUpscale`. This replaces `VulkanPresenter::DispatchTemporalUpscaler`.
   - **DLSS:** `NVSDK_NGX_{D3D12,VULKAN}_Init`, a capability check for `NVSDK_NGX_Feature_SuperSampling`, `NGX_D3D12/VULKAN_CREATE_DLSS_EXT` with the render preset set to the transformer model (DLSS 4), and `NGX_*_EVALUATE_DLSS_EXT`. The Vulkan path needs the instance and device extensions that `NVSDK_NGX_VULKAN_RequiredExtensions` returns, added in `src/ui/vulkan/instance.cpp`/`device.cpp`.
   - **XeSS:** `xessD3D12CreateContext` / `xessVKCreateContext`, `xess*Init` with the quality preset, and `xess*Execute`.
   - **DLSS 5:** at the start of Part 2, check whether NVIDIA has an official developer SDK. If it ships as a newer NGX/DLSS model or preset, it is one more preset value in the DLSS backend. If it is a different feature, such as neural rendering rather than super resolution, or it only exists through community bridges, leave it out and report back. Unofficial NGX DLLs won't be bundled.
   - Every backend writes into a window-resolution output image. If the selected backend fails to create (wrong GPU, missing DLL), log the reason and fall back to FSR 3.1.
7. **The HUD over the upscaled scene.** After the dispatch, draws go to a separate render-resolution RGBA layer cleared to transparent. The presenter composites that layer over the upscaled scene. This means the guest's final copy of the scene to the front buffer has to be redirected while upscaling is on, using the render target cache's resolve path.
   - **Fallback if redirecting proves too invasive:** keep the HUD in place. Mark HUD pixels by writing a mask during ortho draws, then pass it as FSR's `reactive`/`transparencyAndComposition` with MV = 0.
8. **Provider selection.**
   - FSR 4 vs 3.1: use `ffxQueryDescGetVersions` / `ffxOverrideVersion`.
   - Handle MV and depth conventions per backend. DLSS takes MVs in pixels, and it needs the `MVLowRes` and `DepthInverted` flags set to match the Xenos depth range; XeSS and FFX each take their own MV scale.
   - The F4 overlay lists only the backends that initialised on this GPU and shows which one is active.
9. Remove the fake `kFsr2/kFsr3` present path once the new path works, and document the new cvars in `giantsrecomp.toml.example` and the README.

## Part 3: Online netplay (lockstep, 2 players)

The model is **delay-based lockstep**. Each frame, the peers swap inputs. Both run the same frame with both players' inputs, and the remote player is guest user 1. Rollback is out of scope because the SDK has no save states for guest threads or GPU state.

**Phase 0: determinism gate (do this first, with a time limit).** Add input record/replay: a cvar `input_record=<file>` / `input_replay=<file>` that logs per-frame `X_INPUT_STATE` for users 0 and 1 plus portal events. Replay the same file on two runs, hash the game's guest heap every 60 frames, and compare.
- If the runs diverge, find the sources and make them deterministic, starting with:
  - guest time (`src/system/xtimer.cpp`, `xboxkrnl_rtl.cpp` `KeQuerySystemTime`, `KeQueryPerformanceCounter`): derive it from the frame counter while netplay is on
  - RNG seeding
  - async file I/O completion order
  - the frame limiter: lock it to 60
- **If the runs still diverge** because of guest thread scheduling, stop and come back with options: host-authoritative streaming, or single-threading the game's simulation threads. Don't keep building on a design that desyncs.

**Phase 1: transport and session.**
- Add the ENet submodule (a small C library with reliable plus unreliable UDP channels) under `thirdparty/`.
- Use direct IP with a configurable port and no matchmaking or relay: `netplay_host = true`, `netplay_connect = "ip:port"`.
- Handshake: check that both sides have the same XEX SHA-256 (`GIANTSRECOMP_XEX_SHA256`) and the same build hash.
- The host sends its save files and settings that affect gameplay, so both sides start from the same state. The client uses a temporary user data folder so its own saves are never overwritten.
- Both sides start from process launch with lockstep from frame 0.

**Phase 2: input sync.**
- Add `NetplayInputDriver` and `NetplayAssignment` in a new `src/netplay/` (app side, following the `src/hooks` layout). Use the SDK's `InputSystem::SetDeviceAssignment`; no SDK change is needed.
- Every guest frame (from the frame counter `CommandProcessor::guest_frame_count()` already used by `ComputeFrameStats`), send local input for frame N+delay (default 3) and block in `GetState` until the remote input for frame N arrives.
- Vibration stays local.

**Phase 3: portal sync.** Netplay forces `portal_mode = "software"` on both sides.
- When a figure is placed or removed on either side (the F6 picker, or the USB portal through dumping), send the 1 KB figure data with a target frame. Both sides apply it at that frame through `SoftwarePortal::PlaceFigure`/`RemoveFigure` (`thirdparty/skylanders-portal`).
- Writes back to the figure (level, gold) happen in-game on both sides. Only the host saves them to disk.

**Phase 4: desync detection and UX.**
- Send a guest heap hash every 120 frames. On a mismatch, show an overlay message and pause; resyncing by transferring state is out of scope.
- Add an F8 netplay overlay with host/join, ping, delay and status, built like the F6 overlay registered in `OnCreateDialogs` in `src/giantsrecomp_app.h`.

---

## Critical files

- `CMakePresets.json`, `CMakeLists.txt` (new `src/netplay/*.cpp`, ENet), `justfile`, `installer/`, `docs/development.md`, `docs/releasing.md`, `README.md`, `giantsrecomp.toml.example`
- `src/hooks/ultrawide_hook.cpp` (jitter, camera capture, HUD split)
- `src/giantsrecomp_app.h` (netplay setup in `OnPostSetup` and its overlay)
- Fork: `cmake/rexglue_{fidelityfx,dlss,xess}.cmake`, new `include/rex/ui/upscaler.h` + `src/ui/{vulkan,d3d12}/upscaler_{ffx,dlss,xess}.cpp`, `src/ui/vulkan/{instance,device}.cpp` (NGX extensions), `src/ui/vulkan/vulkan_presenter.cpp`, `src/ui/d3d12/d3d12_presenter.cpp`, `src/graphics/{vulkan,d3d12}/command_processor.cpp` and `render_target_cache.cpp`, `src/graphics/pipeline/shader/{spirv,dxbc}_translator*.cpp`, `src/system/xtimer.cpp`, `src/kernel/xboxkrnl/xboxkrnl_rtl.cpp`

## Verification

- **Wayland:** the game keeps running (the guest frame counter still advances) while minimized or on another workspace. Mouse look works in relative mode. Fullscreen toggles, and resizing works. Output is sharp at 150% fractional scaling. Check all of this on KDE and GNOME, under both Wayland and XWayland.
- **Linux:** a clean build with `just build-release` on Linux. The title screen and a story level look the same as Windows in screenshots (no tint). Compare FPS on both platforms with the F3 overlay at the same spot. The AppImage runs on a clean distro VM or container with `rom/` next to it.
- **Upscalers:**
  - In RenderDoc, the MV texture shows motion that matches the camera and characters, and the depth and jitter values are sane.
  - Check each backend side by side for ghosting behind a running character, and check that the HUD is sharp and doesn't move with the scene.
  - Run the backend matrix on real hardware:
    - an RTX card: DLSS on Windows D3D12, Windows Vulkan and Linux
    - an RDNA2+ card: FSR 4 on Windows and the FSR 3.1 fallback on Linux
    - an Arc card or any other GPU: XeSS on Windows
  - Confirm the provider in the log and the F4 overlay.
  - With a vendor DLL missing, the game falls back to FSR 3.1 instead of crashing.
  - `ctest` stays green.
- **Netplay:** the Phase 0 replay hashes match over a 10-minute recorded run, including a level load. Then two instances on localhost play 30 minutes of co-op with no hash mismatch, and the same over LAN. Test placing a figure on each side, and test disconnect handling.
- Add unit tests for the input-frame queue / delay logic and the replay file parser (in `tests/`, same style as `xex_verify_test.cpp`).
