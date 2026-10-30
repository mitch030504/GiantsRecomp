# Temporal upscaling (FSR 3.1)

`present_effect = "fsr3"` runs AMD FidelityFX Super Resolution 3.1 when the guest output is smaller
than the window, for example `video_mode_width/height = 1920x1080` (or 1280x720) in a 4K window.
It's Vulkan-only for now; D3D12 still has the older spatial path.

## How it works

The upscaler runs at present time, on the finished guest frame:

- **Jitter:** while it's active, `BuildPerspectiveProjectionMatrix` (`src/hooks/ultrawide_hook.cpp`)
  shifts every 3D projection by a Halton(2,3) sub-pixel offset keyed to the guest frame index
  (`rex/ui/temporal_jitter.h`), and the presenter tells FSR the same offset.
- **Motion vectors:** estimated by block matching between this frame and the last one
  (`temporal_motion_cs`), since the game doesn't provide any.
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

## Limits and next steps

- The title screen is a soft scene. Repeat the A/B in gameplay, which has hard static edges, once
  input replay can get there.
- Motion vectors from block matching can't separate moving objects from the background well; the
  plan is camera and per-object motion vectors from the game (see `.ai/plans/`).
- D3D12 doesn't have this path yet.
