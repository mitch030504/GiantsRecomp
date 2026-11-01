# Pitch-preserving guest audio implementation plan
Agent: Codex (/root), 2026-11-01. User approved preserving pitch and authorized autonomous fixing.

Goal: replace repeated silence gaps during slower-than-real-time deterministic rendering with
continuous pitch-preserving audio. Keep guest callbacks, scheduling and netplay inputs unchanged.

Use pinned SoundTouch2.4.1 as a replaceable shared output DSP library. A small SDL-side processor
adapts tempo to measured input/output flow and bounded output backlog. Buffer briefly at startup;
keep queues bounded, reset after prolonged starvation, and retain silence when no sound exists.
Process stereo/surround after the existing mix. Ordinary device-paced audio bypasses stretching.

1. Add regression simulations under SDK tests/unit/audio/guest_audio_stretch_test.cpp. Show
   current FIFO playback underflows at half speed. Cover variable rate/bursts, starvation recovery,
   bounded memory, pitch and channel isolation. Compile standalone initially for quick red/green.
2. Implement src/audio/sdl/guest_audio_stretch.h/.cpp using installed SoundTouch for experiments.
   Add pinned fetch/build in cmake/rexglue_soundtouch.cmake, linked from rexaudio/unit tests.
3. Integrate only SDL output callback; increase its bounded raw queue enough for DSP buffering.
   Do not release guest semaphores from the stretched path. Build Linux, replay prior slow-cutscene
   workload with the same output probe and compare shortages. Fix failures before proceeding.
4. Verify native unit checks, Windows cross-build/Proton tests and paired game input/frame agreement.
   Stage the shared library, license and unmodified source archive in SDK/install/app packages.
5. Update docs/netplay.md, plan, paths, decision and handoff with measured results and limitations;
   commit SDK first then app gitlink. No pushes/deployments.

Primary reference: official SoundTouch README (surina.net/soundtouch/README.html), bundled2.4.1
headers/source. SDL frequency-ratio alone changes pitch, so it does not satisfy the user's choice.
