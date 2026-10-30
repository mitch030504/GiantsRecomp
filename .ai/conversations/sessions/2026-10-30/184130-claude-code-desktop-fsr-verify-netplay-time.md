# Session: RX 9070 XT desktop — unpack, FSR 3.1 verification, frame-locked time, determinism
Agent: claude-code (Opus 5.5)
Date: 2026-10-30 17:10–18:45 CET (user away from ~17:35, asked for shutdown at the end)

## Machine
CachyOS desktop, RX 9070 XT (RADV GFX1201, Mesa 27.0-devel), KDE Wayland on a 4K HDMI TV at 150%
scaling with HDR on. The game starts fullscreen, so output is 3840x2160. clang 23 only; no perf.

## Done
- Unpacked the handoff zip (`zip -s 0` fails with "File too large"; `7z x` works but skips 25
  relative symlinks in libmspack/moltenvk, restored with `git checkout -- .`). Memory restored.
- Built (52 s), ctest 14/14. Validation run: one pre-existing error, now fixed (SDK eea516c).
- Red tint: not reproducible on RADV either (guest output after gamma is correct). The user says it
  was the original maintainer's report.
- FSR 3.1 verified, and the x jitter sign flipped (main 3fedb34); see docs/upscaling.md and
  decisions/2026-10-30-frame-locked-time-and-jitter-sign.md.
- SDK: perf_log_csv opened on the first frame (360fc95; perf counters are non-Release only).
- SDK: clock_frame_locked (36b3e1c), with virtual frames during stalls and the underflow fixed.
- Determinism rerun: memory still diverges from boot; docs/netplay.md lists the options (main f7dff47).
- Title-screen profile: 0.2 CPU cores, 28% GPU at 60 fps at 4K — nothing to fix there.

## Gotchas found
- The Bash tool's shell is zsh: an unquoted `$VAR` holding two flags is passed as ONE argument
  (that made video_mode_height silently fall back and the guest render 640x480).
- When the TV turns off, Wayland loses its output and SDL fails ("did not add any displays").
  Run GPU tests in `gamescope --backend headless -W 3840 -H 2160 -- env -u WAYLAND_DISPLAY ...`.
- Guest frames aren't comparable across runs unless `--clock_frame_locked=true` is set.

## Not done / next
- Netplay: the user picks host streaming, deterministic scheduling, or a gameplay-state check first.
- A gameplay input recording (`--input_record`) to rerun the FSR A/B and profile a real scene.
- Hands-on Wayland checks: mouse look in relative mode, F4/F6 overlays at 150%, minimized window.
- D3D12 temporal parity, FSR SDK 2.4 / FSR 4, DLSS (NGX), XeSS: not started (needs a Windows build
  or a new clang-cl + ~/opt/xwin toolchain file; the upscaler interface comes first).
- Nothing pushed. Push the SDK (giants/linux-vulkan) first, then main, when the user says so.
