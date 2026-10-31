# Decision: Deterministic guest scheduling in three steps, verified with a sync trace
Date: 2026-10-31
Agent: claude-code
Status: accepted (step 1 done, steps 2-3 proposed)

## Context
Lockstep netplay needs two runs fed the same inputs to stay identical. With frame-locked time and
guest-paced audio they still split ~10 s into play. The new SDK `sync_trace_log` and windowed
`state_hash_log` showed why:
- sleeps and wait timeouts ran on host time, so poll loops ran a different number of times per
  guest frame (fixed by step 1);
- ~16 guest threads share a few hot semaphores, so the order in which they win them follows the
  host scheduler;
- the first gameplay memory that diverges (read 7050) is a pointer to the same allocation at a
  different address: two threads allocated from the game's own heap in a different order.

## Decision
1. **Guest-time timeouts** (`clock_frame_locked_waits`, SDK aaa81e0, on by default with
   clock_frame_locked): a timeout is a deadline in guest time; the wait continues in 1 ms host
   slices until signalled or past the deadline. Done; pollers match per frame.
2. **One guest thread at a time, in a fixed order** (next): a global run token. A thread holds it
   while running guest code and gives it up only at blocking kernel calls (waits, sleeps,
   NtYieldExecution, VdSwap). The next holder is the first ready thread in thread-id order after
   the current one, where "ready" is decided by trying its wait without blocking (which consumes
   the signal on its behalf). Host threads that run guest code (audio worker callback, GPU
   interrupt callbacks, APC/DPC delivery) take the token too. Guest spin loops without kernel calls
   would deadlock under a token: codegen emits db16cyc/cctpl/cctpm (spin hints) as no-ops today;
   they become yield points. Behind a cvar, only meaningful with clock_frame_locked.
3. **Host events at fixed guest points**: GPU interrupts (vblank from a host 60 Hz timer,
   command-stream INTERRUPT packets), I/O completion and XNotify are queued and delivered at
   VdSwap (or another guest-defined point) instead of when the host produces them.

Each step is checked with two replays of out/tmp/bot/play.inputlog: `sync_frames.py` (per-thread
waits per frame), `hash_onset.py` (first memory bucket that stops matching) and `cmp.py`
(picture split).

## Rationale
Lockstep and rollback both need a deterministic game; the game has no netcode, so there is no
cheaper route (option 3 ruled itself out, streaming is lag-bound). Building the steps in order
keeps each change small and measurable, and each one helps replays and debugging on its own.

## Alternatives Considered
- Single core per process (taskset): ~4 fps, frames exceed the stall threshold and host time
  leaks in. Doesn't remove preemption order either.
- Find and patch the racing threads one by one (e.g. the heap allocator): the game's allocator is
  its own code and is called from several threads; a per-site fix wouldn't cover the next race.
- Host-authoritative streaming (netplay option 1): always in sync but latency-bound, against the
  user's goal of lag-insensitive play.

## Consequences
- Step 2 serialises guest code: fine for this game (main guest thread ~13% of a core during play,
  workers ~1-2%), but it costs any parallel speedup and changes timing for host-fed data.
- Deadlock risks: spin loops on memory without kernel calls or hint instructions; guest code
  waiting on host work while holding the token (must give it up in those waits).
- Boot (before the first frame) stays host-timed; netplay must start both peers from the same
  point (e.g. after boot, or by loading the same state).
