# Netplay determinism experiments 1-2 + sync trace
Agent: claude-code · 2026-10-31 06:31–07:16 CET · branch feat/linux-upscalers-netplay

## Done
- Experiment 1 (replays pinned to one core): invalid. One core gives ~4 fps, every frame > the
  250 ms stall threshold, so hundreds of host-timed virtual frames. Killed.
- Experiment 2: new SDK cvar `audio_guest_paced` (AudioSystem::OnGuestFrame from VdSwap releases
  48000/hz/256 buffers per frame; the SDL callback stops releasing; SDL queue capped at 16).
  Runs (e2a/e2b) still split at frame 7560 (baseline 7680). Not the cause.
- New SDK cvar `sync_trace_log=<file>`: XObject::Wait/WaitMultiple/SignalAndWait, XThread::Delay,
  XTimer::SetTimer, XSemaphore::ReleaseSemaphore append `<tid> <swap> <op> <obj...> <timeout> <res>`.
  Object names: type letter (E event, X mutant, S semaphore, T timer, H thread...) + guest address.
- Helpers (git-ignored, out/tmp/bot/helpers/): sync_cmp.py (per-thread sequence diff),
  sync_frames.py (per-frame per-thread wait multiset diff), threads.sh (per-thread CPU via /proc).
- Findings written to docs/netplay.md ("Where the divergence comes from").

## Findings
- t1/t2 traced 15-min replays split at 7560 too. Traces differ from boot (frame 0).
- Threads 7-11 poll a semaphore with 1 ms timeouts (~20/frame), 16 sleeps 1 ms, 17/33 10 ms,
  12 16 ms, 13 30 ms timeouts: all host-timed, counts per frame differ.
- Semaphores 0x3003xxxx taken ~10^5-10^6 times by threads 6 (main), 25, 27, 34: lock races.
- No guest timers used. Thread 18 is woken by the main thread every ~320 frames; same frames in
  both runs up to ~4900, different after (main thread already diverged).
- Loading stalls: none between frames 120 and 11040. Later stalls hit at the same wall-clock
  second in both parallel runs (host-side cause).
- Gameplay CPU: main guest thread ~13% of a core, GPU host thread ~10%; headless replay ~40 fps.

## State
- SDK changes are staged in thirdparty/rexglue-sdk, NOT committed (asked the user). Main repo:
  docs/netplay.md + .ai files modified, not committed.
- Build out/build/linux-amd64-release is current with these changes.

## Next
Step 1 of the scheduler: sleeps and wait timeouts in guest time under clock_frame_locked
(deadline = guest time + timeout; wake at the VdSwap / virtual frame that passes it). Verify with
sync_frames.py that threads 7-11 poll counts match per frame. Then step 2 (one guest thread at a
time, fixed handoff order) and step 3 (host events at fixed guest points).
