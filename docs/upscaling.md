# Temporal upscaling (FSR 3.1 / FSR 4.1.2 / XeSS)

`present_effect = "fsr3"` runs a temporal upscaler when the guest output is smaller than the window,
for example `video_mode_width/height = 1920x1080` (or 1280x720) in a 4K window. It works on Vulkan
and D3D12. `temporal_upscaler` picks which one:

| `temporal_upscaler` | Linux build | Windows build |
| --- | --- | --- |
| `fsr` (default) | FSR 3.1, built from source | AMD's FidelityFX SDK 2.4 DLLs: FSR 4.1.2 where the GPU and driver support it, FSR 3.1 otherwise |
| `xess` | not available (Intel ships no Linux library) | Intel XeSS 3.0.2 (`libxess.dll`), on D3D12 and Vulkan |
| `dlss` | NVIDIA DLSS (NGX 310.9.16), Vulkan, RTX GPUs | the same, on D3D12 and Vulkan |

If the chosen upscaler can't start, the game logs why and uses FidelityFX.

## How it works

The upscaler runs at present time, on the finished guest frame:

- **Jitter:** while it's active, `BuildPerspectiveProjectionMatrix` (`src/hooks/ultrawide_hook.cpp`)
  shifts every 3D projection by a Halton(2,3) sub-pixel offset keyed to the guest frame index
  (`rex/ui/temporal_jitter.h`), and the presenter tells FSR the same offset.
- **Motion vectors:** the shipping path estimates them by block matching between this frame and the
  last one (`temporal_motion_cs`). An experimental engine guide (`motion_guide`, development only,
  default off) carries per-pixel motion from scene draws; it is not wired to the upscaler yet.
- **Depth:** constant. The HUD is protected by a bias towards zero motion.

The 2D UI is drawn before the upscaler sees the frame, so it gets upscaled with the scene.

## Measuring it

`tools/upscaler_ab.sh` renders the title screen in headless gamescope sessions at 3840x2160, with
`clock_frame_locked` so the same guest frame shows the same camera and sky in every run. The runs
are a native-4K reference (`resolution_scale=2`), plain 1080p, and FSR 3.1 1080p->4K with each
`temporal_jitter_flip` (a development cvar that negates the projection jitter per axis while FSR is
still told the original offset). `tools/upscaler_ab_analyze.py` compares every FSR output with the
reference of the same frame. It masks out the 2D logo and text and the title's rock particles,
which still differ between runs. It reports PSNR and a gradient-weighted RMS error, which
concentrates on edges, where a wrong jitter shows.

Wrong jitter signs show up two ways: the output has *more* high-frequency energy than its own input
(aliasing that FSR couldn't resolve), and it's further from the reference. Use the reference
comparison; sharpness alone rewards the wrong sign.

## Results (RX 9070 XT, RADV, 2026-10-30)

Jitter sign, 46 frames, paired against the shipped sign (± is the standard error):

| Jitter | PSNR | Edge error |
| --- | --- | --- |
| shipped: window moves by (+jx, +jy) | 36.08 dB | 6.32 |
| previous x sign | -1.02 ± 0.05 dB | +0.38 ± 0.05 |
| y negated | -1.22 ± 0.16 dB | +1.14 ± 0.12 |
| both negated | -1.22 ± 0.18 dB | +0.78 ± 0.15 |

- RCAS sharpening off (`present_fsr_sharpness_reduction = 2`) was worse: -0.7 dB, +0.79 edge error.
- On the static 3D content at the left of the title screen, FSR 3.1 is closer to native 4K than a
  bilinear upscale of the 1080p frame (RMS 1.7-2.0 against 2.7-3.0) and needs no sub-pixel
  realignment. Across the whole masked frame, bilinear scores better. That's mostly where the
  title's faint, hazy rocks moved differently in the reference run, which the mask can't catch, so
  treat the whole-frame number as a lower bound.
- No ghost trails were visible behind moving rocks.

GPU load at the game's 60 fps, 4K output, from `gpu_busy_percent` (an idle desktop was 14%):

| Mode | GPU busy |
| --- | --- |
| 1080p, no upscaler | 28% |
| `fsr` (spatial) 1080p->4K | 30% |
| `fsr3` 720p->4K | 36% |
| `fsr3` 1080p->4K | 44% |
| native 4K (`resolution_scale = 2`) | 55% |

## The Windows build under Proton (RX 9070 XT, 2026-10-30)

`TARGET=windows-d3d12` / `windows-vulkan tools/upscaler_ab.sh` runs the same A/B on the
cross-compiled Windows build (`tools/windows_cross.sh`).

- **Which upscaler runs:** with `gpu_backend = "vulkan"`, FidelityFX SDK 2.4 picks **FSR 4.1.2** on
  RDNA4 under Proton. With D3D12 (vkd3d-proton) it picks FSR 3.1.6, because FSR 4 on DX12 needs the
  AMD driver's own path. XeSS runs on both APIs (quality preset "ultra quality" for 720p->1080p).
- **D3D12 FSR 3.1:** 36.24 dB and edge error 6.46, next to native Vulkan's 36.08 dB and 6.32.
  The two backends match.
- **FSR 4.1.2:** the shipped jitter sign is the best of the four by far (+1.5 to +5.8 dB), so FSR 4
  needs no change there. Its absolute numbers swung between 36.4 dB and 42.6 dB across two runs,
  though. Under Proton, `clock_frame_locked` alone doesn't make runs repeat exactly (0 of 54 native frames
  bit-identical, against all of them on native Linux), so the reference and the upscaled run land on
  slightly different moments. Treat the Proton numbers as rough until that's fixed.
- Run Proton's own `wine` with vkd3d-proton and DXVK loaded (the scripts do this). Wine's builtin
  vkd3d can't compile the render-target dump shaders, and every resolve fails.

## FSR 4.1.2 on native Linux (Track 4)

**Current state: working.** A user-built FSR 4.1.2 Vulkan provider runs natively on Linux and
beats the FSR 3.1.4 baseline. The default build still upscales with **FSR 3.1.4**, built from
source from the SDK 1.1.4 fork (`rexglue_fidelityfx.cmake`); the Windows build uses AMD's prebuilt
FidelityFX SDK 2.4 DLLs, which run **FSR 4.1.2** where the GPU and driver support it (see the
Proton section above). FSR 4 has no public source at SDK v2.4.0 — `Kits/FidelityFX/upscalers/` is
FSR 3 only and the FSR 4 code ships solely as signed Windows DLLs under `signedbin/` — so a native
Linux FSR 4 needs extracted assets plus a runtime.

**How to use the user-built provider.** Build an fsr4-vk-style provider from your own copy of the
SDK v2.4.0 DLL (the Sega Superstars Tennis port's `linux/` tree is one such build) and place it
next to the executable, or point `fsr4_data_path` at its folder:

```bash
--fsr4=true --temporal_upscaler=fsr --fsr4_data_path=/path/to/provider
```

In **F9 → Graphics**, the **FSR 4 (Linux/AMD)** section is always listed. *Use FSR 4 and restart*
sets Present effect = `fsr3`, Upscaler = `fsr`, turns the provider on and saves the folder in one
step; the section also shows the loaded provider or the reason it is unavailable. The owner's
private game-included Linux archive places the provider next to the executable, so *Custom provider
folder* can stay empty. The equivalent command line is above.

The folder must contain the provider `.so` under either name (`amd_fidelityfx_upscaler_vk.so` or
`libamd_fidelityfx_upscaler_vk.so`) and its `fsr4-assets/` tree (1,778 SPIR-V modules and
`models/FP8_standard.bin`); `FSR4_ASSET_DIR` overrides the asset location. The device must expose
Vulkan 1.3 plus `VK_KHR_cooperative_matrix` and `VK_EXT_shader_float8` with an e4m3 16x16x16
subgroup matrix; the log names the missing feature and the presenter then falls back to FSR 3.1.4
(otherwise it falls back to spatial FSR). The F9 menu and the log report which provider is active
(`FidelityFX upscaler provider FSR 4.1.2`).

**Routes, in order:**

1. **User-built drop-in (first).** You build an fsr4-vk-style `amd_fidelityfx_upscaler_vk.so`
   yourself from your own copy of the SDK v2.4.0 DLL and place it next to the executable (or point
   `fsr4_data_path` at its folder). With `fsr4 = true` the runtime `dlopen`s it through a new Linux
   branch in `ffx_runtime` and falls back to the linked FSR 3.1.4 when it is missing or refuses to
   start.
2. **Own extractor + runtime (end state, not implemented yet).** Our own MIT extractor, pinned
   fail-closed to the v2.4.0 DLL hash below, plus our own Vulkan FSR 4 runtime behind
   `temporal_upscaler = "fsr4"`, consuming the engine's temporal inputs.

**Asset basis (binding):** only AMD FidelityFX SDK **v2.4.0** (FSR 4.1.2.4555)
`signedbin/amd_fidelityfx_upscaler_dx12.dll`, sha256
`aad4fccccb29084a655f9a4f9f613bd22c8c03fb4a0f8840148cc6543b4ccace` — the same file already pinned in
the SDK's `rexglue_fidelityfx_prebuilt.cmake`. Never fsr4-vk's default SDK 2.3.0 / 4.1.1.2740 DLL:
every hardcoded offset moved between those versions. Extraction must use the **dx12** DLL, because
the vk DLL's blobs are compressed.

**Licensing:** the `signedbin` DLLs are MIT-excepted in SDK 2.4.0's `license.md`, so assets
extracted from the v2.4.0 DLL are AMD-MIT derivatives — user-supplied, runtime-loaded, never
committed to this repository or included in a public release. The owner's private game-included
archive bundles the provider next to the executable, since that archive is never distributed.
fsr4-vk itself is **GPL-3.0-only**; none of its code is vendored into this MIT repository, and a
user-built .so stays on the user's machine. The `justfile` packaging recipes (`package-installer`,
`package-linux`) still refuse to package `amd_fidelityfx_upscaler_vk.so` /
`libamd_fidelityfx_upscaler_vk.so` / the loader `.so` (and the `.dll` names in the Linux tarball),
or any `fsr4-assets/` tree, or any fsr4-vk `build/linux`, `build/windows`, or `assets/` tree found
under `installer/staging/` or `dist/`.

**Measured on the RX 9070 XT (RADV, 2026-11-05):** the provider reports `FSR 4.1.2` and upscales
1080p -> 4K. `tools/upscaler_ab.sh` compares each upscaler with the native-4K render of the same
frame-locked frames, under a mask that drops the title's 2D logo/text and every pixel where a run's
1080p input disagrees with the unjittered native run (the moved rocks), leaving 42% of the frame.
23 title-screen frames (600-1260), 36 s rows, and a 120 s native-4K reference:

| Upscaler | Jitter | Masked PSNR | Edge RMS |
|---|---|---|---|
| FSR 4.1.2 | flip 1 (auto) | **42.46 dB** | **9.54** |
| FSR 4.1.2 | flip 0 | 40.97 | 12.92 |
| FSR 4.1.2 | flip 3 | 39.84 | 13.69 |
| FSR 4.1.2 | flip 2 | 38.97 | 15.93 |
| FSR 3.1.4 | flip 1 | 36.56 | 14.79 |
| FSR 3.1.4 | flip 0 | 36.29 | 15.83 |
| bilinear | - | 43.86 | 8.48 |

FSR 4.1.2 with flip 1 is **+6.17 +/- 0.42 dB** over FSR 3.1.4's old default (flip 0), and the best
of the four jitter signs. Bilinear still wins on this soft title screen (mostly sky and a
low-contrast slab), so the table is for choosing the jitter sign and comparing FSR 4 with FSR 3,
not as an FSR-versus-bilinear verdict.

**Jitter sign:** FSR 4.1.2 wants the opposite projection jitter from FSR 3.1 on this title: the
projection-only flip (flip 1) is best, while negating only the offset reported to the upscaler, or
both the projection and the report, measured worse (39.19 and 35.73 dB in the earlier full-frame
sweep). With `fsr4 = true` the game now sets `temporal_jitter_flip = 1` at startup when the user did
not choose a value; an explicit value still wins. The earlier full-frame comparison over frames
300-570 agreed on the ordering (FSR 4 flip 1 42.87 dB vs FSR 3.1.4 35.08 dB).

**In-engine GPU cost:** `--present_temporal_gpu_log=true` brackets the FidelityFX dispatch with a
Vulkan timestamp pair (a ring of eight, read back several dispatches later so the paint thread never
waits) and logs `temporal_gpu_ms=<ms> frame=<n> backend=<fsr3|fsr4>` for every upscaled frame. On
the RX 9070 XT at 1080p -> 4K over 720 title-screen frames: **FSR 4.1.2 p50 2.09 ms, p95 2.77 ms**
(mean 2.01, p99 3.85), versus FSR 3.1.4 p50 0.95 ms, p95 2.36 ms. The span covers only the upscale
dispatch (not the temporal input preparation), so use the median rather than the minimum.

## Live neural rendering (development)

A development build compiled with `-DREXGLUE_ENABLE_DLSS5_AMD=ON` can run the native DLSSNR-AMD
network on every guest frame instead of on a captured image:

```bash
--dlss5_amd=true --dlss5_amd_mode=3 --dlss5_amd_data_path=/path/to/the/model/parent
```

At the composition seam — the first draw that samples the finished 1000×600 3D scene, before the
HUD — the game resolves the Phase 2 motion guide, records one NR frame at 1920×1080, and gives
that answer to the composition draw in place of the guest scene texture. The HUD is drawn after
the composition, so it stays crisp and is not part of the NR image. Mode 0 turns the path off, and
modes 1/2 still substitute a precomputed `dlss5_amd_image`, which is the way to reproduce an
earlier frame exactly.

Live mode needs the motion guide, which the game now enables automatically when
`dlss5_amd_mode=3` is selected and `motion_guide` was not set explicitly. An explicit
`--motion_guide=false` is honoured, but live NR then records nothing and the log says so. The
remaining prerequisites are the same as above: the validated small model and shaders under
`dlss5_amd_data_path`, an RDNA4 GPU, and a build with the option on. The F9 game menu's Graphics
tab shows whether live NR is on, the recorded-frame count and the last frame's GPU time, or the
first missing prerequisite when it is off.

The source release never ships NVIDIA's model. The owner's private game-included Linux archive
bundles the pinned model and shaders as `dlssnr-amd/` next to the executable and sets
`dlss5_amd = true` / `dlss5_amd_mode = 3`, so live NR is on by default there; uncheck the F9
checkbox (or set `dlss5_amd = false`) to turn it off. Never redistribute that archive.

Measured on an RX 9070 XT at 1920×1080 with RADV: **4.8 ms mean GPU per guest frame** (p50 4.6 ms,
max 10.5 ms over 2384 frames), no skipped frames, with guide history consumed from the second
frame. The live identity control (the same pre-upscaled scene, no network) stays within 1–2
8-bit codes of the guest composition, and real NR output against that control measures 41–45 dB
PSNR when downscaled 8×, with a bit-identical HUD.

NR also receives the game's own scene depth resolve (`0x11ECF000`, 1000x600 `D24S8`, resampled to
the 1920x1080 evaluation extent and nearest-sampled by the network), which is what lets it pick the
nearest of the five depth taps on a silhouette instead of bleeding the background vector across it.
A frame without a usable depth resolve simply runs without it, as before; `dlss5_live_no_depth`
forces that for an A/B.

This path is development-only: it needs a hand-extracted NVIDIA model, the cvars are not in the
settings UI. Expect the mode and the dev cvars
(`dlss5_live_bypass`, `dlss5_live_no_depth`, `dlss5_live_no_substitute`, `dlss5_live_trace`) to
change.

## FSR 4 as the live pre-upscale (development, mode 4)

The same composition seam can run FSR 4 instead of the NR network. Mode 4 feeds the provider the
native 1000x600 scene colour, the engine guide motion (jitter removed, forward guest NDC) and the
guest scene depth, and substitutes its 1920x1080 answer for the scene exactly as mode 3 substitutes
the NR answer. The HUD draws after the seam and is never part of the upscale; the NR model is not
loaded.

```bash
--dlss5_amd=true --dlss5_amd_mode=4 --fsr4=true \
  --fsr4_data_path=/path/to/provider --motion_guide=true --present_effect=bilinear
```

Mode 4 needs the FSR 4 provider from the FSR 4 section above (`fsr4=true`, the `.so` and its
`fsr4-assets/`), `dlss5_amd=true` for the device features, and the motion guide. It drives the
guest projection jitter itself (the presenter's temporal upscaler normally does that), so run it
with `present_effect != fsr3`: a presenter temporal dispatch would overwrite the jitter/active
render size with the presenter's own input extent. The provider is created for renderSize
1000x600 / upscaleSize 1920x1080; a frame whose scene resolve has a different extent (or no depth)
keeps the guest view, so the seam never substitutes an input FSR did not validate.

Measured on the RX 9070 XT at 1920x1080: the provider reports `FSR 4.1.2`, the seam applies every
eligible frame with 0 skips, and the dispatch itself is **0.59 ms p50 / 0.60 ms p95** (GPU
timestamps, 2509 samples; `dlss5_live_fsr4_timing`). The identity control (`dlss5_live_bypass`)
copies the pre-upscaled scene and the dump's answer is byte-identical to its colour (same SHA-256),
which is the mode-3 control's result too. On the soft title screen the seam's FSR 4 output trails
the bilinear bypass by ~1.4 dB against a supersampled reference (39.2 vs 40.7 dB, one frame,
unmasked), the same gap the presenter's masked title A/B measured; gameplay A/B with the input
replay is still owed. This path is development-only and its cvars may change.

## Limits and next steps

- The title screen is a soft scene. Repeat the A/B in gameplay, which has hard static edges, once
  input replay can get there.
- Motion vectors from block matching can't separate moving objects from the background well, so the
  shipping upscaler still uses them. Live NR (`dlss5_amd_mode=3`) uses the engine guide, with the
  guest depth, instead: static and panning windows pass its gate, and it is still development-only
  (see `.ai/plans/` and
  `.ai/conversations/sessions/2026-11-03/225000-opencode-stable-placement.md`).
- Enable the deterministic scheduler for frame-locked benchmarks and check native repeatability
  in each scene before interpreting image-quality numbers (see the d4r results below).
- Native NGX is only verified to fall back cleanly here (no RTX card): its jitter sign and image quality
  need checking on an RTX GPU with the same A/B. The experimental Proton/d4r test below runs on AMD.
  DLSS 5 has two separate Linux paths: the NVIDIA DLSS5VKLayer on the finished picture, and the
  native DLSSNR-AMD integration (development build, RDNA4) on the pre-HUD scene. The native runtime
  has been proved on a one-shot captured frame (real engine motion/depth, 1920x1080, small model)
  and constructs in-engine; live mode 3 now runs it per guest frame at the composition seam, passes
  a 600 s gate, and draws the HUD afterwards, outside the NR image. See the README.

## d4r under Proton (RX 9070 XT)

Use d4r's **Linux/Proton** release with the Windows build of the game. The Windows d4r package
uses Windows HIP libraries and is a different installation. The Proton package supplies
OptiScaler, a matched patched vkd3d-proton, a Wine CUDA bridge, Linux HIP and ZLUDA.
See [release 0.1.5](https://github.com/countervolts/d4r/releases/tag/v0.1.5) and
[NGX routing](https://github.com/countervolts/d4r/blob/linux/docs/architecture.md).

Stage a clean copy rather than adding DLLs to your normal build. From the repository root:

```bash
stage="$PWD/out/tmp/d4r-proton/session"
mkdir -p "$stage"
curl -fL -o out/tmp/d4r-proton/d4r-linux.zip \
  https://github.com/countervolts/d4r/releases/download/v0.1.5/d4r-0.1.5-linux.zip
echo 'a9c2e455c8eca286cd11bae73061411a2668e9505365b831375331765b4733df  out/tmp/d4r-proton/d4r-linux.zip' | sha256sum -c -
cp out/build/win-amd64-release-xwin/{giantsrecompiled.exe,rexruntime.dll,rexgpu-xenos.dll,SoundTouch.dll,TracyClient.dll,libxess.dll,amd_fidelityfx_loader_dx12.dll} "$stage/"
unzip -oq out/tmp/d4r-proton/d4r-linux.zip -d "$stage"
bash "$stage/install.sh" --no-engine --yes
tools/d4r_proton.sh "$stage" 600
```

The installer verifies NVIDIA DLL hashes and caches them; `--no-engine` skips optional standalone
model generation. This tests model K through NVIDIA's library and ZLUDA. Keep frame generation and
Ray Reconstruction off. The package's default `NvngxPath=d4r\nvngx.dll` works because the runner
uses the executable folder as its working directory. It must select the shim, rather than
`d4r\ngx\_nvngx.dll`, which is NVIDIA's core loaded internally by d4r.

The runner defaults to the installed `Proton-GE Latest` (GE-Proton11-11 during this experiment);
`PROTON_DIR` selects another installation. It invokes the full Proton launcher, sets NVAPI
identity, and creates a private prefix, saves and synthetic figure directory for every run.
It uses `audio_mute=true`, `sched_deterministic=true`, matched 60 Hz guest/frame pacing with
`audio_guest_paced=true`, a headless
4K surface and 1080p guest rendering. `SDL_AUDIO_DRIVER=dummy` alone did not silence the Windows
SDL device under Proton in this test. First-time kernel compilation
can take several minutes. Artifacts are printed under `out/tmp/d4r-run-*`.

The runner clears inherited `PROTON_USE_OPTISCALER` and `PROTON_OPTISCALER_CONFIG` for the child.
Otherwise GE's built-in OptiScaler can intercept NGX before d4r, and the game can report
"DLSS 1920x1080 -> 3840x2160" while d4r never loads. A successful runner check requires repeated
`D4R_PROFILE frame=` records in `d4r.log`, the game's DLSS creation message and no FidelityFX
fallback. Inspect `out*.ppm` too: these checks establish execution, not image quality.

For a bounded Chapter 1 run with a blank synthetic Spyro, add:

```bash
tools/d4r_proton.sh "$stage" 600 --portal_bot_figure=Spyro --bot_skip_movies=true \
  "--input_script=Z:${PWD//\//\\}\\tools\\bot\\netplay-story.txt"
```

Use the input/output dumps with `tools/upscaler_ab_analyze.py` for reference comparisons.
As with the other Proton measurements, differing guest frames limit numerical quality claims.
This setup does not establish native RTX parity or DLSS 5 support.

### Observed on 2026-11-01

GE-Proton11-11, d4r 0.1.5 Linux, model K (NVIDIA DLSS 310.7.0, no standalone engine), RX 9070 XT:
the proxy reached NVIDIA's CUDA feature creation and repeated d4r evaluations at 1920x1080 ->
3840x2160. A warm title run passed 5,000 evaluations; sampled median GPU evaluation time was
about 3.5 ms. Chapter 1 gameplay was visually inspected at guest frame 14400 in
`out/tmp/d4r-run-4bhRxD96`; no FidelityFX fallback occurred. No SDK/runtime modification was needed.
The title wrapper had a shutdown error after its script was edited during execution; its captured
frames/profile data remain usable, but that wrapper exit is not a passing automated check.
The new runner rejects early exits, missing d4r samples and FSR fallback; its regression checks are
`python3 tests/d4r_runner_test.py`. The completed approximate reference comparison is recorded below.

The original test combined the default 30 fps limiter with `clock_frame_locked_hz=60` and left
`audio_guest_paced=false`. The user reported severe audio glitches. A live, game-stream-only
14-second recording found 74.7% of stereo samples repeated exactly from 1024 samples (21.33 ms)
earlier, with no silent chunks or PipeWire xruns; the second game stream was silent. This is
consistent with guest audio buffer reuse under mismatched clocks, rather than device underruns.
Direct runtime cvar inspection was denied by ptrace permissions, so the clock diagnosis is based
on launch arguments/defaults and remains to be confirmed with a corrected launch. The runner now
sets matching 60 Hz timing, guest-paced audio and explicit mute for future tests. The active game
was preserved at the user's request; its startup-only audio mode was not changed live.

Follow-up isolated audio probes kept DLSS active and routed only the probe processes to a private
null sink. Exact stereo-sample repetition at a 1024-sample lag was 64.0% with matched 60 fps/60 Hz
and host-paced audio, 9.7% with guest-paced audio, and 0% with ordinary wall-clock timing at 30 fps.
Each capture contained about 14 seconds of non-silent audio. These probes used the title scene;
the original 74.7% capture was in gameplay, so the percentages are diagnostic, not a controlled
sound-quality ranking. Matching the frame cap alone does not solve the problem; guest-paced DSP
reduces the symptom. Ordinary single-player DLSS playback should leave `clock_frame_locked=false`.
Do not disable deterministic timing for netplay. A frame-locked mixer/content investigation remains
open; the existing pitch-preserving output path does not establish correct producer buffers.

For ordinary single-player playback with audio, run the staged copy with wall-clock timing instead
of the frame-aligned test runner. This uses separate private data from the tests:

```bash
root="$PWD"
stage="$root/out/tmp/d4r-proton/session"
proton="$HOME/.steam/steam/compatibilitytools.d/Proton-GE Latest"
mkdir -p "$root/out/tmp/d4r-play/compat"
cd "$stage"
env -u PROTON_USE_OPTISCALER -u PROTON_OPTISCALER_CONFIG -u SDL_AUDIO_DRIVER \
  STEAM_COMPAT_DATA_PATH="$root/out/tmp/d4r-play/compat" \
  STEAM_COMPAT_CLIENT_INSTALL_PATH="$HOME/.steam/steam" \
  PROTON_FORCE_NVAPI=1 DXVK_NVAPI_GPU_ARCH=AD100 \
  WINEDLLOVERRIDES='dxgi=n;d3d12=n;d3d12core=n' \
  "$proton/proton" run "$stage/giantsrecompiled.exe" \
  "--game_data_root=Z:${root//\//\\}\\rom" \
  "--user_data_root=Z:${root//\//\\}\\out\\tmp\\d4r-play\\saves" \
  --portal_ipc=false --gpu_backend=d3d12 --present_effect=fsr3 --temporal_upscaler=dlss \
  --resolution=3840x2160 --resolution_scale=1 --frame_rate_limit=30 \
  --clock_frame_locked=false --audio_guest_paced=false --audio_mute=false
```

### Initial comparison without deterministic scheduling

The five 60-second runs in `out/tmp/d4r-ab` used a 60 fps cap, 60 Hz frame-locked time,
guest-paced audio and explicit mute. Compared 18 common title frames, with mean mask coverage 37.1%:

| Output | Masked PSNR | Edge RMS |
| --- | ---: | ---: |
| DLSS K through d4r | 35.051 dB | 8.912 |
| FSR 3.1.5 | 34.853 dB | 8.076 |
| Bilinear 1080p | 37.327 dB | 7.129 |

The DLSS/FSR PSNR difference was only 0.198 ± 0.312 dB (standard error). Native repeatability was
0 of 23 frames bit-identical; the sky-strip alignment check averaged 4.36 for the native reference
and 3.76 for DLSS against 0.49 for FSR. Scene timing differences limit these diagnostic numbers;
they do not establish a DLSS quality advantage. The title comparison image showed no obvious
corruption. `analysis.txt` and `comparison.png` are retained in that artifact directory.

The final tracked runner also passed a separate 60-second hardware run in
`out/tmp/d4r-run-dX2XltjY`, recording 55 d4r profile samples without FSR fallback. A 6.144-second
capture of its 48 kHz stereo output contained only zero samples, verifying explicit mute.
The original gameplay process was left open throughout the follow-up tests at the user's request.

### Deterministic reference comparison and silent gameplay

On follow-up, adding `sched_deterministic=true` made **22 of 22 native repeats bit-identical**
under Proton. Both test runners now enable it, use guest-time waits and matched 60 Hz timing,
and explicitly mute audio. The earlier non-deterministic comparison above is superseded.

Artifacts: `out/tmp/d4r-repeat`. The analyzer skips frames with no visible unmasked edges;
perfect matches have infinite PSNR, with equal perfect outputs treated as ties. Regression:
`python3 tests/upscaler_analysis_test.py`.

For steady title frames from guest frame 1800 onward (`title-only/analysis.txt`), 11 common frames
covered a mean 42.0% mask; 12 of 12 native input repeats were identical:

| Output | Masked PSNR | Edge RMS |
| --- | ---: | ---: |
| DLSS K through d4r | 39.315 dB | 9.597 |
| FSR 3.1.5 | 35.884 dB | 10.311 |
| Bilinear 1080p | 40.763 dB | 8.256 |

DLSS improved on FSR by 3.431 ± 0.506 dB (standard error), while bilinear still scored better over
this soft scene's full mask. This establishes a result for the measured title scene, not a general
gameplay quality advantage. The sky-strip check was 0.49 for the native reference and 0.35 for each
temporal input against the native 1080p run. Including the title fade gave 19 usable frames:
DLSS 40.382 dB, FSR 36.453 dB, bilinear 42.191 dB. Native 4K and upscaler images can still differ
because of rasterization and jitter even when guest frame timing repeats exactly.

At the user's request, the noisy original process was terminated and replaced with a bounded
silent session in `out/tmp/d4r-run-PDI5lhmx`. It completed ten minutes with 588 d4r profile samples,
no FSR fallback, and actual Chapter 1 Spyro gameplay inspected at frame 14280. A 6.144-second
48 kHz stereo capture was entirely zero-valued. This run had matched clocks and guest-paced audio;
the later deterministic-scheduler change was verified in the separate reference runs. A private
Pulse null sink provided a second audio safeguard. Test processes and temporary sinks were cleaned
up afterward; user saves, default audio routing and other applications were preserved.
