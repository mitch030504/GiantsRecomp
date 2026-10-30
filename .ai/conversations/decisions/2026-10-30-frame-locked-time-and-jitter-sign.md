# Decision: Frame-locked guest time; temporal jitter signs measured against native 4K
Date: 2026-10-30
Agent: claude-code
Status: accepted

## Context
The FSR 3.1 jitter sign had never been verified, and the netplay plan needed guest time
virtualized. Runs weren't comparable frame by frame: guest time followed the host clock, so the
same guest frame showed a different animation state in every run.

## Decision
1. SDK `clock_frame_locked`: guest time = one frame period per VdSwap (stands still in between).
   The first frame is fixed at 10 s and the guest date at 2013-01-01. After 250 ms without a frame,
   time advances in whole virtual frames (the game polls guest time during loads; freezing it
   made a ~1 s boot wait take until the safety valve).
2. Jitter signs are chosen by measurement (`tools/upscaler_ab.sh`): frame-locked headless runs, with
   each FSR output compared against a native-4K render of the same frame. The window now moves by
   (+jx, +jy). The old -jx cost 1.0 dB / +6% edge error; negating y cost 1.2 dB / +18%.

## Rationale
A reference comparison is the only metric that can't be fooled. "Sharpness" alone favoured the
wrong y sign, because a misaligned history leaves aliasing that FSR doesn't resolve.

## Alternatives Considered
- Derive the sign from FFX docs: the hook already matched the documented convention, yet x measured
  wrong. Something in the guest's viewport mapping flips x; not investigated further.
- Freeze time completely between frames: hung boot waits (see 1).

## Consequences
- Rendering is repeatable across runs (camera, sky, logo identical per frame); the title's rock
  particles and guest memory are not. Netplay is blocked on that (docs/netplay.md).
- Bug found and fixed on the way: a host tick sampled before taking the clock lock could predate the
  last frame, underflow, and push guest time forward by years.
- The title screen is a soft scene. Rerun the A/B in gameplay when an input recording exists.
